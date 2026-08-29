from __future__ import annotations

import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from app.services.publishers.base import PublishOutcome, PublishWorkerUnavailable
from app.services.publishers.worker_client import PublishWorkerClient
from app.core.config import settings
from scripts.publish_host_worker import ExecutionJournal, _resolve_media_path, create_worker_app


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_worker_health_does_not_require_token():
    response = TestClient(create_worker_app(token="test-token")).get("/health")
    assert response.status_code == 200
    assert response.json()["worker"] == "windows_chrome"
    assert response.json()["token_configured"] is True


def test_protected_worker_health_requires_matching_token():
    client = TestClient(create_worker_app(token="test-token"))
    assert client.get("/v1/health").status_code == 401
    assert client.get(
        "/v1/health",
        headers={"Authorization": "Bearer wrong-token"},
    ).status_code == 401
    response = client.get(
        "/v1/health",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert response.json()["worker"] == "windows_chrome"


def test_protected_worker_endpoint_rejects_invalid_token():
    response = TestClient(create_worker_app(token="test-token")).post(
        "/v1/accounts/check",
        headers={"Authorization": "Bearer wrong-token"},
        json={"platform": "douyin", "account_id": "account-1"},
    )
    assert response.status_code == 401


def test_worker_client_sends_bearer_token_and_converts_result(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.headers.get("Authorization")
        captured["timeout"] = timeout
        return FakeResponse({
            "outcome": "PUBLISHED",
            "message": "成功",
            "remote_video_id": "BV123",
            "platform_url": "https://www.bilibili.com/video/BV123",
        })

    monkeypatch.setattr("app.services.publishers.worker_client.urlopen", fake_urlopen)
    result = PublishWorkerClient("http://127.0.0.1:8765", "secret-token", 7).publish(
        {"job_id": "job-1", "execution_id": "execution-1", "account_id": "account-1"}
    )
    assert result.outcome == PublishOutcome.PUBLISHED
    assert result.remote_video_id == "BV123"
    assert captured == {"authorization": "Bearer secret-token", "timeout": 7}


def test_worker_client_bypasses_environment_proxy_without_no_proxy(monkeypatch):
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"status": "ok", "worker": "windows_chrome"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            return

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = PublishWorkerClient(f"http://127.0.0.1:{server.server_port}", "token", 2).health()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result == {"status": "ok", "worker": "windows_chrome"}


def test_worker_timeout_is_marked_as_possibly_received(monkeypatch):
    def timeout(*_, **__):
        raise socket.timeout("timed out")

    monkeypatch.setattr("app.services.publishers.worker_client.urlopen", timeout)
    with pytest.raises(PublishWorkerUnavailable) as caught:
        PublishWorkerClient("http://127.0.0.1:8765", "token", 2).publish(
            {"job_id": "job-1", "execution_id": "execution-1", "account_id": "account-1"}
        )
    assert caught.value.request_may_have_been_received is True


def test_worker_connection_reset_after_publish_is_marked_as_possibly_received(monkeypatch):
    def reset(*_, **__):
        raise ConnectionResetError("connection reset after response")

    monkeypatch.setattr("app.services.publishers.worker_client.urlopen", reset)
    with pytest.raises(PublishWorkerUnavailable) as caught:
        PublishWorkerClient("http://127.0.0.1:8765", "token", 2).publish(
            {"job_id": "job-1", "execution_id": "execution-1", "account_id": "account-1"}
        )

    assert caught.value.request_may_have_been_received is True


def test_worker_offline_before_connection_is_safe_retry(monkeypatch):
    def offline(*_, **__):
        raise OSError("connection refused")

    monkeypatch.setattr("app.services.publishers.worker_client.urlopen", offline)
    with pytest.raises(PublishWorkerUnavailable) as caught:
        PublishWorkerClient("http://127.0.0.1:8765", "token", 2).health()
    assert caught.value.request_may_have_been_received is False
    assert "RunDock 中独立托管" in caught.value.message
    assert "127.0.0.1:8765" in caught.value.message
    assert r".\scripts" not in caught.value.message


def test_worker_client_uses_protected_health_endpoint(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers.get("Authorization")
        return FakeResponse({"status": "ok", "worker": "windows_chrome"})

    monkeypatch.setattr("app.services.publishers.worker_client.urlopen", fake_urlopen)
    result = PublishWorkerClient("http://127.0.0.1:8765", "secret-token", 2).health()
    assert result["status"] == "ok"
    assert captured == {
        "url": "http://127.0.0.1:8765/v1/health",
        "authorization": "Bearer secret-token",
    }


def test_worker_maps_docker_tasks_path_to_windows_storage(tmp_path):
    video = tmp_path / "project-a" / "05_clips" / "clip.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"fake video")
    original_tasks_dir = settings.tasks_dir
    original_allowed_roots = settings.publish_worker_allowed_roots
    object.__setattr__(settings, "tasks_dir", tmp_path)
    object.__setattr__(settings, "publish_worker_allowed_roots", "")
    try:
        resolved = _resolve_media_path(
            "/workspace/tasks/project-a/05_clips/clip.mp4",
            required=True,
        )
        assert resolved == str(video.resolve())
    finally:
        object.__setattr__(settings, "tasks_dir", original_tasks_dir)
        object.__setattr__(settings, "publish_worker_allowed_roots", original_allowed_roots)


def test_worker_execution_journal_persists_final_result_without_database(tmp_path):
    original_state_dir = settings.publish_worker_state_dir
    object.__setattr__(settings, "publish_worker_state_dir", tmp_path)
    try:
        journal = ExecutionJournal("execution-journal-test")
        journal.update(
            "confirmed_success",
            {"outcome": "PUBLISHED", "message": "投稿成功", "needs_manual_review": False},
        )
        stored = journal.read()
    finally:
        object.__setattr__(settings, "publish_worker_state_dir", original_state_dir)

    assert stored["phase"] == "confirmed_success"
    assert stored["details"]["outcome"] == "PUBLISHED"
    assert stored["details"]["message"] == "投稿成功"
