from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.main import app
from app.services import publish_service
from app.services.auto_publish_service import create_auto_publish_jobs


PREFIX = "test-publish-group-"


@pytest.fixture(autouse=True)
def clean_publish_group_data():
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


def _time(minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _insert_task(tmp_path: Path, name: str, *, created_at: str) -> tuple[str, Path]:
    task_id = f"{PREFIX}{uuid4().hex[:8]}"
    source_path = tmp_path / f"{name}-原视频.mp4"
    source_path.write_bytes(b"source-video")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, source_type, platform, original_video_path,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, 'upload', 'general', ?, 'completed', ?, ?)
            """,
            (task_id, name, task_id, str(source_path), created_at, created_at),
        )
        connection.commit()
    return task_id, source_path


def _insert_clip_job(
    tmp_path: Path,
    task_id: str,
    *,
    platform: str,
    status: str,
    created_at: str,
    publish_mode: str = "local_browser",
    clip_id: str | None = None,
) -> tuple[str, str, Path]:
    output_clip_id = clip_id or f"{PREFIX}clip-{uuid4().hex[:8]}"
    job_id = f"{PREFIX}job-{uuid4().hex[:8]}"
    clip_path = tmp_path / f"{output_clip_id}.mp4"
    clip_path.write_bytes(b"cut-video")
    with get_connection() as connection:
        existing_clip = connection.execute("SELECT id FROM output_clip WHERE id = ?", (output_clip_id,)).fetchone()
        if not existing_clip:
            connection.execute(
                """
                INSERT INTO output_clip (
                    id, task_id, output_file_path, output_file_name, status,
                    is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'completed', 1, ?, ?)
                """,
                (output_clip_id, task_id, str(clip_path), clip_path.name, created_at, created_at),
            )
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption,
                tags, hashtags, scheduled_at, schedule_timezone, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'original', ?, ?, ?, '测试正文', '测试正文',
                '测试', '测试', ?, 'Asia/Shanghai', ?, ?, ?)
            """,
            (
                job_id,
                task_id,
                output_clip_id,
                output_clip_id,
                platform,
                publish_mode,
                str(clip_path),
                str(clip_path),
                f"{platform}测试片段",
                _time(60) if status == "SCHEDULED" else "",
                status,
                created_at,
                created_at,
            ),
        )
        connection.commit()
    return job_id, output_clip_id, clip_path


def _raw_job(job_id: str) -> dict:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row)


def test_publish_jobs_are_grouped_by_newest_source_task(tmp_path: Path) -> None:
    older_task, older_source = _insert_task(tmp_path, "较早任务", created_at=_time(-20))
    newer_task, newer_source = _insert_task(tmp_path, "最新任务", created_at=_time(-10))
    _insert_clip_job(tmp_path, older_task, platform="douyin", status="WAITING", created_at=_time(-19))
    _insert_clip_job(tmp_path, newer_task, platform="douyin", status="WAITING", created_at=_time(-9))
    _insert_clip_job(tmp_path, newer_task, platform="bilibili", status="WAITING", created_at=_time(-8))

    jobs = [
        job
        for job in publish_service.list_publish_jobs(
            limit=None,
            worker_state={"worker_available": True, "worker_message": ""},
        )
        if str(job.get("task_id") or "").startswith(PREFIX)
    ]
    groups = publish_service._build_publish_task_groups(jobs)

    assert [group["task_id"] for group in groups] == [newer_task, older_task]
    assert groups[0]["task_name"] == "最新任务"
    assert groups[0]["task_source_file_name"] == newer_source.name
    assert groups[1]["task_source_file_name"] == older_source.name
    assert [job["platform"] for job in groups[0]["jobs"]] == ["douyin", "bilibili"]


def test_publish_page_renders_task_identity_without_full_source_path(tmp_path: Path) -> None:
    task_id, source_path = _insert_task(tmp_path, "页面归类任务", created_at=_time(-5))
    _insert_clip_job(tmp_path, task_id, platform="douyin", status="WAITING", created_at=_time(-4))

    response = TestClient(app).get("/publish")
    html = response.text

    assert response.status_code == 200
    assert 'data-publish-task-group' in html
    assert "页面归类任务" in html
    assert source_path.name in html
    assert str(source_path) not in html
    assert "移出内容准备" in html
    assert 'data-task-group-select' in html
    assert "全选本任务" in html


def test_task_group_select_reuses_existing_batch_selection_state() -> None:
    script = (settings.project_root / "app" / "static" / "js" / "publish-center.js").read_text(encoding="utf-8")

    assert "function visibleTaskGroupRows(group)" in script
    assert "function syncTaskGroupSelectionUi(group)" in script
    assert 'event.target.closest("[data-task-group-select]")' in script
    assert "selectedJobIds.add(row.dataset.jobId)" in script
    assert "selectedJobIds.delete(row.dataset.jobId)" in script


