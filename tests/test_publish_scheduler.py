from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.main import app
from app.models.task import PublishBatchScheduleUpdate, PublishJobCreate, PublishScheduleNextStartRequest
from app.services.auto_publish_service import create_auto_publish_jobs
from app.services.publish_domain import TARGET_PLATFORMS
from app.services.publish_readiness import SendReadinessBlocked
from app.services.publish_scheduler import PublishScheduler, build_batch_schedule_times
from app.services import publish_service


PREFIX = "test-real-publish-"


@pytest.fixture(autouse=True)
def clean_publish_data(tmp_path):
    init_db()
    original_export = settings.publish_scheduler_export_dir
    original_default_mode = settings.publish_default_mode
    original_stale = settings.publish_job_stale_minutes
    original_opencli_fallback = settings.publish_enable_opencli_fallback
    object.__setattr__(settings, "publish_scheduler_export_dir", tmp_path / "exports")
    object.__setattr__(settings, "publish_default_mode", "opencli_publish")
    object.__setattr__(settings, "publish_job_stale_minutes", 30)
    object.__setattr__(settings, "publish_enable_opencli_fallback", True)
    _cleanup()
    yield
    _cleanup()
    object.__setattr__(settings, "publish_scheduler_export_dir", original_export)
    object.__setattr__(settings, "publish_default_mode", original_default_mode)
    object.__setattr__(settings, "publish_job_stale_minutes", original_stale)
    object.__setattr__(settings, "publish_enable_opencli_fallback", original_opencli_fallback)


def _cleanup():
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _utc(delta_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _future_beijing_time(hour: int, *, minute: int = 0, days: int = 2) -> datetime:
    future_date = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=days)).date()
    return datetime(future_date.year, future_date.month, future_date.day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))


