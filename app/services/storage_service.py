from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
import json
import os
import re
import sqlite3
import shutil
import tempfile
from typing import BinaryIO
from uuid import uuid4

from app.core.config import EXTERNAL_STORAGE_ROOT, settings


TASK_SUBDIRECTORIES = ("source", "audio", "transcripts", "analysis", "clips", "05_clips", "06_subtitled", "07_covers", "logs")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm", ".m4v", ".ts"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".aac", ".flac", ".ogg", ".wma", ".m4a"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
TRASH_DIR_NAME = "_回收站"
DELETE_STAGING_DIR_NAME = ".niuma-delete-staging"
_WINDOWS_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

# 路径遍历攻击的标记。普通文件名中的 ``~`` 不会被 pathlib 自动展开，
# 因此只拒绝真正会改变父目录的 ``..``。
_PATH_TRAVERSAL_MARKERS = ("..",)


class StorageSafetyError(RuntimeError):
    """存储路径不安全或不满足清理条件。"""


@dataclass(frozen=True)
class ManagedMediaTarget:
    label: str
    path: Path


@dataclass(frozen=True)
class TaskMediaCleanupPlan:
    task_id: str
    targets: tuple[ManagedMediaTarget, ...]
    external_source_path: Path | None

    @property
    def existing_targets(self) -> tuple[ManagedMediaTarget, ...]:
        return tuple(target for target in self.targets if target.path.exists())


@dataclass(frozen=True)
class TaskMediaCleanupResult:
    deleted_paths: tuple[str, ...]
    freed_bytes: int
    external_source_preserved: bool
    cleanup_pending: bool = False
    staged_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class StagedMediaTarget:
    label: str
    original_path: Path
    staged_path: Path
    size_bytes: int


@dataclass(frozen=True)
class StagedTaskMediaCleanup:
    task_id: str
    stage_id: str
    targets: tuple[StagedMediaTarget, ...]
    manifest_roots: tuple[Path, ...]
    external_source_preserved: bool

    @property
    def freed_bytes(self) -> int:
        return sum(target.size_bytes for target in self.targets)


def _ensure_writable_directory(path: Path, label: str) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe_path = path / f".niuma-write-test-{uuid4().hex}"
        probe_path.write_bytes(b"ok")
        probe_path.unlink()
    except OSError as exc:
        raise RuntimeError(f"{label}不可用或不可写：{path}；原因：{exc}") from exc
    return path.resolve()


def configure_runtime_media_storage() -> dict[str, str]:
    """准备大文件目录，并把当前应用进程的临时目录固定到存储盘。"""
    tasks_dir = _ensure_writable_directory(settings.tasks_dir, "任务存储目录")
    upload_temp_dir = _ensure_writable_directory(settings.upload_temp_dir, "上传临时目录")
    export_dir = _ensure_writable_directory(settings.publish_scheduler_export_dir, "发布包目录")

    temp_value = str(upload_temp_dir)
    os.environ["TEMP"] = temp_value
    os.environ["TMP"] = temp_value
    tempfile.tempdir = temp_value
    return {
        "tasks_dir": str(tasks_dir),
        "upload_temp_dir": temp_value,
        "publish_export_dir": str(export_dir),
    }


def _collect_allowed_roots() -> list[Path]:
    """收集所有允许访问的文件系统根目录。"""
    roots: list[Path] = []
    # 始终包含 STORAGE_ROOT 和 TASKS_DIR
    for root in (settings.storage_root, settings.tasks_dir):
        try:
            resolved = root.resolve(strict=False)
            if resolved not in roots:
                roots.append(resolved)
        except (OSError, ValueError):
            pass
    # 额外配置的 ALLOWED_MEDIA_ROOTS
    extra_roots = settings.allowed_media_roots
    if extra_roots:
        for part in extra_roots.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                resolved = Path(part).expanduser().resolve(strict=False)
                if resolved not in roots:
                    roots.append(resolved)
            except (OSError, ValueError):
                pass
    return roots


