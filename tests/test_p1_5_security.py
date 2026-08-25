import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.main import app
from app.models.settings import AIConfigUpdate
from app.models.task import PublishPlatformConfigUpdate, TaskStatus, TaskStatusUpdate
from app.services import ai_config_service, publish_service, task_query_service
from app.services.publish_domain import safe_platform_url, validate_platform_url
from app.services.publish_scheduler import queue_snapshot
from app.services.publishers.base import PublishOutcome, PublishResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRET_SENTINEL = "p1-5-secret-sentinel-value"


def _fake_ai_values() -> dict[str, str]:
    values = {key: "" for key in ai_config_service.SETTING_ATTRS}
    values.update(
        {
            "AI_DEFAULT_PROVIDER": "remote",
            "AI_PUBLISH_PROVIDER": "remote",
            "AI_REQUEST_TIMEOUT_SECONDS": "120",
            "AI_CODEX_PATH": "codex",
            "AI_CODEX_HOME": "",
            "AI_CODEX_MODEL": "gpt-5.6-sol",
            "AI_CODEX_TIMEOUT_SECONDS": "300",
            "TRANSCRIPTION_PROVIDER": "volcengine",
            "TRANSCRIPTION_FALLBACK_PROVIDER": "",
            "VOLCENGINE_ASR_API_URL": "https://example.invalid/asr",
            "VOLCENGINE_ASR_RESOURCE_ID": "volc.bigasr.auc_turbo",
            "VOLCENGINE_ASR_TIMEOUT_SECONDS": "300",
            "VOLCENGINE_ASR_AUDIO_FORMAT": "mp3",
            "AI_ANALYSIS_REMOTE_BASE_URL": "https://example.invalid/v1",
            "AI_ANALYSIS_REMOTE_MODEL": "analysis-model",
            "AI_ANALYSIS_REMOTE_PROTOCOL": "chat_completions",
            "AI_ANALYSIS_REMOTE_REASONING_EFFORT": "",
            "AI_ANALYSIS_REMOTE_RESPONSES_PATH": "/v1/responses",
            "AI_ANALYSIS_REMOTE_DISABLE_RESPONSE_STORAGE": "true",
            "AI_ANALYSIS_REQUEST_TIMEOUT_SECONDS": "120",
            "AI_PUBLISH_REMOTE_BASE_URL": "https://example.invalid/v1",
            "AI_PUBLISH_REMOTE_MODEL": "publish-model",
            "AI_PUBLISH_REMOTE_PROTOCOL": "chat_completions",
            "AI_PUBLISH_REMOTE_REASONING_EFFORT": "",
            "AI_PUBLISH_REMOTE_RESPONSES_PATH": "/v1/responses",
            "AI_PUBLISH_REMOTE_DISABLE_RESPONSE_STORAGE": "true",
            "AI_PUBLISH_REQUEST_TIMEOUT_SECONDS": "120",
            "AI_LOCAL_BASE_URL": "http://127.0.0.1:11434/v1",
            "AI_LOCAL_MODEL": "qwen3:8b",
            "AI_LOCAL_PROTOCOL": "chat_completions",
            "AI_LOCAL_FALLBACK_PROTOCOL": "",
            "AI_LOCAL_HEALTH_TIMEOUT_SECONDS": "30",
            "AI_NETWORK_ACCESS": "enabled",
            "AI_WINDOWS_WSL_SETUP_ACKNOWLEDGED": "true",
            "AI_MODEL_CONTEXT_WINDOW": "1000000",
            "AI_MODEL_AUTO_COMPACT_TOKEN_LIMIT": "900000",
        }
    )
    for key in ai_config_service.SECRET_SETTING_KEYS:
        values[key] = f"{SECRET_SENTINEL}-{key.lower()}"
    return values


def _safe_context(monkeypatch) -> dict:
    monkeypatch.setattr(ai_config_service, "_current_config_values", _fake_ai_values)
    monkeypatch.setattr(ai_config_service, "fetch_ollama_models", lambda **_kwargs: [])
    monkeypatch.setattr(
        ai_config_service.CodexCliProvider,
        "version_status",
        lambda _self: {"ok": True, "version": "test", "detail": "测试可用"},
    )
    return ai_config_service.get_ai_config_context()


def test_ai_config_context_never_returns_secret_or_env_path(monkeypatch) -> None:
    context = _safe_context(monkeypatch)
    serialized = json.dumps(context, ensure_ascii=False)

    assert SECRET_SENTINEL not in serialized
    assert "env_path" not in context
    for key in ai_config_service.SECRET_SETTING_KEYS:
        assert context["values"][key] == ""
        assert context["secret_configured"][key] is True


