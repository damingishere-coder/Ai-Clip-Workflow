from pathlib import Path
from shutil import copyfileobj
from typing import BinaryIO
from uuid import uuid4

from app.core.config import settings


TASK_SUBDIRECTORIES = ("source", "audio", "transcripts", "analysis", "clips", "05_clips", "logs")
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
        "log_path": directories["logs"] / "process.log",
    }


def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def validate_source_video_path(path_value: str | None) -> tuple[bool, str]:
    if not path_value:
        return False, "尚未选择视频文件"
    path = Path(path_value)
    if not path.exists():
        return False, "视频文件不存在"
    if not path.is_file():
        return False, "选择的路径不是文件"
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return False, "请选择常见视频文件格式"
    return True, ""


def get_source_video_path(task: dict) -> Path | None:
    source_path = task.get("nas_file_path") if task.get("source_type") == "nas" else task.get("original_video_path")
    return Path(source_path) if source_path else None


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