def _is_path_within_roots(path: Path, roots: list[Path] | None = None) -> bool:
    """检查路径是否在允许的根目录范围内，阻止 .. 和符号链接逃逸。"""
    if roots is None:
        roots = _collect_allowed_roots()
    if not roots:
        return False

    # 拒绝包含 .. 的路径
    path_str = str(path).replace("\\", "/")
    if ".." in Path(path_str).parts:
        return False

    try:
        resolved = path.resolve(strict=False)
    except (OSError, ValueError):
        return False

    for root in roots:
        try:
            root_resolved = root.resolve(strict=False)
            resolved_str = str(resolved).replace("\\", "/").lower()
            root_str = str(root_resolved).replace("\\", "/").lower()
            # 精确匹配或以 root/ 开头
            if resolved_str == root_str or resolved_str.startswith(root_str + "/"):
                return True
        except (OSError, ValueError):
            continue
    return False


def _validate_upload_extension(filename: str) -> str:
    """校验上传文件扩展名，返回小写扩展名；不合法则抛出 ValueError。"""
    allowed_raw = settings.allowed_upload_extensions
    allowed = {
        ext.strip().lower()
        for ext in allowed_raw.split(",")
        if ext.strip()
    }
    if not allowed:
        allowed = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

    suffix = Path(filename).suffix.lower()
    if not suffix:
        raise ValueError(f"上传文件没有扩展名，允许的格式：{', '.join(sorted(allowed))}")
    if suffix not in allowed:
        raise ValueError(f"不支持的文件格式 {suffix}，允许的格式：{', '.join(sorted(allowed))}")
    return suffix


def ensure_storage_root() -> Path:
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    return settings.storage_root


def ensure_tasks_root() -> Path:
    settings.tasks_dir.mkdir(parents=True, exist_ok=True)
    return settings.tasks_dir


def _storage_relative_parts(task_dir_name: str, label: str = "任务目录名") -> tuple[str, ...]:
    windows_path = PureWindowsPath(str(task_dir_name or "").strip())
    parts = tuple(part for part in windows_path.parts if part not in {"", "."})
    if (
        not parts
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or any(part in _PATH_TRAVERSAL_MARKERS for part in parts)
    ):
        raise StorageSafetyError(f"{label}包含不安全路径：{task_dir_name}")
    return parts


def _storage_path_from_dir_name(task_dir_name: str) -> Path:
    parts = _storage_relative_parts(task_dir_name)
    return _safe_managed_child(settings.tasks_dir, parts, "任务目录")


def sanitize_task_dir_name(task_name: str | None, fallback: str = "untitled") -> str:
    raw_name = (task_name or "").strip() or fallback
    sanitized = _WINDOWS_FORBIDDEN_CHARS.sub("_", raw_name)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" .")
    if not sanitized:
        sanitized = fallback
    if sanitized.upper() in _WINDOWS_RESERVED_NAMES:
        sanitized = f"{sanitized}_"
    return sanitized[:120].strip(" .") or fallback


def _get_existing_task_dir_names(include_deleted: bool = True, exclude_task_id: str | None = None) -> set[str]:
    if not settings.database_path.exists():
        return set()
    try:
        with sqlite3.connect(settings.database_path) as connection:
            connection.row_factory = sqlite3.Row
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
            if "task_dir_name" not in columns:
                return set()
            where_parts = ["task_dir_name IS NOT NULL", "task_dir_name != ''"]
            params: list[str] = []
            if not include_deleted:
                where_parts.append("COALESCE(is_deleted, 0) = 0")
            if exclude_task_id:
                where_parts.append("id != ?")
                params.append(exclude_task_id)
            rows = connection.execute(
                f"SELECT task_dir_name FROM tasks WHERE {' AND '.join(where_parts)}",
                params,
            ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"读取已有任务目录失败：{exc}") from exc
    return {str(row["task_dir_name"]).lower() for row in rows}