def test_system_page_does_not_render_admin_or_provider_secrets(monkeypatch) -> None:
    context = _safe_context(monkeypatch)
    monkeypatch.setattr(task_query_service, "get_ai_config_context", lambda: context)
    previous_token = settings.local_admin_token
    object.__setattr__(settings, "local_admin_token", f"{SECRET_SENTINEL}-admin")
    try:
        response = TestClient(app).get("/system")
    finally:
        object.__setattr__(settings, "local_admin_token", previous_token)

    assert response.status_code == 200
    assert SECRET_SENTINEL not in response.text
    assert 'meta name="local-admin-token"' not in response.text
    assert 'name="ai_analysis_remote_api_key" type="password" value=""' in response.text
    assert "项目本地 .env" in response.text


def test_blank_secret_fields_keep_existing_values_without_returning_them(monkeypatch) -> None:
    current = _fake_ai_values()
    captured: dict[str, str] = {}
    monkeypatch.setattr(ai_config_service, "_current_config_values", lambda: current)
    monkeypatch.setattr(ai_config_service, "_write_env_values", lambda values: captured.update(values))
    monkeypatch.setattr(ai_config_service, "_apply_runtime_values", lambda _values: None)
    monkeypatch.setattr(
        ai_config_service,
        "get_ai_config_context",
        lambda: {"values": ai_config_service._public_config_values(current)},
    )

    response = ai_config_service.save_ai_config(AIConfigUpdate())

    for key in ai_config_service.SECRET_SETTING_KEYS:
        assert captured[key] == current[key]
    assert SECRET_SENTINEL not in json.dumps(response, ensure_ascii=False)


def test_publish_config_and_account_dtos_remove_raw_secrets() -> None:
    config = publish_service._normalize_config(
        {
            "platform": "douyin",
            "client_key": "public-client-key",
            "client_secret": f"{SECRET_SENTINEL}-client",
        }
    )
    account = publish_service._normalize_account(
        {
            "platform": "douyin",
            "login_status": "normal",
            "authorization_status": "authorized",
            "access_token": f"{SECRET_SENTINEL}-access",
            "refresh_token": f"{SECRET_SENTINEL}-refresh",
        }
    )

    assert "client_secret" not in config
    assert "access_token" not in account
    assert "refresh_token" not in account
    assert config["client_secret_masked"]
    assert account["access_token_masked"]
    assert account["refresh_token_masked"]
    assert SECRET_SENTINEL not in json.dumps({"config": config, "account": account}, ensure_ascii=False)


def test_publish_result_dto_redacts_nested_provider_secrets() -> None:
    result = PublishResult(
        outcome=PublishOutcome.PUBLISHED,
        message=f"upstream Authorization: Bearer {SECRET_SENTINEL}-bearer",
        provider_response={
            "access_token": f"{SECRET_SENTINEL}-access",
            "Set-Cookie": f"{SECRET_SENTINEL}-cookie-header",
            "provider_access_token": f"{SECRET_SENTINEL}-provider-access",
            "nested": {"cookie": f"{SECRET_SENTINEL}-cookie", "safe": "visible"},
        },
    )

    payload = result.as_dict()

    assert payload["provider_response"]["access_token"] == "[REDACTED]"
    assert payload["provider_response"]["Set-Cookie"] == "[REDACTED]"
    assert payload["provider_response"]["provider_access_token"] == "[REDACTED]"
    assert payload["provider_response"]["nested"]["cookie"] == "[REDACTED]"
    assert payload["provider_response"]["nested"]["safe"] == "visible"
    assert "[REDACTED]" in payload["message"]
    assert SECRET_SENTINEL not in json.dumps(payload, ensure_ascii=False)


def test_oauth_account_helper_never_returns_token_response(monkeypatch) -> None:
    monkeypatch.setattr(publish_service, "_validate_and_consume_oauth_state", lambda *_args: True)
    monkeypatch.setattr(
        publish_service,
        "_get_platform_config_record",
        lambda _platform: {"platform": "douyin", "client_key": "client", "client_secret": "secret"},
    )
    monkeypatch.setattr(
        publish_service.DouyinPublishProvider,
        "exchange_code",
        lambda _self, _code: {
            "data": {
                "nickname": "安全账号",
                "access_token": f"{SECRET_SENTINEL}-access",
                "refresh_token": f"{SECRET_SENTINEL}-refresh",
            }
        },
    )
    monkeypatch.setattr(
        publish_service,
        "create_account",
        lambda payload: {"status": "ok", "account": {"account_name": payload.account_name}},
    )

    result = publish_service.save_douyin_oauth_account("oauth-code", state="valid-state")

    assert "provider_response" not in result
    assert SECRET_SENTINEL not in json.dumps(result, ensure_ascii=False)


