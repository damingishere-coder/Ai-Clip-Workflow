from pathlib import Path
from uuid import uuid4

from app.core.config import settings


def create_task_directory(task_id: str | None = None) -> Path:
    resolved_task_id = task_id or uuid4().hex[:12]
    task_dir = settings.tasks_dir / resolved_task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def get_task_directory(task_id: str) -> Path:
    return settings.tasks_dir / task_id


def get_expected_subdirectories(task_id: str) -> dict[str, Path]:
    task_dir = get_task_directory(task_id)
    return {
        "source": task_dir / "source",
        "audio": task_dir / "audio",
        "transcripts": task_dir / "transcripts",
        "analysis": task_dir / "analysis",
        "clips": task_dir / "clips",
        "logs": task_dir / "logs",
    }
