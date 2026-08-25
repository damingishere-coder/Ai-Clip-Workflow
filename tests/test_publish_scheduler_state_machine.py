from __future__ import annotations

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db import database as database_module
from app.db.database import (
    _backup_publish_database_before_data_migration,
    _cancel_duplicate_active_publish_jobs,
    get_connection,
    init_db,
)
from app.services import publish_scheduler as scheduler_module
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
            ) VALUES (?, ?, ?, ?, 'douyin', 'manual_export', 'original', ?, ?,
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


def test_duplicate_cleanup_preserves_failed_and_need_review_history():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE publish_jobs (
            id TEXT PRIMARY KEY, output_clip_id TEXT, platform TEXT, publish_mode TEXT,
            status TEXT, provider_response TEXT, created_at TEXT, updated_at TEXT,
            error_code TEXT, error_message TEXT, last_error TEXT
        )
        """
    )
    rows = [
        ("failed-history", "clip-1", "douyin", "local_browser", "FAILED", "2026-01-01T00:00:00Z"),
        ("review-history", "clip-1", "douyin", "local_browser", "NEED_REVIEW", "2026-01-02T00:00:00Z"),
    ]
    connection.executemany(
        "INSERT INTO publish_jobs (id, output_clip_id, platform, publish_mode, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(*row, row[-1]) for row in rows],
    )

    _cancel_duplicate_active_publish_jobs(connection)

    statuses = dict(connection.execute("SELECT id, status FROM publish_jobs").fetchall())
    connection.close()
    assert statuses == {"failed-history": "FAILED", "review-history": "NEED_REVIEW"}


def test_migration_backup_ignores_failed_and_need_review_retry_pair(monkeypatch, tmp_path):
    database_path = tmp_path / "workflow.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE publish_jobs (
            id TEXT PRIMARY KEY, output_clip_id TEXT, platform TEXT, publish_mode TEXT,
            status TEXT
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO publish_jobs (id, output_clip_id, platform, publish_mode, status)
        VALUES (?, 'clip-1', 'douyin', 'local_browser', ?)
        """,
        [("failed-history", "FAILED"), ("review-retry", "NEED_REVIEW")],
    )
    connection.commit()
    monkeypatch.setattr(
        database_module,
        "settings",
        SimpleNamespace(database_path=database_path, data_dir=tmp_path),
    )

    _backup_publish_database_before_data_migration(connection)

    connection.close()
    assert not (tmp_path / "backups").exists()


def test_not_due_job_is_not_claimed(tmp_path):
    calls: list[str] = []
    job_id = _job(tmp_path, scheduled_in=3600)
    PublishScheduler(executor=_executor(PublishResult(PublishOutcome.PUBLISHED), calls)).run_once()
    assert calls == []
    assert _raw(job_id)["status"] == "SCHEDULED"