def test_legacy_publish_job_dto_redacts_raw_provider_fields() -> None:
    job = publish_service._normalize_job(
        {
            "id": "legacy-secret-job",
            "platform": "douyin",
            "status": "FAILED",
            "provider_response": json.dumps({"token": SECRET_SENTINEL, "safe": "visible"}),
            "publish_result": json.dumps({"provider_response": {"cookie": SECRET_SENTINEL}}),
        }
    )

    assert "provider_response" not in job
    assert "publish_result" not in job
    assert job["provider_payload"]["token"] == "[REDACTED]"
    assert job["provider_payload"]["safe"] == "visible"
    assert job["publish_result_payload"]["provider_response"]["cookie"] == "[REDACTED]"
    assert SECRET_SENTINEL not in json.dumps(job, ensure_ascii=False)

    malformed = publish_service._normalize_job(
        {
            "id": "legacy-malformed-secret-job",
            "platform": "douyin",
            "status": "FAILED",
            "provider_response": f'{{"access_token":"{SECRET_SENTINEL}',
        }
    )
    assert malformed["provider_payload"] == {"invalid_payload": True}
    assert SECRET_SENTINEL not in json.dumps(malformed, ensure_ascii=False)


def test_queue_snapshot_redacts_legacy_provider_fields(monkeypatch) -> None:
    job_id = "test-p1-5-queue-secret"
    now = "2026-08-25T00:00:00+00:00"
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE publish_jobs (
            id TEXT, task_id TEXT, platform TEXT, status TEXT,
            provider_response TEXT, publish_result TEXT, platform_url TEXT,
            scheduled_at TEXT, created_at TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO publish_jobs (
            id, task_id, platform, status, provider_response, publish_result, created_at
        ) VALUES (?, 'test-task', 'douyin', 'FAILED', ?, ?, ?)
        """,
        (
            job_id,
            f'{{"access_token":"{SECRET_SENTINEL}',
            json.dumps({"provider_response": {"cookie": SECRET_SENTINEL}}),
            now,
        ),
    )
    connection.commit()
    monkeypatch.setattr("app.services.publish_scheduler.get_connection", lambda: connection)

    try:
        job = next(item for item in queue_snapshot()["all"] if item["id"] == job_id)
    finally:
        connection.close()

    assert "provider_response" not in job
    assert "publish_result" not in job
    assert job["provider_payload"] == {"invalid_payload": True}
    assert job["publish_result_payload"]["provider_response"]["cookie"] == "[REDACTED]"
    assert SECRET_SENTINEL not in json.dumps(job, ensure_ascii=False)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ai_codex_model", "safe-model\nINJECTED_CONFIG=1"),
        ("ai_analysis_remote_base_url", "javascript:alert(1)"),
        ("ai_publish_remote_base_url", "http://api.example.com/v1"),
        ("ai_local_base_url", "http://169.254.169.254/latest/meta-data"),
        ("volcengine_asr_api_url", "file:///etc/passwd"),
        ("ai_analysis_remote_responses_path", "//evil.example/v1/responses"),
    ],
)
def test_ai_config_rejects_unsafe_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        AIConfigUpdate(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("client_secret", "safe\nINJECTED=1"),
        ("api_base_url", "http://api.example.com/v1"),
        ("auth_url", "javascript:alert(1)"),
        ("token_url", "https://user:pass@api.example.com/token"),
        ("upload_url", "https://api.example.com/upload?token=unsafe"),
    ],
)
def test_publish_platform_config_rejects_unsafe_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        PublishPlatformConfigUpdate(**{field: value})


def test_task_status_error_message_has_bounded_length() -> None:
    with pytest.raises(ValidationError):
        TaskStatusUpdate(status=TaskStatus.failed, error_message="x" * 2001)


def test_validation_response_does_not_echo_rejected_secret(monkeypatch) -> None:
    called = False

    def unexpected_save(_payload):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr("app.routers.settings.save_ai_config", unexpected_save)
    response = TestClient(app).post(
        "/api/settings/ai",
        json={"ai_analysis_remote_api_key": f"{SECRET_SENTINEL}\nINJECTED=1"},
    )

    assert response.status_code == 422
    assert called is False
    assert SECRET_SENTINEL not in response.text
    assert '"input"' not in response.text


def test_local_gate_rejects_remote_host_and_accepts_bearer_token() -> None:
    client = TestClient(app)
    previous_token = settings.local_admin_token
    try:
        object.__setattr__(settings, "local_admin_token", "")
        assert client.get("/api/tasks", headers={"Host": "lan.example"}).status_code == 403
        assert client.get("/system", headers={"Host": "lan.example"}).status_code == 403
        assert client.get("/media/tasks/missing/source-video", headers={"Host": "lan.example"}).status_code == 403

        object.__setattr__(settings, "local_admin_token", "test-admin-token")
        assert client.get("/api/tasks", headers={"Host": "lan.example"}).status_code == 401
        assert client.get(
            "/api/tasks",
            headers={"Host": "lan.example", "Authorization": "Bearer wrong-token"},
        ).status_code == 401
        authorized = client.get(
            "/api/tasks",
            headers={"Host": "lan.example", "Authorization": "Bearer test-admin-token"},
        )
        assert authorized.status_code == 200
    finally:
        object.__setattr__(settings, "local_admin_token", previous_token)


def test_remote_peer_cannot_bypass_gate_with_loopback_host(monkeypatch) -> None:
    monkeypatch.delenv("NIUMA_TRUST_DOCKER_LOOPBACK_PROXY", raising=False)
    client = TestClient(app, client=("203.0.113.9", 50000))
    previous_token = settings.local_admin_token
    try:
        object.__setattr__(settings, "local_admin_token", "test-admin-token")
        assert client.get("/api/tasks", headers={"Host": "127.0.0.1"}).status_code == 401
        assert client.get(
            "/api/tasks",
            headers={"Host": "127.0.0.1", "Authorization": "Bearer test-admin-token"},
        ).status_code == 200
    finally:
        object.__setattr__(settings, "local_admin_token", previous_token)


def test_local_gate_keeps_health_public_and_blocks_cross_site_writes() -> None:
    client = TestClient(app, client=("127.0.0.1", 49152))
    assert client.get("/health", headers={"Host": "lan.example"}).status_code == 200
    same_origin = client.post(
        "/api/settings/ai",
        headers={"Host": "127.0.0.1:49152", "Origin": "http://127.0.0.1:49152"},
        json={},
    )
    assert same_origin.status_code != 403
    response = client.post(
        "/api/settings/ai",
        headers={"Host": "127.0.0.1", "Origin": "https://evil.example"},
        json={},
    )
    assert response.status_code == 403

    for external_origin in ("https://creator.douyin.com", "https://members.bilibili.com"):
        response = client.post(
            "/api/settings/ai",
            headers={"Host": "127.0.0.1", "Origin": external_origin},
            json={},
        )
        assert response.status_code == 403


def test_dynamic_frontend_text_is_not_written_with_inner_html() -> None:
    app_script = (PROJECT_ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    publish_script = (PROJECT_ROOT / "app/static/js/publish-center.js").read_text(encoding="utf-8")

    assert "summary.innerHTML" not in app_script
    assert "summary.replaceChildren(heading, detail)" in app_script
    assert "line.innerHTML" not in publish_script
    assert "title.textContent = `第 ${index + 1} 条" in publish_script
    assert "scheduledAt.textContent" in publish_script


@pytest.mark.parametrize(
    ("platform", "url"),
    [
        ("douyin", "javascript:alert(1)"),
        ("douyin", "data:text/html,unsafe"),
        ("douyin", "https://douyin.com.evil.example/video/1"),
        ("bilibili", "https://evil.example/?next=bilibili.com"),
    ],
)
def test_platform_url_rejects_dangerous_or_foreign_links(platform: str, url: str) -> None:
    with pytest.raises(ValueError):
        validate_platform_url(platform, url, allow_empty=False)
    assert safe_platform_url(platform, url) == ""


def test_platform_url_accepts_expected_domains() -> None:
    assert validate_platform_url("douyin", "https://www.douyin.com/video/1")
    assert validate_platform_url("bilibili", "https://www.bilibili.com/video/BV1")


def test_docker_port_is_bound_to_loopback() -> None:
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:8001:8001"' in compose
    assert '\n      - "8001:8001"' not in compose
    assert 'NIUMA_TRUST_DOCKER_LOOPBACK_PROXY: "true"' in compose