def allocate_task_dir_name(
    task_name: str | None,
    parent_dir_name: str | None = None,
    exclude_task_id: str | None = None,
    *,
    reserve: bool = True,
) -> str:
    base_name = sanitize_task_dir_name(task_name, fallback=exclude_task_id or "untitled")
    parent_parts = _storage_relative_parts(parent_dir_name, "父目录名") if parent_dir_name else ()
    existing_names = _get_existing_task_dir_names(exclude_task_id=exclude_task_id)
    tasks_root = ensure_tasks_root()
    root = _safe_managed_child(tasks_root, parent_parts, "父目录") if parent_parts else tasks_root
    root.mkdir(parents=True, exist_ok=True)

    for index in range(1, 1000):
        candidate_name = base_name if index == 1 else f"{base_name} ({index})"
        candidate_parts = (*parent_parts, candidate_name)
        relative_name = str(PureWindowsPath(*candidate_parts))
        if relative_name.lower() in existing_names:
            continue
        candidate_path = _safe_managed_child(tasks_root, candidate_parts, "任务目录")
        if reserve:
            try:
                candidate_path.mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                continue
            return relative_name
        if not candidate_path.exists():
            return relative_name

    for _attempt in range(100):
        relative_name = str(PureWindowsPath(*parent_parts, f"{base_name}-{uuid4().hex[:8]}"))
        candidate_path = _safe_managed_child(
            tasks_root,
            _storage_relative_parts(relative_name),
            "任务目录",
        )
        if reserve:
            try:
                candidate_path.mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                continue
        elif candidate_path.exists():
            continue
        return relative_name
    raise StorageSafetyError("无法分配唯一任务目录，请稍后重试")


def _fetch_task_dir_name(task_id: str) -> str | None:
    if not settings.database_path.exists():
        return None
    try:
        with sqlite3.connect(settings.database_path) as connection:
            connection.row_factory = sqlite3.Row
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
            if "task_dir_name" not in columns:
                return None
            row = connection.execute("SELECT task_dir_name FROM tasks WHERE id = ?", (task_id,)).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(f"读取任务目录映射失败：{exc}") from exc
    if not row:
        return None
    return row["task_dir_name"] or None


def resolve_task_dir_name(task_id: str, task_dir_name: str | None = None) -> str:
    return task_dir_name or _fetch_task_dir_name(task_id) or task_id


def create_task_directory(task_id: str | None = None, task_dir_name: str | None = None) -> Path:
    resolved_task_id = task_id or uuid4().hex[:12]
    resolved_dir_name = task_dir_name or resolved_task_id
    task_dir = _storage_path_from_dir_name(resolved_dir_name)
    task_dir.mkdir(parents=True, exist_ok=True)
    for directory in get_expected_subdirectories(resolved_task_id, resolved_dir_name).values():
        directory.mkdir(parents=True, exist_ok=True)
    return task_dir


def get_task_directory(task_id: str, task_dir_name: str | None = None) -> Path:
    return _storage_path_from_dir_name(resolve_task_dir_name(task_id, task_dir_name))


def get_expected_subdirectories(task_id: str, task_dir_name: str | None = None) -> dict[str, Path]:
    task_dir = get_task_directory(task_id, task_dir_name)
    return {name: task_dir / name for name in TASK_SUBDIRECTORIES}


