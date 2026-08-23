from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.database import get_connection, init_db
from app.main import app
from app.services import publish_service


PREFIX = "test-publish-link-"


@pytest.fixture(autouse=True)
def clean_publish_link_data():
    init_db()
    _cleanup()
    yield
    _cleanup()


@pytest.fixture
def fake_cover(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cover_path = tmp_path / "cover.jpg"
    cover_path.write_bytes(b"cover")

    def generate_cover(_item: dict, _video_source: str = "original") -> dict:
        return {
            "cover_file_path": str(cover_path),
            "cover_time_seconds": 1.5,
        }

    monkeypatch.setattr(publish_service, "_generate_default_publish_cover", generate_cover)
    return cover_path


def _cleanup() -> None:
    with get_connection() as connection:
        job_rows = connection.execute(
            "SELECT id FROM publish_jobs WHERE task_id LIKE ?",
            (f"{PREFIX}%",),
        ).fetchall()
        if job_rows:
            placeholders = ",".join("?" for _ in job_rows)
            connection.execute(
                f"DELETE FROM publish_job_events WHERE job_id IN ({placeholders})",
                [row["id"] for row in job_rows],
            )
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM subtitle_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute(
            "DELETE FROM subtitle_cues WHERE revision_id IN (SELECT id FROM subtitle_revisions WHERE track_id IN (SELECT id FROM subtitle_tracks WHERE task_id LIKE ?))",
            (f"{PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM subtitle_revisions WHERE track_id IN (SELECT id FROM subtitle_tracks WHERE task_id LIKE ?)",
            (f"{PREFIX}%",),
        )
        connection.execute("DELETE FROM subtitle_tracks WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _time(minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _insert_task(tmp_path: Path, name: str = "关联测试", platform: str = "general") -> str:
    task_id = f"{PREFIX}{uuid4().hex[:8]}"
    source_path = tmp_path / f"{task_id}-source.mp4"
    source_path.write_bytes(b"source")
    now = _time()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, source_type, platform,
                original_video_path, status, created_at, updated_at
            ) VALUES (?, ?, ?, 'upload', ?, ?, 'completed', ?, ?)
            """,
            (task_id, name, task_id, platform, str(source_path), now, now),
        )
        connection.commit()
    return task_id


def _insert_candidate(task_id: str, suffix: str) -> str:
    candidate_id = f"{PREFIX}candidate-{suffix}-{uuid4().hex[:6]}"
    now = _time()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO clip_candidates (
                id, task_id, title, start_time, end_time, duration_seconds,
                summary, created_at, updated_at
            ) VALUES (?, ?, ?, '00:00:01', '00:00:11', 10, ?, ?, ?)
            """,
            (candidate_id, task_id, f"片段 {suffix}", f"片段 {suffix} 摘要", now, now),
        )
        connection.commit()
    return candidate_id


def _insert_output(
    tmp_path: Path,
    task_id: str,
    candidate_id: str,
    suffix: str,
    *,
    active: bool,
) -> tuple[str, Path]:
    output_id = f"{PREFIX}output-{suffix}-{uuid4().hex[:6]}"
    output_path = tmp_path / f"{output_id}.mp4"
    output_path.write_bytes(b"clip")
    now = _time()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, clip_candidate_id, output_file_path, output_file_name,
                status, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?)
            """,
            (
                output_id,
                task_id,
                candidate_id,
                str(output_path),
                output_path.name,
                int(active),
                now,
                now,
            ),
        )
        connection.commit()
    return output_id, output_path


def _insert_job(
    task_id: str,
    output_id: str,
    platform: str,
    status: str,
    *,
    title: str = "旧版标题",
    scheduled_at: str = "",
    error_code: str = "",
    created_at: str | None = None,
    publish_mode: str = "local_browser",
) -> str:
    job_id = f"{PREFIX}job-{uuid4().hex[:8]}"
    created_at = created_at or _time()
    with get_connection() as connection:
        output = connection.execute(
            "SELECT output_file_path FROM output_clip WHERE id = ?",
            (output_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption,
                tags, hashtags, scheduled_at, schedule_timezone, status, error_code,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'original', ?, ?, ?, '旧简介',
                '旧简介', '旧标签', '旧标签', ?, 'Asia/Shanghai', ?, ?, ?, ?)
            """,
            (
                job_id,
                task_id,
                output_id,
                output_id,
                platform,
                publish_mode,
                output["output_file_path"],
                output["output_file_path"],
                title,
                scheduled_at,
                status,
                error_code,
                created_at,
                created_at,
            ),
        )
        connection.commit()
    return job_id