def test_cancel_send_returns_job_to_preparation_and_clears_schedule(tmp_path):
    job_id = _job(tmp_path, status="SCHEDULED", scheduled_in=3600)
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET next_attempt_at = ?, claimed_at = ?, finished_at = ?,
                execution_id = 'old-execution', execution_phase = 'received',
                error_code = 'old-error', error_message = '旧错误', last_error = '旧错误',
                needs_manual_review = 1
            WHERE id = ?
            """,
            (_iso(3600), _iso(), _iso(), job_id),
        )
        connection.commit()

    result = PublishScheduler().cancel_job(job_id)
    job = _raw(job_id)

    assert result["job"]["status"] == "WAITING"
    assert "返回内容准备" in result["message"]
    assert job["scheduled_at"] == ""
    assert job["next_attempt_at"] is None
    assert job["claimed_at"] is None
    assert job["finished_at"] is None
    assert job["execution_id"] is None
    assert job["execution_phase"] == ""
    assert job["error_code"] == ""
    assert job["error_message"] == ""
    assert job["needs_manual_review"] == 0
    with get_connection() as connection:
        event = connection.execute(
            "SELECT * FROM publish_job_events WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    assert event["event_type"] == "returned_to_preparation"
    assert event["from_status"] == "SCHEDULED"
    assert event["to_status"] == "WAITING"


def test_skip_remains_terminal_cancelled(tmp_path):
    job_id = _job(tmp_path, status="WAITING")

    PublishScheduler().skip_job(job_id)

    assert _raw(job_id)["status"] == "CANCELLED"
    assert _raw(job_id)["error_message"] == "用户跳过任务"


def test_legacy_user_cancel_is_restored_on_database_init(tmp_path):
    job_id = _job(tmp_path, status="CANCELLED", scheduled_in=3600)
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET error_code = '', error_message = '用户取消任务', last_error = '用户取消任务'
            WHERE id = ?
            """,
            (job_id,),
        )
        connection.commit()

    init_db()
    job = _raw(job_id)

    assert job["status"] == "WAITING"
    assert job["scheduled_at"] == ""
    assert job["error_message"] == ""
    with get_connection() as connection:
        event = connection.execute(
            "SELECT event_type FROM publish_job_events WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    assert event["event_type"] == "legacy_cancel_restored"


def test_retry_returns_clear_error_when_same_clip_has_active_replacement(tmp_path):
    source_id = _job(tmp_path, status="FAILED")
    scheduler = PublishScheduler()
    replacement = scheduler.retry_failed(source_id, visibility="private")

    with pytest.raises(ValueError, match="已有任务"):
        scheduler.retry_failed(source_id, visibility="private")

    assert _raw(source_id)["status"] == "FAILED"
    assert _raw(replacement["job_id"])["status"] == "SCHEDULED"


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
        published_at=_iso() if outcome == PublishOutcome.PUBLISHED else "",
        error_code="mock_error" if outcome != PublishOutcome.PUBLISHED else "",
        needs_manual_review=outcome == PublishOutcome.NEED_REVIEW,
    )
    PublishScheduler(executor=_executor(result, calls)).run_once()
    row = _raw(job_id)
    assert calls == [job_id]
    assert row["status"] == expected
    assert row["claimed_at"]
    assert row["finished_at"]


def test_untrusted_provider_platform_url_requires_manual_review(tmp_path):
    calls: list[str] = []
    job_id = _job(tmp_path)
    result = PublishResult(
        outcome=PublishOutcome.PUBLISHED,
        message="投稿成功但链接异常",
        remote_video_id="remote-unsafe",
        platform_url="javascript:alert(1)",
        published_at=_iso(),
    )

    PublishScheduler(executor=_executor(result, calls)).run_once()

    row = _raw(job_id)
    assert calls == [job_id]
    assert row["status"] == "NEED_REVIEW"
    assert row["error_code"] == "invalid_platform_url"
    assert row["platform_url"] in {None, ""}


def test_published_job_is_never_executed_again(tmp_path):
    calls: list[str] = []
    job_id = _job(tmp_path, status="PUBLISHED")
    result = PublishScheduler(executor=_executor(PublishResult(PublishOutcome.PUBLISHED), calls)).execute_job(job_id)
    assert result["status"] == "skipped"
    assert calls == []


def test_two_schedulers_atomically_claim_only_once(tmp_path):
    calls: list[str] = []
    job_id = _job(tmp_path)
    executor = _executor(PublishResult(PublishOutcome.PUBLISHED, published_at=_iso()), calls)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: PublishScheduler(executor=executor).execute_job(job_id), range(2)))
    assert calls == [job_id]
    assert sorted(item["status"] for item in results) == ["published", "skipped"]


def test_manual_retry_creates_new_task_and_keeps_failed_history(tmp_path):
    old_id = _job(tmp_path, status="FAILED")
    created = PublishScheduler().retry_failed(old_id, visibility="private")
    assert _raw(old_id)["status"] == "FAILED"
    assert created["job_id"] != old_id
    assert _raw(created["job_id"])["retry_of_job_id"] == old_id
    assert _raw(created["job_id"])["status"] == "SCHEDULED"
    assert _raw(created["job_id"])["visibility"] == "private"