def get_artifact_paths(task_id: str, task_dir_name: str | None = None) -> dict[str, Path]:
    directories = get_expected_subdirectories(task_id, task_dir_name)
    return {
        "task_dir": get_task_directory(task_id, task_dir_name),
        "audio_path": directories["audio"] / "source.wav",
        "transcript_path": directories["transcripts"] / "transcript.md",
        "analysis_path": directories["analysis"] / "candidate_clips.json",
        "clips_dir": directories["05_clips"],
        "subtitled_dir": directories["06_subtitled"],
        "covers_dir": directories["07_covers"],
        "log_path": directories["logs"] / "process.log",
    }


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def resolve_video_file_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None

    path = Path(path_value)
    if path.exists():
        return path

    container_tasks_root = "/workspace/tasks/"
    normalized_posix = path_value.replace("\\", "/")
    if normalized_posix.startswith(container_tasks_root):
        relative_value = normalized_posix[len(container_tasks_root) :]
        first_part, separator, rest = relative_value.partition("/")
        task_dir_name = _fetch_task_dir_name(first_part)
        if task_dir_name and task_dir_name != first_part:
            task_dir_posix = task_dir_name.replace("\\", "/")
            relative_value = f"{task_dir_posix}{separator}{rest}" if rest else task_dir_name
        return _safe_managed_child(
            settings.tasks_dir,
            _storage_relative_parts(relative_value, "容器媒体路径"),
            "容器媒体路径",
        )

    windows_storage_root = str(settings.storage_root)
    raw_value = path_value.replace("/", "\\")
    known_windows_root = str(EXTERNAL_STORAGE_ROOT)
    if raw_value.lower().startswith(known_windows_root.lower() + "\\"):
        relative_value = raw_value[len(known_windows_root) + 1 :]
        first_part = PureWindowsPath(relative_value).parts[0] if PureWindowsPath(relative_value).parts else ""
        task_dir_name = _fetch_task_dir_name(first_part)
        if task_dir_name and task_dir_name != first_part:
            rest_parts = PureWindowsPath(relative_value).parts[1:]
            relative_value = str(PureWindowsPath(task_dir_name, *rest_parts))
        return _safe_managed_child(
            settings.storage_root,
            _storage_relative_parts(relative_value, "旧版媒体路径"),
            "旧版媒体路径",
        )

    if windows_storage_root:
        normalized_root = windows_storage_root.replace("/", "\\")
        if raw_value.lower().startswith(normalized_root.lower() + "\\"):
            relative_value = raw_value[len(normalized_root) + 1 :]
            first_part = PureWindowsPath(relative_value).parts[0] if PureWindowsPath(relative_value).parts else ""
            task_dir_name = _fetch_task_dir_name(first_part)
            if task_dir_name and task_dir_name != first_part:
                rest_parts = PureWindowsPath(relative_value).parts[1:]
                relative_value = str(PureWindowsPath(task_dir_name, *rest_parts))
            return _safe_managed_child(
                settings.storage_root,
                _storage_relative_parts(relative_value, "媒体路径"),
                "媒体路径",
            )

    return path


def resolve_task_media_file_path(
    path_value: str | None,
    *,
    task_id: str,
    task_dir_name: str | None,
    allowed_subdirectories: tuple[str, ...],
    allowed_extensions: set[str] | frozenset[str] = VIDEO_EXTENSIONS,
) -> Path | None:
    """解析任务产物，并确认它只能落在当前任务的受控子目录内。"""
    if not path_value or not allowed_subdirectories:
        return None
    try:
        resolved_path = resolve_video_file_path(path_value)
        if resolved_path is None or resolved_path.suffix.lower() not in allowed_extensions:
            return None
        task_root = get_task_directory(task_id, task_dir_name)
        roots = [task_root]

        legacy_root = settings.project_root / "tasks"
        task_id_parts = _safe_relative_parts(task_id, "任务 ID")
        if len(task_id_parts) == 1:
            roots.append(_safe_managed_child(legacy_root, task_id_parts, "旧版任务目录"))
        dir_parts = _safe_relative_parts(task_dir_name or task_id, "任务目录名")
        if len(dir_parts) == 1 and (task_dir_name or task_id).lower() != task_id.lower():
            roots.append(_safe_managed_child(legacy_root, dir_parts, "旧版任务目录"))
    except (OSError, ValueError, StorageSafetyError):
        return None

    for root in roots:
        for subdirectory in allowed_subdirectories:
            allowed_root = root / subdirectory
            if resolved_path.resolve(strict=False) == allowed_root.resolve(strict=False):
                continue
            if _path_is_within(resolved_path, allowed_root):
                return resolved_path
    return None


def validate_source_video_path(path_value: str | None) -> tuple[bool, str]:
    if not path_value:
        return False, "尚未选择视频文件"
    try:
        path = resolve_video_file_path(path_value)
    except StorageSafetyError:
        return False, "视频路径包含不安全的路径跳转字符"
    if path is None:
        return False, "尚未选择视频文件"

    # 路径遍历检查
    path_str = str(path).replace("\\", "/")
    if ".." in Path(path_str).parts:
        return False, "视频路径包含不安全的路径跳转字符"

    if not path.exists():
        return False, "视频文件不存在"
    if not path.is_file():
        return False, "选择的路径不是文件"
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return False, "请选择常见视频文件格式"

    # 必须在允许的根目录下
    if not _is_path_within_roots(path):
        return False, "视频文件不在允许的存储目录范围内"

    return True, ""


