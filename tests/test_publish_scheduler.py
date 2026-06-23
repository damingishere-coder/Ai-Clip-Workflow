from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.services.auto_publish_service import create_auto_publish_jobs
from app.services.publish_scheduler import PublishScheduler
from app.services.publish_service import get_publish_job


TEST_PREFIX = "test-v140-"


@pytest.fixture(autouse=True)
def publish_scheduler_db_cleanup(tmp_path):
    init_db()
    _cleanup()
    original_export_dir = settings.publish_scheduler_export_dir
    original_default_platform = settings.publish_scheduler_default_platform
    original_allow_without_review = settings.publish_scheduler_allow_publish_without_review
    object.__setattr__(settings, "publish_scheduler_export_dir", tmp_path / "发布 packages")
    object.__setattr__(settings, "publish_scheduler_default_platform", "manual_export")
    object.__setattr__(settings, "publish_scheduler_allow_publish_without_review", False)
    yield
    _cleanup()
    object.__setattr__(settings, "publish_scheduler_export_dir", original_export_dir)
    object.__setattr__(settings, "publish_scheduler_default_platform", original_default_platform)
    object.__setattr__(settings, "publish_scheduler_allow_publish_without_review", original_allow_without_review)


def _cleanup() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs")
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{TEST_PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{TEST_PREFIX}%",))
        connection.commit()


def _iso(delta_seconds: int = 0) -> str:
    return (datetime.now().astimezone() + timedelta(seconds=delta_seconds)).isoformat(timespec="seconds")


def _video(tmp_path: Path, name: str = "clip.mp4") -> Path:
    path = tmp_path / "含 中文 空格" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-video")
    return path


def _insert_job(
    tmp_path: Path,
    *,
    status: str = "SCHEDULED",
    scheduled_at: str | None = None,
    video_path: str | None = None,
    title: str = "测试标题",
    caption: str = "测试文案",
    risk_flags: list[str] | None = None,
) -> str:
    task_id = f"{TEST_PREFIX}{uuid4().hex[:8]}"
    clip_id = f"{TEST_PREFIX}clip-{uuid4().hex[:8]}"
    job_id = f"{TEST_PREFIX}job-{uuid4().hex[:8]}"
    now = _iso()
    video = video_path if video_path is not None else str(_video(tmp_path))
    risk_json = json.dumps(risk_flags or [], ensure_ascii=False)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (id, task_name, task_dir_name, status, created_at, updated_at)
            VALUES (?, ?, ?, 'COMPLETED', ?, ?)
            """,
            (task_id, task_id, task_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, output_file_path, output_file_name, status, is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, 'clip.mp4', 'completed', 1, ?, ?)
            """,
            (clip_id, task_id, video, now, now),
        )
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption,
                tags, hashtags, cover_text, risk_flags, scheduled_at, status,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'manual_export', 'manual_export',
                'original', ?, ?, ?, ?, ?, '#测试', '#测试', '封面文案', ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                task_id,
                clip_id,
                clip_id,
                video,
                video,
                title,
                caption,
                caption,
                risk_json,
                scheduled_at if scheduled_at is not None else _iso(-60),
                status,
                now,
                now,
            ),
        )
        connection.commit()
    return job_id


def test_future_scheduled_job_is_not_published(tmp_path):
    job_id = _insert_job(tmp_path, scheduled_at=_iso(3600))
    result = PublishScheduler(interval_seconds=1).run_once()
    job = get_publish_job(job_id)
    assert result["matched_count"] == 0
    assert job["status"] == "SCHEDULED"


def test_due_job_exports_publish_package(tmp_path):
    job_id = _insert_job(tmp_path, scheduled_at=_iso(-60))
    result = PublishScheduler(interval_seconds=1).run_once()
    job = get_publish_job(job_id)
    package_dir = Path(job["publish_result_payload"]["package_dir"])
    assert result["published_count"] == 1
    assert job["status"] == "PUBLISHED"
    assert (package_dir / "clip.mp4").exists()
    assert (package_dir / "title.txt").exists()
    assert (package_dir / "caption.txt").exists()
    assert (package_dir / "hashtags.txt").exists()
    assert (package_dir / "cover_text.txt").exists()
    assert (package_dir / "publish_plan.json").exists()
    assert (package_dir / "metadata.json").exists()