def _job(job_id: str) -> dict:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row)


def test_first_sync_creates_only_douyin_and_is_idempotent(
    tmp_path: Path,
    fake_cover: Path,
) -> None:
    task_id = _insert_task(tmp_path)
    for suffix in ("one", "two"):
        candidate_id = _insert_candidate(task_id, suffix)
        _insert_output(tmp_path, task_id, candidate_id, suffix, active=True)

    first = publish_service.sync_task_publish_jobs(
        task_id,
        prefer_subtitled=False,
        restore_removed=False,
    )
    second = publish_service.sync_task_publish_jobs(
        task_id,
        prefer_subtitled=False,
        restore_removed=False,
    )

    assert first["created_count"] == 2
    assert first["link_state"]["linked_count"] == 2
    assert first["link_state"]["missing_count"] == 0
    assert second["created_count"] == 0
    assert second["skipped_count"] == 2
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT platform, status, scheduled_at, video_source
            FROM publish_jobs WHERE task_id = ? ORDER BY platform
            """,
            (task_id,),
        ).fetchall()
    assert len(rows) == 2
    assert {row["platform"] for row in rows} == {"douyin"}
    assert {row["status"] for row in rows} == {"WAITING"}
    assert all(not row["scheduled_at"] for row in rows)
    assert {row["video_source"] for row in rows} == {"original"}


def test_recut_cancels_only_old_preparation_and_preserves_execution_evidence(
    tmp_path: Path,
    fake_cover: Path,
) -> None:
    task_id = _insert_task(tmp_path)
    candidate_id = _insert_candidate(task_id, "recut")
    old_output_id, _ = _insert_output(tmp_path, task_id, candidate_id, "old", active=False)
    new_output_id, _ = _insert_output(tmp_path, task_id, candidate_id, "new", active=True)
    waiting_id = _insert_job(task_id, old_output_id, "douyin", "WAITING", title="要继承的标题")
    scheduled_id = _insert_job(
        task_id,
        old_output_id,
        "bilibili",
        "SCHEDULED",
        scheduled_at=_time(60),
    )
    published_id = _insert_job(
        task_id,
        old_output_id,
        "douyin",
        "PUBLISHED",
        created_at=_time(-20),
        publish_mode="manual_export",
    )
    review_id = _insert_job(
        task_id,
        old_output_id,
        "bilibili",
        "NEED_REVIEW",
        created_at=_time(-20),
        publish_mode="manual_export",
    )
    failed_id = _insert_job(
        task_id,
        old_output_id,
        "douyin",
        "FAILED",
        created_at=_time(-30),
        publish_mode="opencli_publish",
    )

    result = publish_service.sync_task_publish_jobs(
        task_id,
        prefer_subtitled=False,
        restore_removed=False,
    )

    assert result["created_count"] == 1
    assert result["superseded_count"] == 1
    assert _job(waiting_id)["status"] == "CANCELLED"
    assert _job(waiting_id)["error_code"] == publish_service.SUPERSEDED_BY_RECUT_ERROR_CODE
    assert _job(scheduled_id)["status"] == "SCHEDULED"
    assert _job(scheduled_id)["scheduled_at"]
    assert _job(published_id)["status"] == "PUBLISHED"
    assert _job(review_id)["status"] == "NEED_REVIEW"
    assert _job(failed_id)["status"] == "FAILED"
    with get_connection() as connection:
        new_jobs = connection.execute(
            "SELECT * FROM publish_jobs WHERE output_clip_id = ? ORDER BY platform",
            (new_output_id,),
        ).fetchall()
        events = connection.execute(
            """
            SELECT event_type FROM publish_job_events
            WHERE job_id IN (?, ?) ORDER BY id
            """,
            (waiting_id, scheduled_id),
        ).fetchall()
    assert len(new_jobs) == 1
    assert {row["platform"] for row in new_jobs} == {"douyin"}
    assert {row["status"] for row in new_jobs} == {"WAITING"}
    assert all(not row["scheduled_at"] for row in new_jobs)
    assert [row["event_type"] for row in events] == ["superseded_by_recut"]


def test_explicit_sync_restores_user_removed_content(
    tmp_path: Path,
    fake_cover: Path,
) -> None:
    task_id = _insert_task(tmp_path, platform="douyin")
    candidate_id = _insert_candidate(task_id, "restore")
    output_id, _ = _insert_output(tmp_path, task_id, candidate_id, "restore", active=True)
    job_id = _insert_job(task_id, output_id, "douyin", "WAITING")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET status = 'CANCELLED',
                scheduled_at = NULL,
                error_code = ?,
                error_message = '用户主动移出发送中心',
                updated_at = ?
            WHERE id = ?
            """,
            (publish_service.USER_REMOVED_ERROR_CODE, _time(), job_id),
        )
        connection.commit()

    automatic = publish_service.sync_task_publish_jobs(
        task_id,
        prefer_subtitled=False,
        restore_removed=False,
    )
    explicit = publish_service.sync_task_publish_jobs(
        task_id,
        prefer_subtitled=False,
        restore_removed=True,
    )

    assert automatic["created_count"] == 0
    assert automatic["restored_count"] == 0
    assert automatic["link_state"]["removed_count"] == 1
    assert explicit["restored_count"] == 1
    assert _job(job_id)["status"] == "WAITING"
    assert _job(job_id)["scheduled_at"] == ""