def get_source_video_path(task: dict) -> Path | None:
    return resolve_video_file_path(task.get("original_video_path"))


def save_uploaded_video(task_id: str, filename: str, file_object: BinaryIO, task_dir_name: str | None = None) -> Path:
    # 扩展名校验
    _validate_upload_extension(filename)
    create_task_directory(task_id, task_dir_name)

    safe_name = Path(filename or "source_video").name
    if not Path(safe_name).suffix:
        safe_name = f"{safe_name}.mp4"
    output_path = get_expected_subdirectories(task_id, task_dir_name)["source"] / safe_name

    # 流式写入 + 大小限制检查
    max_size = settings.max_upload_size_bytes
    written = 0
    try:
        with output_path.open("wb") as target:
            while True:
                chunk = file_object.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                written += len(chunk)
                if written > max_size:
                    max_gb = max_size / (1024 * 1024 * 1024)
                    raise ValueError(f"上传文件超过大小限制（{max_gb:.1f} GB）")
                target.write(chunk)
    except Exception:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return output_path


def remove_failed_task_directory(task_id: str, task_dir_name: str) -> None:
    """仅清理本次尚未写入数据库的新任务目录。"""
    if _fetch_task_dir_name(task_id):
        return
    task_dir = get_task_directory(task_id, task_dir_name)
    tasks_root = settings.tasks_dir.resolve()
    resolved = task_dir.resolve(strict=False)
    try:
        within_root = resolved.is_relative_to(tasks_root)
    except AttributeError:  # pragma: no cover - Python 3.8 兼容
        within_root = str(resolved).lower().startswith(str(tasks_root).lower() + os.sep)
    if resolved == tasks_root or not within_root or task_dir.is_symlink():
        raise StorageSafetyError(f"拒绝清理不安全的任务目录：{task_dir}")
    if task_dir.exists():
        shutil.rmtree(task_dir)


def _safe_relative_parts(value: str, label: str) -> tuple[str, ...]:
    return _storage_relative_parts(value, label)


def _safe_managed_child(root: Path, parts: tuple[str, ...], label: str) -> Path:
    resolved_root = root.resolve(strict=False)
    candidate = root.joinpath(*parts)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        within_root = resolved_candidate.is_relative_to(resolved_root)
    except AttributeError:  # pragma: no cover - Python 3.8 兼容
        within_root = str(resolved_candidate).lower().startswith(str(resolved_root).lower() + os.sep)
    if resolved_candidate == resolved_root or not within_root or candidate.is_symlink():
        raise StorageSafetyError(f"拒绝访问不安全的{label}：{candidate}")
    return candidate


def _deduplicate_targets(targets: list[ManagedMediaTarget]) -> tuple[ManagedMediaTarget, ...]:
    unique: list[ManagedMediaTarget] = []
    seen: set[str] = set()
    for target in targets:
        key = str(target.path.resolve(strict=False)).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(target)
    return tuple(unique)


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        return path.resolve(strict=False).is_relative_to(parent.resolve(strict=False))
    except (AttributeError, OSError, ValueError):
        path_value = str(path.resolve(strict=False)).lower()
        parent_value = str(parent.resolve(strict=False)).lower()
        return path_value.startswith(parent_value + os.sep)


