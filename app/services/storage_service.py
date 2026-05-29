from pathlib import Path, PureWindowsPath
import re
import sqlite3
import shutil
from shutil import copyfileobj
from typing import BinaryIO
from uuid import uuid4

from app.core.config import EXTERNAL_STORAGE_ROOT, settings


TASK_SUBDIRECTORIES = ("source", "audio", "transcripts", "analysis", "clips", "05_clips", "06_subtitled", "07_covers", "logs")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm", ".m4v", ".ts"}
TRASH_DIR_NAME = "_回收站"
_WINDOWS_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def ensure_storage_root() -> Path:
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    return settings.storage_root


def _storage_relative_parts(task_dir_name: str) -> tuple[str, ...]:
    return tuple(part for part in PureWindowsPath(task_dir_name).parts if part not in {"", "."})


def _storage_path_from_dir_name(task_dir_name: str) -> Path:
    return settings.tasks_dir.joinpath(*_storage_relative_parts(task_dir_name))


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
    except sqlite3.Error:
        return set()
    return {str(row["task_dir_name"]).lower() for row in rows}


def allocate_task_dir_name(
    task_name: str | None,
    parent_dir_name: str | None = None,
    exclude_task_id: str | None = None,
) -> str:
    base_name = sanitize_task_dir_name(task_name, fallback=exclude_task_id or "untitled")
    parent_parts = _storage_relative_parts(parent_dir_name or "")
    existing_names = _get_existing_task_dir_names(exclude_task_id=exclude_task_id)
    root = ensure_storage_root().joinpath(*parent_parts)
    root.mkdir(parents=True, exist_ok=True)

    for index in range(1, 1000):
        candidate_name = base_name if index == 1 else f"{base_name} ({index})"
        candidate_parts = (*parent_parts, candidate_name)
        relative_name = str(PureWindowsPath(*candidate_parts))
        candidate_path = ensure_storage_root().joinpath(*candidate_parts)
        if relative_name.lower() not in existing_names and not candidate_path.exists():
            return relative_name

    return str(PureWindowsPath(*parent_parts, f"{base_name}-{uuid4().hex[:6]}"))


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
    except sqlite3.Error:
        return None
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
        return settings.tasks_dir.joinpath(*PureWindowsPath(relative_value).parts)

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
        return settings.storage_root.joinpath(*PureWindowsPath(relative_value).parts)

    if windows_storage_root:
        normalized_root = windows_storage_root.replace("/", "\\")
        if raw_value.lower().startswith(normalized_root.lower() + "\\"):
            relative_value = raw_value[len(normalized_root) + 1 :]
            first_part = PureWindowsPath(relative_value).parts[0] if PureWindowsPath(relative_value).parts else ""
            task_dir_name = _fetch_task_dir_name(first_part)
            if task_dir_name and task_dir_name != first_part:
                rest_parts = PureWindowsPath(relative_value).parts[1:]
                relative_value = str(PureWindowsPath(task_dir_name, *rest_parts))
            return settings.storage_root.joinpath(*PureWindowsPath(relative_value).parts)

    return path


def validate_source_video_path(path_value: str | None) -> tuple[bool, str]:
    if not path_value:
        return False, "尚未选择视频文件"
    path = resolve_video_file_path(path_value)
    if path is None:
        return False, "尚未选择视频文件"
    if not path.exists():
        return False, "视频文件不存在"
    if not path.is_file():
        return False, "选择的路径不是文件"
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return False, "请选择常见视频文件格式"
    return True, ""


def get_source_video_path(task: dict) -> Path | None:
    source_path = task.get("nas_file_path") if task.get("source_type") == "nas" else task.get("original_video_path")
    return resolve_video_file_path(source_path)


def save_uploaded_video(task_id: str, filename: str, file_object: BinaryIO, task_dir_name: str | None = None) -> Path:
    create_task_directory(task_id, task_dir_name)
    safe_name = Path(filename or "source_video").name
    if not Path(safe_name).suffix:
        safe_name = f"{safe_name}.mp4"
    output_path = get_expected_subdirectories(task_id, task_dir_name)["source"] / safe_name
    with output_path.open("wb") as target:
        copyfileobj(file_object, target)
    return output_path


def move_task_directory_to_trash(task_id: str, task_name: str, task_dir_name: str | None = None) -> tuple[str, Path]:
    current_dir_name = resolve_task_dir_name(task_id, task_dir_name)
    source_dir = get_task_directory(task_id, current_dir_name)
    trash_dir_name = allocate_task_dir_name(task_name, parent_dir_name=TRASH_DIR_NAME, exclude_task_id=task_id)
    target_dir = _storage_path_from_dir_name(trash_dir_name)
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    if source_dir.exists() and source_dir.resolve() != target_dir.resolve():
        shutil.move(str(source_dir), str(target_dir))
    return trash_dir_name, target_dir


def browse_video_directory(path_value: str | None) -> dict:
    base_path = Path(path_value) if path_value else settings.storage_root
    if not base_path.exists():
        return {"path": str(base_path), "exists": False, "directories": [], "files": []}
    if base_path.is_file():
        base_path = base_path.parent

    directories = []
    files = []
    for item in sorted(base_path.iterdir(), key=lambda value: (value.is_file(), value.name.lower())):
        if item.is_dir():
            directories.append({"name": item.name, "path": str(item)})
        elif is_video_file(item):
            files.append({"name": item.name, "path": str(item), "size": item.stat().st_size})

    return {
        "path": str(base_path),
        "exists": True,
        "directories": directories,
        "files": files,
    }
