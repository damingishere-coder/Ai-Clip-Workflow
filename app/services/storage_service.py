from pathlib import Path, PureWindowsPath
from shutil import copyfileobj
from typing import BinaryIO
from uuid import uuid4

from app.core.config import EXTERNAL_STORAGE_ROOT, settings


TASK_SUBDIRECTORIES = ("source", "audio", "transcripts", "analysis", "clips", "05_clips", "06_subtitled", "logs")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm", ".m4v", ".ts"}


def ensure_storage_root() -> Path:
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    return settings.storage_root


def create_task_directory(task_id: str | None = None) -> Path:
    resolved_task_id = task_id or uuid4().hex[:12]
    task_dir = ensure_storage_root() / resolved_task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    for directory in get_expected_subdirectories(resolved_task_id).values():
        directory.mkdir(parents=True, exist_ok=True)
    return task_dir


def get_task_directory(task_id: str) -> Path:
    return settings.tasks_dir / task_id


def get_expected_subdirectories(task_id: str) -> dict[str, Path]:
    task_dir = get_task_directory(task_id)
    return {name: task_dir / name for name in TASK_SUBDIRECTORIES}


def get_artifact_paths(task_id: str) -> dict[str, Path]:
    directories = get_expected_subdirectories(task_id)
    return {
        "task_dir": get_task_directory(task_id),
        "audio_path": directories["audio"] / "source.wav",
        "transcript_path": directories["transcripts"] / "transcript.md",
        "analysis_path": directories["analysis"] / "candidate_clips.json",
        "clips_dir": directories["05_clips"],
        "subtitled_dir": directories["06_subtitled"],
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
        return settings.tasks_dir.joinpath(*PureWindowsPath(relative_value).parts)

    windows_storage_root = str(settings.storage_root)
    raw_value = path_value.replace("/", "\\")
    known_windows_root = str(EXTERNAL_STORAGE_ROOT)
    if raw_value.lower().startswith(known_windows_root.lower() + "\\"):
        relative_value = raw_value[len(known_windows_root) + 1 :]
        return settings.storage_root.joinpath(*PureWindowsPath(relative_value).parts)

    if windows_storage_root:
        normalized_root = windows_storage_root.replace("/", "\\")
        if raw_value.lower().startswith(normalized_root.lower() + "\\"):
            relative_value = raw_value[len(normalized_root) + 1 :]
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


def save_uploaded_video(task_id: str, filename: str, file_object: BinaryIO) -> Path:
    create_task_directory(task_id)
    safe_name = Path(filename or "source_video").name
    if not Path(safe_name).suffix:
        safe_name = f"{safe_name}.mp4"
    output_path = get_expected_subdirectories(task_id)["source"] / safe_name
    with output_path.open("wb") as target:
        copyfileobj(file_object, target)
    return output_path


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