def build_task_media_cleanup_plan(task: dict, *, include_legacy: bool = True) -> TaskMediaCleanupPlan:
    task_id = str(task.get("id") or "").strip()
    task_id_parts = _safe_relative_parts(task_id, "任务 ID")
    if len(task_id_parts) != 1:
        raise StorageSafetyError(f"任务 ID 必须是单层目录名：{task_id}")

    task_dir_name = str(task.get("task_dir_name") or task_id)
    task_parts = _safe_relative_parts(task_dir_name, "任务目录名")
    task_dir = _safe_managed_child(settings.tasks_dir, task_parts, "任务目录")
    targets = [ManagedMediaTarget("E 盘任务目录", task_dir)]

    export_dir = _safe_managed_child(
        settings.publish_scheduler_export_dir,
        task_id_parts,
        "发布包目录",
    )
    targets.append(ManagedMediaTarget("E 盘发布包目录", export_dir))

    if include_legacy:
        legacy_root = settings.project_root / "tasks"
        legacy_values = [task_id]
        if len(task_parts) == 1 and task_dir_name.lower() != task_id.lower():
            legacy_values.append(task_dir_name)
        for legacy_value in legacy_values:
            legacy_parts = _safe_relative_parts(legacy_value, "旧版任务目录名")
            legacy_path = _safe_managed_child(legacy_root, legacy_parts, "旧版 C 盘任务目录")
            targets.append(ManagedMediaTarget("旧版 C 盘任务目录", legacy_path))

    managed_targets = _deduplicate_targets(targets)
    source_path = get_source_video_path(task)
    external_source_path = None
    if source_path and source_path.exists():
        if not any(_path_is_within(source_path, target.path) for target in managed_targets):
            external_source_path = source_path

    return TaskMediaCleanupPlan(
        task_id=task_id,
        targets=managed_targets,
        external_source_path=external_source_path,
    )


def _directory_size_bytes(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def task_media_cleanup_plan_size(plan: TaskMediaCleanupPlan) -> int:
    return sum(
        _directory_size_bytes(target.path)
        for target in plan.existing_targets
        if target.path.is_dir() and not target.path.is_symlink()
    )


def _cleanup_manifest_payload(
    staged: StagedTaskMediaCleanup,
    *,
    status: str,
    moved_paths: tuple[str, ...] = (),
) -> dict:
    return {
        "version": 1,
        "task_id": staged.task_id,
        "stage_id": staged.stage_id,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "moved_paths": list(moved_paths),
        "targets": [
            {
                "label": target.label,
                "original_path": str(target.original_path),
                "staged_path": str(target.staged_path),
                "size_bytes": target.size_bytes,
            }
            for target in staged.targets
        ],
    }


def _write_cleanup_manifests(
    staged: StagedTaskMediaCleanup,
    *,
    status: str,
    moved_paths: tuple[str, ...] = (),
) -> None:
    payload = _cleanup_manifest_payload(staged, status=status, moved_paths=moved_paths)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    for root in staged.manifest_roots:
        root.mkdir(parents=True, exist_ok=True)
        manifest_path = root / "manifest.json"
        temporary_path = root / f"manifest.json.tmp-{uuid4().hex}"
        temporary_path.write_text(serialized, encoding="utf-8")
        os.replace(temporary_path, manifest_path)


def _cleanup_stage_roots(staged: StagedTaskMediaCleanup) -> tuple[str, ...]:
    pending: list[str] = []
    for root in staged.manifest_roots:
        if not root.exists():
            continue
        try:
            shutil.rmtree(root)
        except OSError:
            pending.append(str(root))
    return tuple(pending)


def stage_task_media_cleanup_plan(plan: TaskMediaCleanupPlan) -> StagedTaskMediaCleanup:
    """把托管媒体原子移动到同卷隔离区，尚不执行永久删除。"""
    stage_id = f"{plan.task_id}-{uuid4().hex}"
    staged_targets: list[StagedMediaTarget] = []
    manifest_roots: list[Path] = []
    for index, target in enumerate(plan.targets):
        path = target.path
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_dir():
            raise StorageSafetyError(f"拒绝暂存异常的{target.label}：{path}")

        stage_root = _safe_managed_child(
            path.parent,
            (DELETE_STAGING_DIR_NAME, stage_id),
            f"{target.label}删除隔离区",
        )
        staged_path = _safe_managed_child(
            stage_root,
            (f"{index:02d}-{path.name}",),
            f"{target.label}删除暂存目录",
        )
        staged_targets.append(
            StagedMediaTarget(
                label=target.label,
                original_path=path,
                staged_path=staged_path,
                size_bytes=_directory_size_bytes(path),
            )
        )
        if stage_root not in manifest_roots:
            manifest_roots.append(stage_root)

    staged = StagedTaskMediaCleanup(
        task_id=plan.task_id,
        stage_id=stage_id,
        targets=tuple(staged_targets),
        manifest_roots=tuple(manifest_roots),
        external_source_preserved=plan.external_source_path is not None,
    )
    if not staged.targets:
        return staged

    _write_cleanup_manifests(staged, status="prepared")
    moved_paths: list[str] = []
    try:
        for target in staged.targets:
            target.staged_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target.original_path), str(target.staged_path))
            if target.original_path.exists() or not target.staged_path.exists():
                raise RuntimeError(f"暂存{target.label}后路径状态异常：{target.original_path}")
            moved_paths.append(str(target.original_path))
            _write_cleanup_manifests(
                staged,
                status="staging",
                moved_paths=tuple(moved_paths),
            )
        _write_cleanup_manifests(
            staged,
            status="staged",
            moved_paths=tuple(moved_paths),
        )
        return staged
    except Exception as exc:
        try:
            rollback_staged_task_media_cleanup(staged)
        except Exception as rollback_exc:
            raise RuntimeError(
                f"暂存任务媒体失败且自动恢复未完成：{exc}；恢复错误：{rollback_exc}"
            ) from exc
        raise RuntimeError(f"暂存任务媒体失败，已恢复原目录：{exc}") from exc