def test_scheduled_content_card_has_clear_schedule_marker(tmp_path: Path) -> None:
    task_id, _ = _insert_task(tmp_path, "已排期标记任务", created_at=_time(-5))
    job_id, _, _ = _insert_clip_job(
        tmp_path,
        task_id,
        platform="douyin",
        status="SCHEDULED",
        created_at=_time(-4),
    )

    response = TestClient(app).get("/publish")
    html = response.text
    card_start = html.index(f'data-job-id="{job_id}"')
    card_end = html.index("</article>", card_start)
    card_html = html[card_start:card_end]

    assert response.status_code == 200
    assert 'data-status="SCHEDULED"' in card_html
    assert "data-content-schedule" in card_html
    assert "已排期" in card_html
    assert "调整排期" in card_html


def test_schedule_marker_updates_without_page_reload() -> None:
    script = (settings.project_root / "app" / "static" / "js" / "publish-center.js").read_text(encoding="utf-8")

    assert 'row.classList.toggle("is-scheduled", isScheduled)' in script
    assert "contentScheduleBadge.hidden = !isScheduled" in script
    assert 'isScheduled ? "调整排期" : "加入发布计划"' in script


def test_dismiss_keeps_files_and_other_platform_and_blocks_recreation(tmp_path: Path) -> None:
    task_id, _ = _insert_task(tmp_path, "安全移出任务", created_at=_time(-5))
    douyin_job, clip_id, clip_path = _insert_clip_job(
        tmp_path,
        task_id,
        platform="douyin",
        status="SCHEDULED",
        created_at=_time(-4),
    )
    bilibili_job, _, _ = _insert_clip_job(
        tmp_path,
        task_id,
        platform="bilibili",
        status="WAITING",
        created_at=_time(-3),
        clip_id=clip_id,
    )

    dismissed = publish_service.dismiss_publish_job(douyin_job)
    raw_douyin = _raw_job(douyin_job)

    assert dismissed["job"]["status"] == "CANCELLED"
    assert dismissed["job"]["is_user_removed"] is True
    assert raw_douyin["error_code"] == publish_service.USER_REMOVED_ERROR_CODE
    assert raw_douyin["scheduled_at"] == ""
    assert _raw_job(bilibili_job)["status"] == "WAITING"
    assert clip_path.exists()

    refreshed = publish_service.refresh_send_queue(use_ai=False, platform="douyin")
    assert refreshed["skipped_removed"] >= 1
    assert not any(job.get("output_clip_id") == clip_id for job in refreshed["created"])

    auto_result = create_auto_publish_jobs(
        {"id": task_id, "platform": "douyin"},
        [
            {
                "output_clip": {"id": clip_id, "output_file_path": str(clip_path)},
                "metadata": {
                    "platform": "douyin",
                    "title": "不会重建",
                    "caption": "不会重建正文",
                    "hashtags": ["测试"],
                    "risk_flags": [],
                },
                "scheduled_at": "",
            }
        ],
    )
    assert auto_result["created_count"] == 0
    assert auto_result["skipped_count"] == 1

    with get_connection() as connection:
        events = connection.execute(
            "SELECT event_type FROM publish_job_events WHERE job_id = ? ORDER BY id",
            (douyin_job,),
        ).fetchall()
    assert [event["event_type"] for event in events] == ["removed_from_preparation"]


def test_restore_returns_to_waiting_and_rejects_active_duplicate(tmp_path: Path) -> None:
    task_id, _ = _insert_task(tmp_path, "恢复任务", created_at=_time(-5))
    job_id, clip_id, _ = _insert_clip_job(
        tmp_path,
        task_id,
        platform="douyin",
        status="WAITING",
        created_at=_time(-4),
    )
    publish_service.dismiss_publish_job(job_id)

    restored = publish_service.restore_publish_job(job_id)
    assert restored["job"]["status"] == "WAITING"
    assert restored["job"]["is_user_removed"] is False
    assert _raw_job(job_id)["error_code"] == ""

    publish_service.dismiss_publish_job(job_id)
    _insert_clip_job(
        tmp_path,
        task_id,
        platform="douyin",
        status="WAITING",
        created_at=_time(-2),
        publish_mode="manual_export",
        clip_id=clip_id,
    )
    with pytest.raises(ValueError, match="已有有效发布内容"):
        publish_service.restore_publish_job(job_id)


def test_dismiss_and_restore_routes_are_available() -> None:
    route_paths = set(app.openapi()["paths"])
    assert "/api/publish/jobs/{job_id}/dismiss" in route_paths
    assert "/api/publish/jobs/{job_id}/restore" in route_paths
    assert settings.database_path.name == "test_workflow.sqlite3"
