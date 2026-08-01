from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.database import get_connection, init_db
from app.services import publish_service


PREFIX = "test-cover-backfill-"


@pytest.fixture(autouse=True)
def cleanup_cover_backfill_rows():
    init_db()
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _seed_clip(tmp_path: Path, *, cover_time_seconds: float | None = None) -> dict:
    suffix = uuid4().hex[:8]
    task_id = f"{PREFIX}task-{suffix}"
    candidate_id = f"{PREFIX}candidate-{suffix}"
    output_clip_id = f"{PREFIX}clip-{suffix}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    video_path = tmp_path / f"{output_clip_id}.mp4"
    video_path.write_bytes(b"fake-video")
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
            INSERT INTO clip_candidates (
                id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                cover_time_seconds, created_at, updated_at
            )
            VALUES (?, ?, ?, '测试候选片段', '00:00:10', '00:01:10', 60, ?, ?, ?)
            """,
            (candidate_id, task_id, candidate_id, cover_time_seconds, now, now),
        )
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, clip_candidate_id, output_file_path, output_file_name,
                status, is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'completed', 1, ?, ?)
            """,
            (output_clip_id, task_id, candidate_id, str(video_path), video_path.name, now, now),
        )
        connection.commit()
    return {
        "task_id": task_id,
        "output_clip_id": output_clip_id,
        "video_path": video_path,
    }


def _seed_job(
    clip: dict,
    *,
    platform: str,
    status: str = "WAITING",
    cover_file_path: str = "",
    cover_time_seconds: float = 0,
) -> str:
    job_id = f"{PREFIX}job-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption,
                tags, hashtags, cover_mode, cover_time_seconds, cover_file_path,
                status, provider_response, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'local_browser', 'original', ?, ?, '测试标题',
                '测试简介', '测试简介', '测试', '测试', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                clip["task_id"],
                clip["output_clip_id"],
                clip["output_clip_id"],
                platform,
                str(clip["video_path"]),
                str(clip["video_path"]),
                "time" if cover_file_path else "auto",
                cover_time_seconds,
                cover_file_path,
                status,
                json.dumps({"source": "test"}, ensure_ascii=False),
                now,
                now,
            ),
        )
        connection.commit()
    return job_id


def test_backfill_generates_once_and_updates_both_platforms(monkeypatch, tmp_path):
    clip = _seed_clip(tmp_path)
    douyin_job = _seed_job(clip, platform="douyin")
    bilibili_job = _seed_job(clip, platform="bilibili")
    cover_path = tmp_path / "generated-midpoint.jpg"
    cover_path.write_bytes(b"cover")
    calls: list[tuple[str, object]] = []

    def fake_generate(item, preferred_time_seconds=None, video_source="original"):
        calls.append((item["output_clip_id"], preferred_time_seconds))
        return {
            "cover_file_path": str(cover_path),
            "cover_media_url": "/fake-cover",
            "cover_time_seconds": 30,
            "cover_source": "midpoint_fallback",
        }

    monkeypatch.setattr(publish_service, "generate_publish_cover_for_item", fake_generate)
    result = publish_service.backfill_missing_publish_covers()

    assert result["status"] == "ok"
    assert result["generated_cover_count"] == 1
    assert result["updated_job_count"] == 2
    assert calls == [(clip["output_clip_id"], None)]
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id, cover_mode, cover_time_seconds, cover_file_path FROM publish_jobs WHERE id IN (?, ?) ORDER BY id",
            (douyin_job, bilibili_job),
        ).fetchall()
    assert len(rows) == 2
    assert all(row["cover_mode"] == "time" for row in rows)
    assert all(row["cover_time_seconds"] == 30 for row in rows)
    assert all(row["cover_file_path"] == str(cover_path) for row in rows)