def rollback_staged_task_media_cleanup(staged: StagedTaskMediaCleanup) -> None:
    """数据库提交前失败时，把已经暂存的目录恢复到原路径。"""
    if not staged.targets:
        return
    try:
        _write_cleanup_manifests(staged, status="rolling_back")
    except OSError:
        pass

    errors: list[str] = []
    for target in reversed(staged.targets):
        source = target.staged_path
        destination = target.original_path
        if source.exists():
            if destination.exists():
                errors.append(f"原路径已被占用：{destination}")
                continue
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
            except OSError as exc:
                errors.append(f"恢复{target.label}失败：{exc}")
        elif not destination.exists():
            errors.append(f"原路径与暂存路径都不存在：{destination}")

    if errors:
        try:
            _write_cleanup_manifests(staged, status="recovery_required")
        except OSError:
            pass
        raise RuntimeError("；".join(errors))

    try:
        _write_cleanup_manifests(staged, status="rolled_back")
    except OSError:
        pass
    _cleanup_stage_roots(staged)


def finalize_staged_task_media_cleanup(staged: StagedTaskMediaCleanup) -> TaskMediaCleanupResult:
    """数据库已提交后清除隔离目录；失败时保留清单供安全重试。"""
    if staged.targets:
        try:
            _write_cleanup_manifests(
                staged,
                status="committed",
                moved_paths=tuple(str(target.original_path) for target in staged.targets),
            )
        except OSError:
            return TaskMediaCleanupResult(
                deleted_paths=tuple(str(target.original_path) for target in staged.targets),
                freed_bytes=staged.freed_bytes,
                external_source_preserved=staged.external_source_preserved,
                cleanup_pending=True,
                staged_paths=tuple(str(root) for root in staged.manifest_roots if root.exists()),
            )

    pending = _cleanup_stage_roots(staged)
    return TaskMediaCleanupResult(
        deleted_paths=tuple(str(target.original_path) for target in staged.targets),
        freed_bytes=staged.freed_bytes,
        external_source_preserved=staged.external_source_preserved,
        cleanup_pending=bool(pending),
        staged_paths=pending,
    )


def apply_task_media_cleanup_plan(plan: TaskMediaCleanupPlan) -> TaskMediaCleanupResult:
    """兼容入口：先暂存，再完成清理；需要数据库原子性的调用方应分阶段调用。"""
    staged = stage_task_media_cleanup_plan(plan)
    return finalize_staged_task_media_cleanup(staged)


def move_task_directory_to_trash(task_id: str, task_name: str, task_dir_name: str | None = None) -> tuple[str, Path]:
    current_dir_name = resolve_task_dir_name(task_id, task_dir_name)
    source_dir = get_task_directory(task_id, current_dir_name)
    trash_dir_name = allocate_task_dir_name(
        task_name,
        parent_dir_name=TRASH_DIR_NAME,
        exclude_task_id=task_id,
        reserve=False,
    )
    target_dir = _storage_path_from_dir_name(trash_dir_name)
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    if source_dir.exists() and source_dir.resolve() != target_dir.resolve():
        shutil.move(str(source_dir), str(target_dir))
    return trash_dir_name, target_dir