def test_missing_video_marks_failed(tmp_path):
    job_id = _insert_job(tmp_path, video_path=str(tmp_path / "missing.mp4"), scheduled_at=_iso(-60))
    PublishScheduler(interval_seconds=1).run_once()
    job = get_publish_job(job_id)
    assert job["status"] == "FAILED"
    assert "does not exist" in job["last_error"]


def test_need_review_is_not_auto_published(tmp_path):
    job_id = _insert_job(tmp_path, status="NEED_REVIEW", risk_flags=["sensitive"], scheduled_at=_iso(-60))
    PublishScheduler(interval_seconds=1).run_once()
    assert get_publish_job(job_id)["status"] == "NEED_REVIEW"


def test_cancelled_is_not_auto_published(tmp_path):
    job_id = _insert_job(tmp_path, status="CANCELLED", scheduled_at=_iso(-60))
    PublishScheduler(interval_seconds=1).run_once()
    assert get_publish_job(job_id)["status"] == "CANCELLED"


def test_failed_job_can_retry(tmp_path):
    job_id = _insert_job(tmp_path, status="FAILED", scheduled_at=_iso(-60))
    result = PublishScheduler(interval_seconds=1).retry_failed(job_id)
    assert result["status"] == "published"
    assert get_publish_job(job_id)["status"] == "PUBLISHED"


def test_published_job_is_not_republished(tmp_path):
    job_id = _insert_job(tmp_path, status="PUBLISHED", scheduled_at=_iso(-60))
    with get_connection() as connection:
        connection.execute("UPDATE publish_jobs SET attempt_count = 2 WHERE id = ?", (job_id,))
        connection.commit()
    PublishScheduler(interval_seconds=1).run_once()
    assert get_publish_job(job_id)["attempt_count"] == 2


def test_run_once_module_command(tmp_path):
    db_path = tmp_path / "run_once.sqlite3"
    env = {
        **os.environ,
        "DATABASE_PATH": str(db_path),
        "DATA_DIR": str(tmp_path / "data"),
        "TASKS_DIR": str(tmp_path / "tasks"),
        "STORAGE_ROOT": str(tmp_path / "tasks"),
        "PUBLISH_SCHEDULER_EXPORT_DIR": str(tmp_path / "exports"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "app.publish_scheduler", "run-once"],
        cwd=settings.project_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0
    assert "matched_count" in result.stdout


def test_windows_style_path_with_spaces_and_chinese_exports(tmp_path):
    video = _video(tmp_path, "中文 空格 clip.mp4")
    job_id = _insert_job(tmp_path, video_path=str(video), scheduled_at=_iso(-60))
    PublishScheduler(interval_seconds=1).run_once()
    package_dir = Path(get_publish_job(job_id)["publish_result_payload"]["package_dir"])
    assert (package_dir / "clip.mp4").exists()


def test_v130_auto_publish_job_is_scanned_by_v140_scheduler(tmp_path):
    task_id = f"{TEST_PREFIX}auto-{uuid4().hex[:8]}"
    clip_id = f"{TEST_PREFIX}out-{uuid4().hex[:8]}"
    video = _video(tmp_path, "auto_clip.mp4")
    now = _iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (id, task_name, task_dir_name, platform, status, created_at, updated_at)
            VALUES (?, ?, ?, 'general', 'COMPLETED', ?, ?)
            """,
            (task_id, task_id, task_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, output_file_path, output_file_name, status, is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, 'auto_clip.mp4', 'completed', 1, ?, ?)
            """,
            (clip_id, task_id, str(video), now, now),
        )
        connection.commit()

    create_auto_publish_jobs(
        {"id": task_id, "platform": "general"},
        [
            {
                "output_clip": {"id": clip_id, "output_file_path": str(video)},
                "metadata": {
                    "platform": "douyin",
                    "title": "自动任务标题",
                    "caption": "自动任务文案",
                    "hashtags": ["自动发布"],
                    "cover_text": "自动封面",
                    "risk_flags": [],
                    "source": "test",
                },
                "scheduled_at": _iso(-60),
            }
        ],
    )
    result = PublishScheduler(interval_seconds=1).run_once()
    assert result["published_count"] == 1
    with get_connection() as connection:
        row = connection.execute("SELECT status FROM publish_jobs WHERE task_id = ?", (task_id,)).fetchone()
    assert row["status"] == "PUBLISHED"
