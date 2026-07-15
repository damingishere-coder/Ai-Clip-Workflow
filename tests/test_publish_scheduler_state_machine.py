from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.database import get_connection, init_db
from app.services.publish_scheduler import PublishScheduler
from app.services.publishers.base import PublishOutcome, PublishResult


PREFIX = "test-state-machine-"


@pytest.fixture(autouse=True)
def clean_state_machine_rows():
    init_db()
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _iso(seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _job(tmp_path: Path, *, status: str = "SCHEDULED", scheduled_in: int = -60) -> str:
    suffix = uuid4().hex[:10]
    task_id = f"{PREFIX}{suffix}"
    clip_id = f"{PREFIX}clip-{suffix}"
    job_id = f"{PREFIX}job-{suffix}"
    video = tmp_path / f"{suffix}.mp4"
    video.write_bytes(b"fake video")
    now = _iso()
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO tasks (id, task_name, task_dir_name, platform, status, created_at, updated_at) VALUES (?, ?, ?, 'douyin', 'COMPLETED', ?, ?)",
            (task_id, task_id, task_id, now, now),
        )
        connection.execute(
            "INSERT INTO output_clip (id, task_id, output_file_path, output_file_name, status, is_active, created_at, updated_at) VALUES (?, ?, ?, 'clip.mp4', 'completed', 1, ?, ?)",
            (clip_id, task_id, str(video), now, now),
        )
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption,
                tags, hashtags, risk_flags, scheduled_at, schedule_timezone, timezone,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'douyin', 'local_browser', 'original', ?, ?,
                '测试标题', '测试正文', '测试正文', '测试', '测试', ?, ?,
                'Asia/Shanghai', 'Asia/Shanghai', ?, ?, ?)
            """,
            (job_id, task_id, clip_id, clip_id, str(video), str(video), json.dumps([]), _iso(scheduled_in), status, now, now),
        )
        connection.commit()
    return job_id


def _raw(job_id: str) -> dict:
    with get_connection() as connection:
        return dict(connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone())


def _executor(result: PublishResult, calls: list[str]):
    def execute(job_id: str, **_):
        calls.append(job_id)
        return result.as_dict()
    return execute


def test_publish_schema_migration_contains_worker_and_review_fields():
    with get_connection() as connection:
        job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(publish_jobs)")}
        account_columns = {row["name"] for row in connection.execute("PRAGMA table_info(publish_accounts)")}
        event_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'publish_job_events'"
        ).fetchone()
    assert {
        "claimed_at", "started_at", "finished_at", "max_attempts", "worker_id",
        "platform_url", "needs_manual_review", "timezone", "next_attempt_at",
        "execution_id", "execution_phase", "retry_of_job_id",
    }.issubset(job_columns)
    assert {"login_status", "login_checked_at", "login_message", "last_login_at", "auth_type"}.issubset(account_columns)
    assert event_table is not None


def test_not_due_job_is_not_claimed(tmp_path):
    calls: list[str] = []
    job_id = _job(tmp_path, scheduled_in=3600)
    PublishScheduler(executor=_executor(PublishResult(PublishOutcome.PUBLISHED), calls)).run_once()
    assert calls == []
    assert _raw(job_id)["status"] == "SCHEDULED"


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (PublishOutcome.PUBLISHED, "PUBLISHED"),
        (PublishOutcome.FAILED, "FAILED"),
        (PublishOutcome.NEED_REVIEW, "NEED_REVIEW"),
    ],
)
def test_due_job_follows_publisher_outcome(tmp_path, outcome, expected):
    calls: list[str] = []
    job_id = _job(tmp_path)
    result = PublishResult(
        outcome=outcome,
        message="mock result",
        remote_video_id="remote-1" if outcome == PublishOutcome.PUBLISHED else "",
        platform_url="https://www.douyin.com/video/1" if outcome == PublishOutcome.PUBLISHED else "",
        error_code="mock_error" if outcome != PublishOutcome.PUBLISHED else "",
        needs_manual_review=outcome == PublishOutcome.NEED_REVIEW,
    )
    PublishScheduler(executor=_executor(result, calls)).run_once()
    row = _raw(job_id)
    assert calls == [job_id]
    assert row["status"] == expected
    assert row["claimed_at"]
    assert row["finished_at"]


def test_published_job_is_never_executed_again(tmp_path):
    calls: list[str] = []
    job_id = _job(tmp_path, status="PUBLISHED")
    result = PublishScheduler(executor=_executor(PublishResult(PublishOutcome.PUBLISHED), calls)).execute_job(job_id)
    assert result["status"] == "skipped"
    assert calls == []


def test_two_schedulers_atomically_claim_only_once(tmp_path):
    calls: list[str] = []
    job_id = _job(tmp_path)
    executor = _executor(PublishResult(PublishOutcome.PUBLISHED), calls)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: PublishScheduler(executor=executor).execute_job(job_id), range(2)))
    assert calls == [job_id]
    assert sorted(item["status"] for item in results) == ["published", "skipped"]


def test_manual_retry_creates_new_task_and_keeps_failed_history(tmp_path):
    old_id = _job(tmp_path, status="FAILED")
    created = PublishScheduler().retry_failed(old_id)
    assert _raw(old_id)["status"] == "FAILED"
    assert created["job_id"] != old_id
    assert _raw(created["job_id"])["retry_of_job_id"] == old_id
    assert _raw(created["job_id"])["status"] == "SCHEDULED"
