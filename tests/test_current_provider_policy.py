from pathlib import Path
import json

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.settings import AIConfigUpdate
from app.services import ai_config_service as config
from app.services.ai.base import AIProviderError, ProviderConfig
from app.services.ai.ai_clip_analyzer import build_provider, build_remote_provider
from app.services.ai import diagnostics
from app.services.ai.local_model_provider import LocalModelProvider
from app.services.ai.remote_responses_provider import RemoteResponsesProvider
from app.services import transcript_service, ai_analysis_workflow_service
from app.services.local_transcription_runtime import TranscriptionOfflinePolicyError


@pytest.mark.parametrize(
    "payload",
    [
        {"ai_default_provider": "remote"},
        {"ai_publish_provider": "local"},
        {"ai_codex_model": "another-model"},
        {"ai_codex_home": "other-login"},
        {"ai_analysis_remote_api_key": "key"},
        {"transcription_provider": "volcengine"},
        {"transcription_offline_only": False},
        {"transcription_local_files_only": False},
    ],
)
def test_retired_settings_cannot_be_saved(payload):
    with pytest.raises(ValidationError):
        AIConfigUpdate(**payload)


def test_patch_preserves_legacy_credentials_and_home(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    original = "AI_ANALYSIS_REMOTE_API_KEY=old-secret\nAI_CODEX_HOME=C:/original/login\nAI_LOCAL_BASE_URL=http://legacy\n"
    env.write_text(original, encoding="utf-8")
    monkeypatch.setattr(config, "_env_path", lambda: env)
    monkeypatch.setattr(config, "_apply_runtime_values", lambda _: None)
    monkeypatch.setattr(config, "get_ai_config_context", lambda: {})
    config.save_ai_config(AIConfigUpdate(ai_codex_timeout_seconds=450))
    text = env.read_text(encoding="utf-8")
    assert original in text
    assert "AI_CODEX_TIMEOUT_SECONDS=450" in text
    assert "AI_PUBLISH_REMOTE" not in text


def test_current_settings_never_probe_or_expose_retired_providers(monkeypatch):
    monkeypatch.setattr(
        diagnostics, "fetch_ollama_models", lambda **kw: pytest.fail("retired probe")
    )
    monkeypatch.setattr(
        config.CodexCliProvider,
        "version_status",
        lambda self: {"ok": True, "version": "test", "detail": "可执行"},
    )
    monkeypatch.setattr(
        config.CodexCliProvider,
        "login_status",
        lambda self: {"ok": True, "detail": "已登录"},
    )
    monkeypatch.setattr(
        config, "get_local_transcription_runtime_status", lambda: {"ready": True}
    )
    data = config.get_ai_config_context()
    assert data["configured_count"] == 3
    assert not any(
        word in json.dumps(data)
        for word in ["REMOTE", "API_KEY", "VOLCENGINE", "AI_LOCAL", "AI_CODEX_HOME"]
    )


@pytest.mark.parametrize("provider", ["remote", "local"])
def test_analysis_and_factory_fail_before_side_effects(provider, monkeypatch):
    monkeypatch.setattr(
        ai_analysis_workflow_service,
        "get_connection",
        lambda: pytest.fail("must reject before DB writes"),
    )
    with pytest.raises(AIProviderError, match="已停用"):
        build_provider(provider)
    with pytest.raises(AIProviderError, match="已停用"):
        ai_analysis_workflow_service.queue_task_ai_analysis("missing", provider)
    assert (
        TestClient(app)
        .post(f"/api/tasks/missing/process/ai?provider={provider}")
        .status_code
        == 422
    )


def test_legacy_direct_adapters_and_diagnostics_make_no_request(monkeypatch):
    import app.services.ai.base as base

    monkeypatch.setattr(base, "urlopen", lambda *a, **kw: pytest.fail("network"))
    pc = ProviderConfig("https://legacy.invalid", "key", "old", "chat_completions", 10)
    for action in [
        lambda: RemoteResponsesProvider(pc).generate_json("x"),
        lambda: LocalModelProvider(pc).generate_json("x"),
        build_remote_provider,
        diagnostics.fetch_ollama_models,
        diagnostics.test_remote_json_generation,
        diagnostics.test_local_json_generation,
    ]:
        with pytest.raises(AIProviderError, match="已停用"):
            action()


def test_cloud_transcription_stays_blocked_even_if_legacy_switch_changes(monkeypatch):
    previous = settings.transcription_offline_only
    object.__setattr__(settings, "transcription_offline_only", False)
    monkeypatch.setattr(
        transcript_service, "urlopen", lambda *a, **kw: pytest.fail("cloud request")
    )
    try:
        with pytest.raises(TranscriptionOfflinePolicyError):
            transcript_service._request_volcengine_transcript({}, {}, allow_empty=False)
    finally:
        object.__setattr__(settings, "transcription_offline_only", previous)


def test_settings_template_has_only_effective_fields():
    text = (Path(__file__).parents[1] / "app/templates/system_status.html").read_text(
        encoding="utf-8"
    )
    for field in [
        "ai_analysis_remote",
        "ai_publish_remote",
        "volcengine_asr",
        "ai_local_",
        "ai_codex_home",
    ]:
        assert field not in text
    assert 'name="ai_codex_timeout_seconds"' in text
