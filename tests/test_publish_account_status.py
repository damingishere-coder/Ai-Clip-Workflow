from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import publish_service
from app.services.publish_repository import PublishRepository
from app.services.publishers.worker_client import PublishWorkerClient
from scripts import publish_host_worker


def test_start_login_immediately_marks_account_pending(monkeypatch):
    state = {
        "id": "account-pending",
        "platform": "douyin",
        "login_status": "normal",
        "last_login_at": "2026-07-18T01:00:00Z",
    }

    monkeypatch.setattr(publish_service, "get_account", lambda _account_id: dict(state))
    monkeypatch.setattr(
        PublishWorkerClient,
        "start_login",
        lambda _self, _platform, _account_id: {"status": "started", "message": "登录窗口已打开"},
    )

    def update(_self, _account_id, status, message, **_kwargs):
        state.update({"login_status": status, "login_message": message})

    monkeypatch.setattr(PublishRepository, "update_account_status", update)
    result = publish_service.start_browser_account_login("account-pending")
    assert result["account"]["login_status"] == "login_pending"
    assert result["message"] == "登录窗口已打开"


def test_busy_account_check_is_not_mislabeled_as_expired(monkeypatch):
    state = {
        "id": "account-busy",
        "platform": "douyin",
        "login_status": "normal",
        "last_login_at": "2026-07-18T01:00:00Z",
    }
    monkeypatch.setattr(publish_service, "get_account", lambda _account_id: dict(state))
    monkeypatch.setattr(
        PublishWorkerClient,
        "check_account",
        lambda _self, _platform, _account_id: {"login_status": "busy", "message": "账号正在操作"},
    )

    def update(_self, _account_id, status, message, **_kwargs):
        state.update({"login_status": status, "login_message": message})

    monkeypatch.setattr(PublishRepository, "update_account_status", update)
    result = publish_service.check_browser_account("account-busy")
    assert result["account"]["login_status"] == "busy"


def test_worker_login_background_never_writes_sqlite(monkeypatch):
    class FakePublisher:
        def open_login(self, _account_id):
            return {"login_status": "normal", "message": "登录成功"}

    publisher = FakePublisher()
    monkeypatch.setattr(publish_host_worker, "get_platform_publisher", lambda *_args, **_kwargs: publisher)
    monkeypatch.setattr(
        PublishRepository,
        "update_account_status",
        lambda *_args, **_kwargs: pytest.fail("Windows Worker 不得直接写 SQLite"),
    )
    client = TestClient(publish_host_worker.create_worker_app(token="test-token"))
    headers = {"Authorization": "Bearer test-token"}

    response = client.post(
        "/v1/accounts/login",
        headers=headers,
        json={"platform": "douyin", "account_id": "account-background"},
    )
    assert response.status_code == 202


def test_worker_rejects_second_window_for_busy_account():
    lock = publish_host_worker._account_lock("douyin", "account-locked")
    assert lock.acquire(blocking=False)
    try:
        response = TestClient(publish_host_worker.create_worker_app(token="test-token")).post(
            "/v1/accounts/login",
            headers={"Authorization": "Bearer test-token"},
            json={"platform": "douyin", "account_id": "account-locked"},
        )
        assert response.status_code == 409
        assert "正在运行" in response.json()["detail"]
    finally:
        lock.release()