def _insert_job(
    tmp_path: Path,
    *,
    status: str = "SCHEDULED",
    publish_mode: str = "manual_export",
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
        return {"status": "ok", "confirmed": True, "message": "mock submitted", "job": _raw(job_id)}
    monkeypatch.setattr(publish_service, "execute_opencli_send_job", execute)


def test_platform_and_publish_mode_are_separate():
    assert TARGET_PLATFORMS == {"douyin": "抖音", "bilibili": "B站"}
    with pytest.raises(ValidationError):
        PublishJobCreate(task_id="task", output_clip_id="clip", platform="manual_export", title="标题")


def test_auto_pipeline_creates_only_metadata_target_platform(tmp_path):
    job_id = _insert_job(tmp_path, status="CANCELLED")
    seed = _raw(job_id)
    cover_path = tmp_path / "auto-pipeline-cover.jpg"
    cover_path.write_bytes(b"cover")
    result = create_auto_publish_jobs(
        {"id": seed["task_id"], "platform": "general"},
        [{
            "output_clip": {"id": seed["output_clip_id"], "output_file_path": seed["video_file_path"]},
            "cover": {
                "cover_file_path": str(cover_path),
                "cover_time_seconds": 15,
                "cover_source": "ai_frame",
            },
            "metadata": {"platform": "bilibili", "title": "自动标题", "caption": "自动正文", "hashtags": ["自动"], "risk_flags": []},
            "scheduled_at": "",
        }],
        subtitle_delivery_mode="original",
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
        interval_minutes=180, daily_start_time="09:00", daily_end_time="21:00", reject_past=False,
    )
    assert result == ["2026-07-12T01:00:00+00:00"]


def test_daily_window_overflow_moves_to_next_local_day():
    result = build_batch_schedule_times(
        3, start_at_local="2026-07-12T20:00", timezone_name="Asia/Shanghai",
        interval_minutes=180, daily_start_time="09:00", daily_end_time="21:00", reject_past=False,
    )
    assert result == ["2026-07-12T12:00:00+00:00", "2026-07-13T01:00:00+00:00", "2026-07-13T04:00:00+00:00"]


def test_schedule_request_defaults_use_seven_to_midnight():
    batch = PublishBatchScheduleUpdate(job_ids=["job-1"])
    next_start = PublishScheduleNextStartRequest(job_ids=["job-1"], platform="douyin")
    assert (batch.daily_start_time, batch.daily_end_time) == ("07:00", "00:00")
    assert (next_start.daily_start_time, next_start.daily_end_time) == ("07:00", "00:00")


def test_next_schedule_start_uses_current_platform_and_excludes_selected_jobs(tmp_path):
    selected_time = _future_beijing_time(23)
    selected = _insert_job(tmp_path, status="WAITING", scheduled_at=selected_time.astimezone(timezone.utc).isoformat(timespec="seconds"))
    latest_time = _future_beijing_time(19)
    latest = _insert_job(tmp_path, status="WAITING", scheduled_at=latest_time.astimezone(timezone.utc).isoformat(timespec="seconds"))
    _insert_job(
        tmp_path,
        status="SCHEDULED",
        platform="bilibili",
        scheduled_at=_future_beijing_time(23).astimezone(timezone.utc).isoformat(timespec="seconds"),
    )
    _insert_job(
        tmp_path,
        status="PUBLISHED",
        scheduled_at=_future_beijing_time(23).astimezone(timezone.utc).isoformat(timespec="seconds"),
    )

    result = PublishScheduler().next_batch_schedule_start(
        [selected],
        platform="douyin",
        timezone_name="Asia/Shanghai",
        interval_minutes=180,
        daily_start_time="07:00",
        daily_end_time="00:00",
    )

    assert result["status"] == "ok"
    assert result["latest_job_id"] == latest
    assert result["latest_scheduled_at_local_display"].endswith(" 19:00")
    assert result["next_start_at_local_display"].endswith(" 22:00")


@pytest.mark.parametrize(
    ("latest_hour", "expected_day_offset", "expected_hour"),
    [(21, 1, 0), (22, 1, 7)],
)
def test_next_schedule_start_respects_cross_midnight_window(
    tmp_path,
    latest_hour,
    expected_day_offset,
    expected_hour,
):
    selected = _insert_job(tmp_path, status="WAITING", scheduled_at="")
    latest_time = _future_beijing_time(latest_hour)
    _insert_job(
        tmp_path,
        status="SCHEDULED",
        scheduled_at=latest_time.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )
    result = PublishScheduler().next_batch_schedule_start(
        [selected],
        platform="douyin",
        interval_minutes=180,
        daily_start_time="07:00",
        daily_end_time="00:00",
    )
    expected = latest_time.date() + timedelta(days=expected_day_offset)
    assert result["next_start_at_local_display"] == f"{expected:%Y-%m-%d} {expected_hour:02d}:00"


def test_next_schedule_start_returns_empty_without_other_future_schedule(tmp_path):
    selected = _insert_job(tmp_path, status="WAITING", scheduled_at="")
    result = PublishScheduler().next_batch_schedule_start([selected], platform="douyin")
    assert result["status"] == "empty"
    assert result["next_start_at_local"] == ""
    assert "手动选择" in result["message"]


def test_next_schedule_start_api_and_platform_isolation(tmp_path):
    selected = _insert_job(tmp_path, status="WAITING", scheduled_at="")
    latest_time = _future_beijing_time(19)
    _insert_job(
        tmp_path,
        status="SCHEDULED",
        scheduled_at=latest_time.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )
    headers = {"Authorization": f"Bearer {settings.local_admin_token}"} if settings.local_admin_token else {}
    payload = {
        "job_ids": [selected],
        "platform": "douyin",
        "timezone": "Asia/Shanghai",
        "interval_minutes": 180,
        "daily_start_time": "07:00",
        "daily_end_time": "00:00",
    }
    client = TestClient(app)
    response = client.post("/api/publish/schedules/next-start", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["next_start_at_local_display"].endswith(" 22:00")
    assert response.json()["next_start_at_local"].endswith("T22:00")
    assert "+" not in response.json()["next_start_at_local"]

    bilibili = _insert_job(tmp_path, status="WAITING", platform="bilibili", scheduled_at="")
    response = client.post(
        "/api/publish/schedules/next-start",
        json={**payload, "job_ids": [bilibili]},
        headers=headers,
    )
    assert response.status_code == 409
    assert "当前平台与所选任务不一致" in response.json()["detail"]


def test_scheduled_manual_export_job_can_run_now(tmp_path):
    job_id = _insert_job(tmp_path, status="SCHEDULED", scheduled_at=_utc(3600))
    result = PublishScheduler().publish_now(job_id)
    assert result["status"] == "scheduled"
    PublishScheduler().run_once()
    assert _raw(job_id)["status"] == "EXPORTED"


def test_due_legacy_opencli_job_moves_to_review_once_without_being_claimed(monkeypatch, tmp_path):
    calls = []
    _mock_opencli_success(monkeypatch, calls)
    job_id = _insert_job(tmp_path, publish_mode="opencli_publish")
    result = PublishScheduler().execute_job(job_id)
    assert calls == []
    assert result["error_code"] == "legacy_schedule_requires_confirmation"
    assert _raw(job_id)["status"] == "NEED_REVIEW"
    with get_connection() as connection:
        event_count = connection.execute(
            "SELECT COUNT(*) FROM publish_job_events WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
    repeated = PublishScheduler().execute_job(job_id)
    assert repeated["status"] == "skipped"
    with get_connection() as connection:
        repeated_event_count = connection.execute(
            "SELECT COUNT(*) FROM publish_job_events WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
    assert repeated_event_count == event_count


def test_publish_now_legacy_without_account_is_blocked_before_status_change(tmp_path):
    job_id = _insert_job(tmp_path, publish_mode="opencli_publish")
    with pytest.raises(SendReadinessBlocked) as caught:
        PublishScheduler().publish_now(job_id)
    assert caught.value.readiness["action"] == "create_account"
    assert _raw(job_id)["status"] == "SCHEDULED"


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


def test_two_schedulers_cannot_claim_same_job(tmp_path):
    job_id = _insert_job(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: PublishScheduler().execute_job(job_id), range(2)))
    assert sorted(item["status"] for item in results) == ["exported", "skipped"]
    assert _raw(job_id)["status"] == "EXPORTED"


def test_only_stale_publishing_job_is_recovered(tmp_path):
    stale = _insert_job(tmp_path, status="PUBLISHING", updated_at=_utc(-3600))
    fresh = _insert_job(tmp_path, status="PUBLISHING", updated_at=_utc(-60))
    recovered = PublishScheduler().recover_interrupted_jobs()
    assert recovered == 1
    assert _raw(stale)["status"] == "NEED_REVIEW"
    assert _raw(fresh)["status"] == "PUBLISHING"


def test_finished_manual_review_execution_is_reconciled_without_stale_wait(tmp_path):
    job_id = _insert_job(tmp_path, status="PUBLISHING", updated_at=_utc())
    with get_connection() as connection:
        connection.execute(
            "UPDATE publish_jobs SET execution_id = ?, execution_phase = 'manual_review_waiting' WHERE id = ?",
            ("execution-manual-review", job_id),
        )
        connection.commit()

    class FinishedWorker:
        @staticmethod
        def execution(_execution_id):
            return {
                "phase": "manual_review",
                "identity": {
                    "job_id": job_id,
                    "platform": str(_raw(job_id).get("platform") or ""),
                    "account_id": str(_raw(job_id).get("account_id") or ""),
                },
                "details": {
                    "outcome": "NEED_REVIEW",
                    "message": "上传状态需要人工确认",
                    "remote_video_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "provider_response": {},
                    "error_code": "video_upload_timeout",
                    "needs_manual_review": True,
                },
            }

    recovered = PublishScheduler(worker_client=FinishedWorker()).recover_interrupted_jobs()

    assert recovered == 1
    job = _raw(job_id)
    assert job["status"] == "NEED_REVIEW"
    assert job["error_code"] == "video_upload_timeout"


def test_confirmed_success_execution_is_recovered_once_without_republishing(tmp_path):
    job_id = _insert_job(tmp_path, status="PUBLISHING", updated_at=_utc())
    with get_connection() as connection:
        connection.execute(
            "UPDATE publish_jobs SET execution_id = ?, execution_phase = 'claimed' WHERE id = ?",
            ("execution-confirmed-success", job_id),
        )
        connection.commit()

    class FinishedWorker:
        calls = 0

        @classmethod
        def execution(cls, _execution_id):
            cls.calls += 1
            return {
                "phase": "confirmed_success",
                "identity": {
                    "job_id": job_id,
                    "platform": str(_raw(job_id).get("platform") or ""),
                    "account_id": str(_raw(job_id).get("account_id") or ""),
                },
                "details": {
                    "outcome": "PUBLISHED",
                    "message": "投稿成功",
                    "remote_video_id": "video-1",
                    "platform_url": "https://www.douyin.com/video/video-1",
                    "published_at": _utc(),
                    "provider_response": {},
                    "error_code": "",
                    "needs_manual_review": False,
                },
            }

    publish_calls: list[str] = []
    scheduler = PublishScheduler(
        worker_client=FinishedWorker(),
        executor=lambda job_id, **_kwargs: publish_calls.append(job_id),
    )

    assert scheduler.recover_interrupted_jobs() == 1
    assert scheduler.recover_interrupted_jobs() == 0
    assert _raw(job_id)["status"] == "PUBLISHED"
    assert FinishedWorker.calls == 1
    assert publish_calls == []


def test_recovery_schedule_keeps_18_jobs_in_order_on_two_hour_grid():
    scheduled = build_batch_schedule_times(
        18,
        start_at_local="2026-07-29T21:00:00+08:00",
        timezone_name="Asia/Shanghai",
        interval_minutes=120,
        daily_start_time="09:00",
        daily_end_time="21:00",
        reject_past=False,
    )
    local = [
        datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai"))
        for value in scheduled
    ]

    assert len(local) == 18
    assert local[0].isoformat(timespec="minutes") == "2026-07-29T21:00+08:00"
    assert local[-1].isoformat(timespec="minutes") == "2026-08-01T13:00+08:00"
    assert [item.hour for item in local[:8]] == [21, 9, 11, 13, 15, 17, 19, 21]
    assert all(item.minute == 0 and 9 <= item.hour <= 21 for item in local)


def test_preview_and_save_use_identical_schedule(tmp_path):
    first = _insert_job(tmp_path, status="WAITING", scheduled_at="")
    second = _insert_job(tmp_path, status="WAITING", scheduled_at="")
    scheduler = PublishScheduler()
    future_day = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=2)).strftime("%Y-%m-%d")
    params = dict(
        start_at_local=f"{future_day}T20:00", timezone_name="Asia/Shanghai",
        interval_minutes=180, daily_start_time="09:00", daily_end_time="21:00",
    )
    preview = scheduler.preview_batch_schedule([first, second], **params)
    saved = scheduler.update_batch_schedule([first, second], action="apply", **params)
    assert saved["schedule"] == preview["schedule"]
    assert [_raw(first)["scheduled_at"], _raw(second)["scheduled_at"]] == [
        item["scheduled_at_utc"] for item in preview["schedule"]
    ]


def test_schedule_preview_api_matches_save_api(tmp_path):
    job_ids = [_insert_job(tmp_path, status="WAITING", scheduled_at="") for _ in range(10)]
    future_day = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=2)).strftime("%Y-%m-%d")
    following_day = (
        datetime.strptime(future_day, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    payload = {
        "job_ids": job_ids, "action": "apply", "start_at_local": f"{future_day}T06:00",
        "timezone": "Asia/Shanghai", "interval_minutes": 180,
        "daily_start_time": "06:00", "daily_end_time": "00:00",
    }
    headers = {"Authorization": f"Bearer {settings.local_admin_token}"} if settings.local_admin_token else {}
    client = TestClient(app)
    preview = client.post("/api/publish/schedules/preview", json=payload, headers=headers)
    saved = client.patch("/api/publish/jobs/schedule-batch", json=payload, headers=headers)
    assert preview.status_code == 200
    assert saved.status_code == 200
    assert preview.json()["schedule"] == saved.json()["schedule"]
    assert [item["scheduled_at_local_display"] for item in preview.json()["schedule"]] == [
        f"{future_day} 06:00",
        f"{future_day} 09:00",
        f"{future_day} 12:00",
        f"{future_day} 15:00",
        f"{future_day} 18:00",
        f"{future_day} 21:00",
        f"{following_day} 00:00",
        f"{following_day} 06:00",
        f"{following_day} 09:00",
        f"{following_day} 12:00",
    ]


def test_frontend_uses_one_selection_semantic_and_no_schedule_reload():
    template = (settings.project_root / "app" / "templates" / "publish.html").read_text(encoding="utf-8")
    script = (settings.project_root / "app" / "static" / "js" / "publish-center.js").read_text(encoding="utf-8")
    assert "data-publish-select" in template
    assert "data-publish-schedule-checkbox" not in template
    assert "data-send-job-checkbox" not in template
    assert "window.location.reload" not in script
    assert "/api/publish/schedules/preview" in script
    assert "/api/publish/schedules/next-start" in script
    assert "data-schedule-feedback" in template
    assert "data-use-latest-schedule" in template
    assert "正在生成预览…" in script
    assert 'scheduleForm?.addEventListener("input", () =>' in script


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
