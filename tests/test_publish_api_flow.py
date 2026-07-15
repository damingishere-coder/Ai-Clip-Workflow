from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.routers import publish as publish_router


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


def test_review_cannot_be_marked_published_without_platform_evidence():
    response = TestClient(app).post(
        "/api/publish/jobs/not-present/mark-published",
        headers=_headers(),
        json={"platform_url": ""},
    )
    assert response.status_code in {400, 422}