def test_run_forever_retries_after_transient_database_error(monkeypatch):
    scheduler = PublishScheduler(interval_seconds=1)
    attempts: list[int] = []

    def flaky_run_once():
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database temporarily unavailable")
        scheduler._record_scan_success(datetime.now(timezone.utc))
        scheduler.stop()
        return {"status": "ok"}

    monkeypatch.setattr(scheduler, "run_once", flaky_run_once)
    asyncio.run(asyncio.wait_for(scheduler.run_forever(), timeout=3))

    assert attempts == [1, 2]
    assert scheduler_module._SCHEDULER_HEALTH["running"] is False
    assert scheduler_module._SCHEDULER_HEALTH["consecutive_failures"] == 0
    assert scheduler_module._SCHEDULER_HEALTH["last_error_code"] == ""


def test_scheduler_background_task_is_tracked_and_awaited(monkeypatch):
    async def scenario():
        scheduler = PublishScheduler()
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def fake_run_forever():
            started.set()
            await stopped.wait()

        def fake_stop():
            stopped.set()

        monkeypatch.setattr(scheduler, "run_forever", fake_run_forever)
        monkeypatch.setattr(scheduler, "stop", fake_stop)
        monkeypatch.setattr(scheduler_module, "PublishScheduler", lambda: scheduler)
        original_enabled = scheduler_module.settings.publish_scheduler_enabled
        object.__setattr__(scheduler_module.settings, "publish_scheduler_enabled", True)
        try:
            returned = await scheduler_module.start_scheduler_background()
            await started.wait()

            assert returned is scheduler
            assert scheduler._background_task is not None
            assert scheduler._background_task.get_name() == "niuma-publish-scheduler"

            await scheduler.shutdown()
            assert scheduler._background_task.done()
        finally:
            object.__setattr__(
                scheduler_module.settings, "publish_scheduler_enabled", original_enabled
            )

    asyncio.run(scenario())


def test_scheduler_shutdown_before_background_task_initializes_does_not_hang():
    async def scenario():
        scheduler = PublishScheduler()
        scheduler._background_task = asyncio.create_task(scheduler.run_forever())
        await asyncio.wait_for(scheduler.shutdown(), timeout=3)
        assert scheduler._background_task.done()

    asyncio.run(scenario())


def test_unexpected_job_error_does_not_block_later_due_jobs(monkeypatch):
    scheduler = PublishScheduler()
    calls: list[str] = []
    monkeypatch.setattr(scheduler, "recover_interrupted_jobs", lambda: 0)
    monkeypatch.setattr(
        scheduler,
        "list_due_jobs",
        lambda: [{"id": "broken-job"}, {"id": "later-job"}],
    )

    def execute(job_id: str):
        calls.append(job_id)
        if job_id == "broken-job":
            raise ValueError("broken")
        return {"status": "skipped", "job_id": job_id}

    monkeypatch.setattr(scheduler, "execute_job", execute)
    monkeypatch.setattr(
        scheduler.repository,
        "get_job",
        lambda job_id: {"id": job_id, "status": "SCHEDULED"},
    )
    monkeypatch.setattr(
        scheduler,
        "_mark_need_review",
        lambda job_id, error_code, message, **_kwargs: {
            "status": "need_review",
            "job_id": job_id,
            "error_code": error_code,
            "message": message,
        },
    )

    result = scheduler.run_once()

    assert calls == ["broken-job", "later-job"]
    assert result["need_review_count"] == 1
    assert result["skipped_count"] == 1


def test_terminal_result_rolls_back_when_job_state_changed(tmp_path):
    job_id = _job(tmp_path, status="PUBLISHED")
    with get_connection() as connection:
        connection.execute(
            "UPDATE publish_jobs SET provider_response = ? WHERE id = ?",
            ('{"original": true}', job_id),
        )
        connection.commit()

    result = PublishScheduler()._mark_published(
        job_id,
        PublishResult(
            outcome=PublishOutcome.PUBLISHED,
            message="新结果",
            provider_response={"replacement": True},
        ),
    )

    assert result["status"] == "skipped"
    assert _raw(job_id)["provider_response"] == '{"original": true}'