def test_backfill_only_updates_requested_platform(monkeypatch, tmp_path):
    clip = _seed_clip(tmp_path)
    douyin_job = _seed_job(clip, platform="douyin")
    bilibili_job = _seed_job(clip, platform="bilibili")
    cover_path = tmp_path / "douyin-only.jpg"
    cover_path.write_bytes(b"cover")

    monkeypatch.setattr(
        publish_service,
        "generate_publish_cover_for_item",
        lambda *_args, **_kwargs: {
            "cover_file_path": str(cover_path),
            "cover_media_url": "/fake-cover",
            "cover_time_seconds": 20,
            "cover_source": "midpoint_fallback",
        },
    )

    result = publish_service.backfill_missing_publish_covers("douyin")

    assert result["updated_job_count"] == 1
    assert [job["id"] for job in result["jobs"]] == [douyin_job]
    with get_connection() as connection:
        rows = {
            row["id"]: dict(row)
            for row in connection.execute(
                "SELECT id, cover_file_path FROM publish_jobs WHERE id IN (?, ?)",
                (douyin_job, bilibili_job),
            ).fetchall()
        }
    assert rows[douyin_job]["cover_file_path"] == str(cover_path)
    assert rows[bilibili_job]["cover_file_path"] in {"", None}


def test_backfill_rejects_unsupported_platform():
    with pytest.raises(ValueError, match="暂不支持"):
        publish_service.backfill_missing_publish_covers("unknown")


def test_backfill_reuses_existing_cover_and_skips_cancelled(monkeypatch, tmp_path):
    clip = _seed_clip(tmp_path, cover_time_seconds=12.5)
    existing_cover = tmp_path / "existing.jpg"
    existing_cover.write_bytes(b"existing-cover")
    existing_job = _seed_job(
        clip,
        platform="douyin",
        status="PUBLISHED",
        cover_file_path=str(existing_cover),
        cover_time_seconds=8,
    )
    missing_job = _seed_job(clip, platform="bilibili")
    cancelled_clip = _seed_clip(tmp_path)
    cancelled_job = _seed_job(cancelled_clip, platform="douyin", status="CANCELLED")
    monkeypatch.setattr(
        publish_service,
        "generate_publish_cover_for_item",
        lambda *_args, **_kwargs: pytest.fail("已有同切片封面时不应再次调用 FFmpeg"),
    )

    result = publish_service.backfill_missing_publish_covers()

    assert result["generated_cover_count"] == 0
    assert result["reused_cover_count"] == 1
    assert result["updated_job_count"] == 1
    with get_connection() as connection:
        rows = {
            row["id"]: dict(row)
            for row in connection.execute(
                "SELECT id, cover_file_path, cover_time_seconds FROM publish_jobs WHERE id IN (?, ?, ?)",
                (existing_job, missing_job, cancelled_job),
            ).fetchall()
        }
    assert rows[existing_job]["cover_file_path"] == str(existing_cover)
    assert rows[missing_job]["cover_file_path"] == str(existing_cover)
    assert rows[missing_job]["cover_time_seconds"] == 8
    assert rows[cancelled_job]["cover_file_path"] in {"", None}


def test_backfill_returns_partial_when_one_clip_fails(monkeypatch, tmp_path):
    first = _seed_clip(tmp_path, cover_time_seconds=9)
    second = _seed_clip(tmp_path)
    first_job = _seed_job(first, platform="douyin")
    second_job = _seed_job(second, platform="bilibili")
    cover_path = tmp_path / "partial-success.jpg"
    cover_path.write_bytes(b"cover")

    def fake_generate(item, preferred_time_seconds=None, video_source="original"):
        if item["output_clip_id"] == second["output_clip_id"]:
            raise ValueError("模拟 FFmpeg 失败")
        assert preferred_time_seconds == 9
        return {
            "cover_file_path": str(cover_path),
            "cover_media_url": "/fake-cover",
            "cover_time_seconds": 9,
            "cover_source": "ai_frame",
        }

    monkeypatch.setattr(publish_service, "generate_publish_cover_for_item", fake_generate)
    result = publish_service.backfill_missing_publish_covers()

    assert result["status"] == "partial"
    assert result["generated_cover_count"] == 1
    assert result["updated_job_count"] == 1
    assert result["failed_clip_count"] == 1
    assert "模拟 FFmpeg 失败" in result["errors"][0]["message"]
    with get_connection() as connection:
        first_row = connection.execute("SELECT cover_file_path FROM publish_jobs WHERE id = ?", (first_job,)).fetchone()
        second_row = connection.execute("SELECT cover_file_path FROM publish_jobs WHERE id = ?", (second_job,)).fetchone()
    assert first_row["cover_file_path"] == str(cover_path)
    assert second_row["cover_file_path"] in {"", None}
