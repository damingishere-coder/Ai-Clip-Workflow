from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.routers import publish as publish_router
from app.services.publish_readiness import PublishPlatformIsolationBlocked, SendReadinessBlocked


def _headers() -> dict[str, str]:
    if settings.local_admin_token:
        return {"Authorization": f"Bearer {settings.local_admin_token}"}
    return {}


def test_publish_now_api_only_schedules_then_wakes_same_scheduler(monkeypatch):
    events: list[tuple[str, str]] = []

    class FakeScheduler:
        def publish_now(self, job_id: str) -> dict:
            events.append(("publish_now", job_id))
            return {"status": "scheduled", "job_id": job_id, "scheduled_at": "2026-07-15T02:00:00+00:00"}

        def run_once(self) -> dict:
            events.append(("run_once", ""))
            return {"status": "ok"}

    monkeypatch.setattr(publish_router, "PublishScheduler", FakeScheduler)
    response = TestClient(app).post("/api/publish/jobs/job-1/publish-now", headers=_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "scheduled"
    assert events == [("publish_now", "job-1"), ("run_once", "")]


def test_past_single_schedule_is_rejected_by_api():
    response = TestClient(app).patch(
        "/api/publish/jobs/not-present/schedule",
        headers=_headers(),
        json={"scheduled_at": "2000-01-01T09:00", "timezone": "Asia/Shanghai"},
    )
    assert response.status_code == 400
    assert "晚于当前时间" in response.json()["detail"]


def test_publish_now_returns_structured_readiness_block(monkeypatch):
    readiness = {
        "ready": False,
        "dispatch_ready": False,
        "message": "账号尚未登录",
        "action": "login_account",
        "issues": [{"code": "account_login_required", "action": "login_account"}],
    }

    class BlockedScheduler:
        def publish_now(self, job_id: str) -> dict:
            raise SendReadinessBlocked(readiness)

    monkeypatch.setattr(publish_router, "PublishScheduler", BlockedScheduler)
    response = TestClient(app).post("/api/publish/jobs/job-1/publish-now", headers=_headers())
    assert response.status_code == 409
    assert response.json()["detail"] == readiness


def test_retry_now_api_runs_preflight_and_wakes_scheduler(monkeypatch):
    events: list[tuple[str, str]] = []

    class FakeScheduler:
        def retry_failed(self, job_id: str, _scheduled_at=None, *, visibility=None) -> dict:
            events.append(("retry_failed", f"{job_id}:{visibility}"))
            return {"status": "scheduled", "job_id": "replacement-job", "retry_of_job_id": job_id}

        def run_once(self) -> dict:
            events.append(("run_once", ""))
            return {"status": "ok"}

    monkeypatch.setattr(publish_router, "PublishScheduler", FakeScheduler)
    response = TestClient(app).post(
        "/api/publish/jobs/failed-job/retry",
        headers=_headers(),
        json={"visibility": "private"},
    )
    assert response.status_code == 200
    assert response.json()["job_id"] == "replacement-job"
    assert events == [("retry_failed", "failed-job:private"), ("run_once", "")]


def test_retry_now_returns_structured_readiness_block(monkeypatch):
    readiness = {
        "ready": False,
        "dispatch_ready": False,
        "message": "Windows 发布 Worker 未连接",
        "action": "start_worker",
        "issues": [{"code": "publish_worker_unavailable", "action": "start_worker"}],
    }

    class BlockedScheduler:
        def retry_failed(self, _job_id: str, _scheduled_at=None, *, visibility=None) -> dict:
            raise SendReadinessBlocked(readiness)

    monkeypatch.setattr(publish_router, "PublishScheduler", BlockedScheduler)
    response = TestClient(app).post(
        "/api/publish/jobs/failed-job/retry",
        headers=_headers(),
        json={"visibility": "public"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == readiness


def test_review_cannot_be_marked_published_without_platform_evidence():
    response = TestClient(app).post(
        "/api/publish/jobs/not-present/mark-published",
        headers=_headers(),
        json={"platform_url": ""},
    )
    assert response.status_code in {400, 422}


def test_mixed_platform_target_batch_returns_conflict(monkeypatch):
    def blocked(_payload):
        raise PublishPlatformIsolationBlocked("抖音和 B站任务不能混合操作")

    monkeypatch.setattr(publish_router.publish_service, "update_publish_jobs_target_batch", blocked)
    response = TestClient(app).patch(
        "/api/publish/jobs/target-batch",
        headers=_headers(),
        json={
            "job_ids": ["douyin-job", "bilibili-job"],
            "platform": "douyin",
            "account_id": "account-1",
            "publish_mode": "local_browser",
        },
    )
    assert response.status_code == 409
    assert "不能混合" in response.json()["detail"]


def test_backfill_covers_api_returns_batch_result(monkeypatch):
    expected = {
        "status": "ok",
        "message": "已补齐",
        "generated_cover_count": 1,
        "reused_cover_count": 0,
        "updated_job_count": 2,
        "failed_clip_count": 0,
        "errors": [],
        "jobs": [],
    }
    monkeypatch.setattr(publish_router.publish_service, "backfill_missing_publish_covers", lambda: expected)
    response = TestClient(app).post("/api/publish/covers/backfill", headers=_headers())
    assert response.status_code == 200
    assert response.json() == expected