def test_subtitle_sync_updates_only_unscheduled_video_sources(
    tmp_path: Path,
    fake_cover: Path,
) -> None:
    task_id = _insert_task(tmp_path)
    candidate_id = _insert_candidate(task_id, "subtitle")
    output_id, _ = _insert_output(tmp_path, task_id, candidate_id, "subtitle", active=True)
    subtitled_path = tmp_path / f"{output_id}-subtitled.mp4"
    subtitled_path.write_bytes(b"subtitled")
    now = _time()
    with get_connection() as connection:
        track_id = f"{PREFIX}track-{uuid4().hex[:8]}"
        revision_id = f"{PREFIX}revision-{uuid4().hex[:8]}"
        connection.execute(
            """
            INSERT INTO subtitle_tracks (
                id, task_id, track_type, output_clip_id, name, active_revision_id,
                sync_status, created_at, updated_at
            ) VALUES (?, ?, 'clip', ?, '已审核字幕', ?, 'manual', ?, ?)
            """,
            (track_id, task_id, output_id, revision_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO subtitle_revisions (
                id, track_id, revision_number, origin, status, cue_count,
                checksum, created_at, approved_at
            ) VALUES (?, ?, 1, 'manual', 'approved', 1, 'verified-test', ?, ?)
            """,
            (revision_id, track_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO subtitle_jobs (
                id, task_id, output_clip_id, revision_id, status, output_file_path,
                validation_status, verified_at, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'completed', ?, 'verified', ?, 1, ?, ?)
            """,
            (
                f"{PREFIX}subtitle-{uuid4().hex[:8]}",
                task_id,
                output_id,
                revision_id,
                str(subtitled_path),
                now,
                now,
                now,
            ),
        )
        connection.commit()

    _insert_job(
        task_id,
        output_id,
        "bilibili",
        "SCHEDULED",
        scheduled_at=_time(60),
    )

    publish_service.sync_task_publish_jobs(
        task_id,
        prefer_subtitled=False,
        restore_removed=False,
    )
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs SET status = 'SCHEDULED', scheduled_at = ?
            WHERE task_id = ? AND platform = 'bilibili'
            """,
            (_time(60), task_id),
        )
        connection.commit()

    result = publish_service.sync_task_publish_jobs(
        task_id,
        prefer_subtitled=True,
        restore_removed=True,
    )
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT platform, status, video_source, video_file_path
            FROM publish_jobs WHERE task_id = ? ORDER BY platform
            """,
            (task_id,),
        ).fetchall()
    jobs = {row["platform"]: dict(row) for row in rows}

    assert result["updated_count"] == 1
    assert len(result["warnings"]) == 0
    assert jobs["douyin"]["video_source"] == "subtitled"
    assert jobs["douyin"]["video_file_path"] == str(subtitled_path)
    assert jobs["bilibili"]["status"] == "SCHEDULED"
    assert jobs["bilibili"]["video_source"] == "original"


def test_task_sync_routes_are_available() -> None:
    route_paths = set(app.openapi()["paths"])
    assert "/api/publish/tasks/{task_id}/link-state" in route_paths
    assert "/api/publish/tasks/{task_id}/sync" in route_paths
