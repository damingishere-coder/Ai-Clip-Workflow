from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.database import get_connection, init_db
from app.services import publish_service
from app.services.publish_readiness import PublishPlatformIsolationBlocked


PREFIX = "test-publish-history-"


@pytest.fixture(autouse=True)
def clean_publish_history_rows():
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


def _seed_job(
    tmp_path: Path,
    *,
    status: str,
    platform: str = "douyin",
    scheduled_at: str = "",
    started_at: str = "",
    finished_at: str = "",
    history_hidden: int = 0,
) -> str:
    suffix = uuid4().hex[:10]
    task_id = f"{PREFIX}{suffix}"
    clip_id = f"{PREFIX}clip-{suffix}"
    job_id = f"{PREFIX}job-{suffix}"
    video = tmp_path / f"{suffix}.mp4"
    video.write_bytes(b"fake video")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, platform, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'COMPLETED', ?, ?)
            """,
            (task_id, task_id, task_id, platform, now, now),
        )
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, output_file_path, output_file_name,
                status, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, 'clip.mp4', 'completed', 1, ?, ?)
            """,
            (clip_id, task_id, str(video), now, now),
        )
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption,
                tags, hashtags, visibility, scheduled_at, schedule_timezone, timezone,
                status, started_at, finished_at, history_hidden, history_hidden_at,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, 'manual_export',
                'original', ?, ?, '执行记录测试', '测试正文', '测试正文',
                '测试', '测试', 'public', ?, 'Asia/Shanghai', 'Asia/Shanghai',
                ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                job_id,
                task_id,
                clip_id,
                clip_id,
                platform,
                str(video),
                str(video),
                scheduled_at,
                status,
                started_at or None,
                finished_at or None,
                history_hidden,
                now if history_hidden else None,
                now,
                now,
            ),
        )
        connection.commit()
    return job_id


def _raw(job_id: str) -> dict:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row)


def test_publish_history_schema_is_backward_compatible():
    with get_connection() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(publish_jobs)")}
        index = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_publish_jobs_history_visibility'"
        ).fetchone()
    assert {"history_hidden", "history_hidden_at"}.issubset(columns)
    assert index is not None


def test_history_calendar_uses_schedule_then_start_and_excludes_hidden(tmp_path):
    scheduled = _seed_job(
        tmp_path,
        status="PUBLISHED",
        scheduled_at="2026-07-27T16:30:00+00:00",
        finished_at="2026-07-29T18:00:00+00:00",
    )
    started = _seed_job(
        tmp_path,
        status="FAILED",
        started_at="2026-07-28T09:00:00",
        finished_at="2026-07-28T10:00:00",
    )
    _seed_job(
        tmp_path,
        status="FAILED",
        scheduled_at="2026-07-27T17:00:00+00:00",
        history_hidden=1,
    )
    _seed_job(
        tmp_path,
        status="PUBLISHED",
        scheduled_at="2026-07-31T16:30:00+00:00",
    )

    calendar = publish_service.get_publish_history_calendar("douyin", "2026-07")
    day = next(item for item in calendar["days"] if item["date"] == "2026-07-28")

    assert day["total"] == 2
    assert day["counts"]["PUBLISHED"] == 1
    assert day["counts"]["FAILED"] == 1
    assert all(item["date"] != "2026-08-01" for item in calendar["days"])

    records = publish_service.list_publish_history_records(
        platform="douyin",
        date="2026-07-28",
        status="all",
        page=1,
        page_size=50,
    )
    assert {job["id"] for job in records["jobs"]} == {scheduled, started}
    assert all(job["history_date"] == "2026-07-28" for job in records["jobs"])


def test_history_hide_is_atomic_and_restore_preserves_job(tmp_path):
    failed_id = _seed_job(
        tmp_path,
        status="FAILED",
        scheduled_at="2026-07-27T16:30:00+00:00",
        finished_at="2026-07-27T17:00:00+00:00",
    )
    publishing_id = _seed_job(
        tmp_path,
        status="PUBLISHING",
        scheduled_at="2026-07-27T16:40:00+00:00",
        started_at="2026-07-27T16:40:00+00:00",
    )
    before = _raw(failed_id)

    with pytest.raises(ValueError, match="终态记录"):
        publish_service.hide_publish_history_records(
            [failed_id, publishing_id],
            platform="douyin",
        )
    assert _raw(failed_id)["history_hidden"] == 0
    assert _raw(publishing_id)["history_hidden"] == 0

    hidden = publish_service.hide_publish_history_records([failed_id], platform="douyin")
    after_hide = _raw(failed_id)
    assert hidden["affected_count"] == 1
    assert after_hide["history_hidden"] == 1
    assert after_hide["status"] == before["status"]
    assert after_hide["scheduled_at"] == before["scheduled_at"]
    assert after_hide["finished_at"] == before["finished_at"]

    deleted_records = publish_service.list_publish_history_records(
        platform="douyin",
        deleted=True,
    )
    assert [job["id"] for job in deleted_records["jobs"]] == [failed_id]

    restored = publish_service.restore_publish_history_records([failed_id], platform="douyin")
    assert restored["affected_count"] == 1
    assert _raw(failed_id)["history_hidden"] == 0
    with get_connection() as connection:
        event_types = [
            row["event_type"]
            for row in connection.execute(
                "SELECT event_type FROM publish_job_events WHERE job_id = ? ORDER BY id",
                (failed_id,),
            ).fetchall()
        ]
    assert event_types[-2:] == ["history_record_hidden", "history_record_restored"]


def test_history_batch_rejects_cross_platform_records(tmp_path):
    douyin = _seed_job(tmp_path, status="FAILED", platform="douyin")
    bilibili = _seed_job(tmp_path, status="FAILED", platform="bilibili")

    with pytest.raises(PublishPlatformIsolationBlocked):
        publish_service.hide_publish_history_records([douyin, bilibili], platform="douyin")

    assert _raw(douyin)["history_hidden"] == 0
    assert _raw(bilibili)["history_hidden"] == 0
