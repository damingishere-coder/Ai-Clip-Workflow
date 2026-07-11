from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.main import app
from app.models.task import PublishJobCreate
from app.services.auto_publish_service import create_auto_publish_jobs
from app.services.publish_domain import TARGET_PLATFORMS
from app.services.publish_scheduler import PublishScheduler, build_batch_schedule_times
from app.services import publish_service


PREFIX = "test-real-publish-"


@pytest.fixture(autouse=True)
def clean_publish_data(tmp_path):
    init_db()
    original_export = settings.publish_scheduler_export_dir
    original_default_mode = settings.publish_default_mode
    original_stale = settings.publish_job_stale_minutes
    object.__setattr__(settings, "publish_scheduler_export_dir", tmp_path / "exports")
    object.__setattr__(settings, "publish_default_mode", "opencli_publish")
    object.__setattr__(settings, "publish_job_stale_minutes", 30)
    _cleanup()
    yield
    _cleanup()
    object.__setattr__(settings, "publish_scheduler_export_dir", original_export)
    object.__setattr__(settings, "publish_default_mode", original_default_mode)
    object.__setattr__(settings, "publish_job_stale_minutes", original_stale)


def _cleanup():
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _utc(delta_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert_job(
    tmp_path: Path,
    *,
    status: str = "SCHEDULED",
    publish_mode: str = "opencli_publish",
    platform: str = "douyin",
    scheduled_at: str | None = None,
    risk_flags: list[str] | None = None,
    updated_at: str | None = None,
) -> str:
    task_id = f"{PREFIX}{uuid4().hex[:8]}"
    clip_id = f"{PREFIX}clip-{uuid4().hex[:8]}"
    job_id = f"{PREFIX}job-{uuid4().hex[:8]}"
    video = tmp_path / f"{clip_id}.mp4"
    video.write_bytes(b"fake-video")
    now = _utc()
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO tasks (id, task_name, task_dir_name, platform, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'COMPLETED', ?, ?)",
            (task_id, task_id, task_id, platform, now, now),
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
                tags, hashtags, risk_flags, scheduled_at, schedule_timezone, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'original', ?, ?, '测试标题', '测试正文', '测试正文',
                '测试', '测试', ?, ?, 'Asia/Shanghai', ?, ?, ?)
            """,
            (
                job_id, task_id, clip_id, clip_id, platform, publish_mode,
                str(video), str(video), json.dumps(risk_flags or [], ensure_ascii=False),
                scheduled_at if scheduled_at is not None else _utc(-60), status, now, updated_at or now,
            ),
        )
        connection.commit()
    return job_id


def _raw(job_id: str) -> dict:
    with get_connection() as connection:
        return dict(connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone())


def _mock_opencli_success(monkeypatch, calls: list[str]):
    def execute(job_id, runner=None):
        calls.append(job_id)
        return {"status": "ok", "message": "mock submitted", "job": _raw(job_id)}
    monkeypatch.setattr(publish_service, "execute_opencli_send_job", execute)


def test_platform_and_publish_mode_are_separate():
    assert TARGET_PLATFORMS == {"douyin": "抖音", "bilibili": "B站"}
    with pytest.raises(ValidationError):
        PublishJobCreate(task_id="task", output_clip_id="clip", platform="manual_export", title="标题")


def test_auto_pipeline_creates_only_metadata_target_platform(tmp_path):
    job_id = _insert_job(tmp_path, status="CANCELLED")
    seed = _raw(job_id)
    result = create_auto_publish_jobs(
        {"id": seed["task_id"], "platform": "general"},
        [{
            "output_clip": {"id": seed["output_clip_id"], "output_file_path": seed["video_file_path"]},
            "metadata": {"platform": "bilibili", "title": "自动标题", "caption": "自动正文", "hashtags": ["自动"], "risk_flags": []},
            "scheduled_at": "",
        }],
    )
    created = result["created"][0]
    assert created["platform"] == "bilibili"
    assert created["publish_mode"] == "opencli_publish"
    assert created["status"] == "WAITING"


def test_refresh_queue_does_not_turn_manual_export_into_platform(monkeypatch, tmp_path):
    manual_id = _insert_job(tmp_path, status="WAITING", publish_mode="manual_export", platform="douyin")
    monkeypatch.setattr(publish_service, "_generate_default_publish_cover", lambda *args, **kwargs: {})
    result = publish_service.refresh_send_queue(use_ai=False)
    assert _raw(manual_id)["publish_mode"] == "manual_export"
    assert all(job["platform"] in TARGET_PLATFORMS for job in result["created"])
    assert not any(job["platform"] in {"manual_export", "local_browser"} for job in result["created"])


def test_shanghai_local_time_is_stored_as_utc():
    result = build_batch_schedule_times(
        1, start_at_local="2026-07-12T09:00", timezone_name="Asia/Shanghai",
        interval_minutes=180, daily_start_time="09:00", daily_end_time="21:00",
    )
    assert result == ["2026-07-12T01:00:00Z"]


def test_daily_window_overflow_moves_to_next_local_day():
    result = build_batch_schedule_times(
        3, start_at_local="2026-07-12T20:00", timezone_name="Asia/Shanghai",
        interval_minutes=180, daily_start_time="09:00", daily_end_time="21:00",
    )
    assert result == ["2026-07-12T12:00:00Z", "2026-07-13T01:00:00Z", "2026-07-13T04:00:00Z"]


def test_scheduled_job_can_publish_now(monkeypatch, tmp_path):
    calls = []
    _mock_opencli_success(monkeypatch, calls)
    job_id = _insert_job(tmp_path, status="SCHEDULED", scheduled_at=_utc(3600))
    result = PublishScheduler().publish_now(job_id)
    assert result["status"] == "published"
    assert calls == [job_id]
    assert _raw(job_id)["status"] == "PUBLISHED"


def test_due_opencli_job_calls_executor_and_success_is_published(monkeypatch, tmp_path):
    calls = []
    _mock_opencli_success(monkeypatch, calls)
    job_id = _insert_job(tmp_path)
    PublishScheduler().run_once()
    assert job_id in calls
    assert _raw(job_id)["status"] == "PUBLISHED"
    assert _raw(job_id)["published_at"]


def test_opencli_failure_becomes_failed(monkeypatch, tmp_path):
    job_id = _insert_job(tmp_path)
    monkeypatch.setattr(
        publish_service,
        "execute_opencli_send_job",
        lambda job_id, runner=None: {"status": "failed", "message": "mock failed", "job": _raw(job_id)},
    )
    PublishScheduler().run_once()
    assert _raw(job_id)["status"] == "FAILED"


def test_manual_export_success_is_exported(tmp_path):
    job_id = _insert_job(tmp_path, publish_mode="manual_export")
    result = PublishScheduler().run_once()
    assert result["exported_count"] == 1
    assert _raw(job_id)["status"] == "EXPORTED"
    assert not _raw(job_id)["published_at"]


def test_need_review_is_never_executed(monkeypatch, tmp_path):
    calls = []
    _mock_opencli_success(monkeypatch, calls)
    job_id = _insert_job(tmp_path, status="NEED_REVIEW", risk_flags=["敏感"])
    PublishScheduler().run_once()
    assert calls == []
    assert _raw(job_id)["status"] == "NEED_REVIEW"


def test_two_schedulers_cannot_claim_same_job(monkeypatch, tmp_path):
    calls = []
    _mock_opencli_success(monkeypatch, calls)
    job_id = _insert_job(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: PublishScheduler().execute_job(job_id), range(2)))
    assert len(calls) == 1
    assert sorted(item["status"] for item in results) == ["published", "skipped"]


def test_only_stale_publishing_job_is_recovered(tmp_path):
    stale = _insert_job(tmp_path, status="PUBLISHING", updated_at=_utc(-3600))
    fresh = _insert_job(tmp_path, status="PUBLISHING", updated_at=_utc(-60))
    recovered = PublishScheduler().recover_interrupted_jobs()
    assert recovered == 1
    assert _raw(stale)["status"] == "SCHEDULED"
    assert _raw(fresh)["status"] == "PUBLISHING"


def test_preview_and_save_use_identical_schedule(tmp_path):
    first = _insert_job(tmp_path, status="WAITING", scheduled_at="")
    second = _insert_job(tmp_path, status="WAITING", scheduled_at="")
    scheduler = PublishScheduler()
    params = dict(
        start_at_local="2026-07-12T20:00", timezone_name="Asia/Shanghai",
        interval_minutes=180, daily_start_time="09:00", daily_end_time="21:00",
    )
    preview = scheduler.preview_batch_schedule([first, second], **params)
    saved = scheduler.update_batch_schedule([first, second], action="apply", **params)
    assert saved["schedule"] == preview["schedule"]
    assert [_raw(first)["scheduled_at"], _raw(second)["scheduled_at"]] == [
        item["scheduled_at_utc"] for item in preview["schedule"]
    ]


def test_schedule_preview_api_matches_save_api(tmp_path):
    first = _insert_job(tmp_path, status="WAITING", scheduled_at="")
    second = _insert_job(tmp_path, status="WAITING", scheduled_at="")
    payload = {
        "job_ids": [first, second], "action": "apply", "start_at_local": "2026-07-12T20:00",
        "timezone": "Asia/Shanghai", "interval_minutes": 180,
        "daily_start_time": "09:00", "daily_end_time": "21:00",
    }
    headers = {"Authorization": f"Bearer {settings.local_admin_token}"} if settings.local_admin_token else {}
    client = TestClient(app)
    preview = client.post("/api/publish/schedules/preview", json=payload, headers=headers)
    saved = client.patch("/api/publish/jobs/schedule-batch", json=payload, headers=headers)
    assert preview.status_code == 200
    assert saved.status_code == 200
    assert preview.json()["schedule"] == saved.json()["schedule"]


def test_frontend_uses_one_selection_semantic_and_no_schedule_reload():
    template = (settings.project_root / "app" / "templates" / "publish.html").read_text(encoding="utf-8")
    script = (settings.project_root / "app" / "static" / "js" / "publish-center.js").read_text(encoding="utf-8")
    assert "data-publish-select" in template
    assert "data-publish-schedule-checkbox" not in template
    assert "data-send-job-checkbox" not in template
    assert "window.location.reload" not in script
    assert "/api/publish/schedules/preview" in script


def test_run_once_module_command(tmp_path):
    db_path = tmp_path / "run_once.sqlite3"
    env = {
        **dict(__import__("os").environ), "DATABASE_PATH": str(db_path),
        "DATA_DIR": str(tmp_path / "data"), "TASKS_DIR": str(tmp_path / "tasks"),
        "STORAGE_ROOT": str(tmp_path / "tasks"), "PUBLISH_SCHEDULER_ENABLED": "false",
    }
    result = subprocess.run(
        [sys.executable, "-m", "app.publish_scheduler", "run-once"], cwd=settings.project_root,
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0
    assert "matched_count" in result.stdout
