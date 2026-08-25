from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from app.services.ai import ai_clip_analyzer
from app.services.ai.ai_clip_analyzer import AIAnalysisError, AnalysisRequest
from app.services.ai.base import (
    AIProviderError,
    ProviderConfig,
    extract_chat_completion_text,
    generate_json_with_safe_retry,
    post_json,
)
from app.services.ai.local_model_provider import LocalModelProvider
from app.services.ai.variety_comedy_analyzer import _generate_payload
from app.services import transcript_service


class _Response:
    def __init__(self, body: str, headers: dict[str, str] | None = None):
        self._body = body.encode("utf-8")
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def _http_error(code: int, body: str = "error", headers: dict[str, str] | None = None) -> HTTPError:
    return HTTPError("https://provider.invalid", code, "error", headers or {}, BytesIO(body.encode("utf-8")))


def test_ai_transport_classifies_429_as_safe_retry(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai.base.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_http_error(429, headers={"Retry-After": "7"})),
    )
    with pytest.raises(AIProviderError) as captured:
        post_json("https://provider.invalid", {"x": 1}, "", 1)
    assert captured.value.category == "rate_limited"
    assert captured.value.safe_to_retry is True
    assert captured.value.billing_uncertain is False
    assert captured.value.retry_after_seconds == 7


def test_ai_transport_does_not_mark_5xx_or_timeout_safe(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai.base.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_http_error(503)),
    )
    with pytest.raises(AIProviderError) as server_error:
        post_json("https://provider.invalid", {}, "", 1)
    assert server_error.value.safe_to_retry is False
    assert server_error.value.billing_uncertain is True

    monkeypatch.setattr(
        "app.services.ai.base.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError(TimeoutError("timed out"))),
    )
    with pytest.raises(AIProviderError) as timeout_error:
        post_json("https://provider.invalid", {}, "", 1)
    assert timeout_error.value.category == "timeout"
    assert timeout_error.value.safe_to_retry is False
    assert timeout_error.value.billing_uncertain is True


def test_ai_transport_error_never_exposes_provider_body(monkeypatch):
    secret = "provider-secret-sentinel"
    monkeypatch.setattr(
        "app.services.ai.base.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _http_error(401, body=json.dumps({"authorization": secret, "prompt": secret}))
        ),
    )

    with pytest.raises(AIProviderError) as captured:
        post_json("https://provider.invalid", {}, "", 1)

    assert captured.value.http_status == 401
    assert secret not in str(captured.value)


def test_ai_transport_invalid_json_never_exposes_response_body(monkeypatch):
    secret = "invalid-json-secret-sentinel"
    monkeypatch.setattr("app.services.ai.base.urlopen", lambda *_args, **_kwargs: _Response(secret))

    with pytest.raises(AIProviderError) as captured:
        post_json("https://provider.invalid", {}, "", 1)

    assert captured.value.category == "invalid_response_json"
    assert secret not in str(captured.value)


def test_shared_retry_only_repeats_explicitly_safe_failures():
    class Provider:
        def __init__(self, *, safe: bool):
            self.calls = 0
            self.safe = safe

        def generate_json(self, _prompt: str) -> str:
            self.calls += 1
            if self.calls < 3:
                raise AIProviderError("temporary", safe_to_retry=self.safe, retry_after_seconds=0)
            return "{}"

    safe_provider = Provider(safe=True)
    assert generate_json_with_safe_retry(safe_provider, "prompt", sleep_fn=lambda _seconds: None) == "{}"
    assert safe_provider.calls == 3

    ambiguous_provider = Provider(safe=False)
    with pytest.raises(AIProviderError):
        generate_json_with_safe_retry(ambiguous_provider, "prompt", sleep_fn=lambda _seconds: None)
    assert ambiguous_provider.calls == 1


@pytest.mark.parametrize(
    "body, category",
    [("", "empty_response"), ("not-json", "invalid_response_json"), ("[]", "invalid_response_schema")],
)
def test_ai_transport_rejects_empty_or_invalid_json_without_retry(monkeypatch, body, category):
    monkeypatch.setattr("app.services.ai.base.urlopen", lambda *_args, **_kwargs: _Response(body))
    with pytest.raises(AIProviderError) as captured:
        post_json("https://provider.invalid", {}, "", 1)
    assert captured.value.category == category
    assert captured.value.safe_to_retry is False
    assert captured.value.billing_uncertain is True


def test_chat_completion_rejects_non_object_message_as_uncertain_schema_error():
    with pytest.raises(AIProviderError) as captured:
        extract_chat_completion_text({"choices": [{"message": "broken"}]})
    assert captured.value.category == "invalid_response_schema"
    assert captured.value.billing_uncertain is True


class _CountingInvalidProvider:
    name = "remote"

    def __init__(self):
        self.calls = 0

    def generate_json(self, _prompt: str, retry_instruction: str | None = None) -> str:
        del retry_instruction
        self.calls += 1
        return "not-json"


def test_general_analysis_invalid_json_does_not_call_provider_twice(monkeypatch, tmp_path: Path):
    transcript = tmp_path / "transcript.md"
    transcript.write_text("00:00:00 - 00:01:00 测试正文", encoding="utf-8")
    provider = _CountingInvalidProvider()
    monkeypatch.setattr(ai_clip_analyzer, "build_provider", lambda *_args, **_kwargs: provider)
    request = AnalysisRequest(
        task_id="no-double-charge",
        transcript_path=transcript,
        max_clip_duration_minutes=3,
        target_clip_count=1,
        ai_preference="",
        provider_name="remote",
    )
    with pytest.raises(AIAnalysisError, match="没有生成可用候选"):
        ai_clip_analyzer.analyze_task_transcript(request)
    assert provider.calls == 1


def test_variety_invalid_json_does_not_call_provider_twice():
    provider = _CountingInvalidProvider()
    with pytest.raises(AIAnalysisError, match="JSON 解析失败"):
        _generate_payload(provider, "prompt", expected_key="moments")
    assert provider.calls == 1


def test_local_protocol_fallback_only_runs_for_missing_endpoint(monkeypatch):
    provider = LocalModelProvider(
        ProviderConfig(
            base_url="http://127.0.0.1:11434/v1",
            api_key="",
            model="local",
            protocol="responses",
            fallback_protocol="chat_completions",
            timeout_seconds=1,
        )
    )
    calls: list[str] = []

    def fail_ambiguous(*_args):
        calls.append("responses")
        raise AIProviderError("server", http_status=503, billing_uncertain=True)

    monkeypatch.setattr(provider, "_responses", fail_ambiguous)
    monkeypatch.setattr(provider, "_chat_completions", lambda *_args: calls.append("chat") or "{}")
    with pytest.raises(AIProviderError):
        provider.generate_json("prompt")
    assert calls == ["responses"]

    calls.clear()
    monkeypatch.setattr(
        provider,
        "_responses",
        lambda *_args: (_ for _ in ()).throw(AIProviderError("missing", http_status=404)),
    )
    assert provider.generate_json("prompt") == "{}"
    assert calls == ["chat"]


def test_volcengine_retries_429_with_same_request_id(monkeypatch):
    monkeypatch.setattr(transcript_service, "_ensure_volcengine_configured", lambda: None)
    monkeypatch.setattr(transcript_service, "_build_volcengine_flash_payload", lambda _path: {})
    requests = []

    def fake_urlopen(request, **_kwargs):
        requests.append(request)
        if len(requests) < 3:
            raise _http_error(429, headers={"Retry-After": "0"})
        return _Response(json.dumps({"result": {"text": "转写成功"}}))

    monkeypatch.setattr(transcript_service, "urlopen", fake_urlopen)
    segments = transcript_service.transcribe_audio_with_volcengine_flash(
        Path("unused.audio"),
        request_id="stable-request-id",
        sleep_fn=lambda _seconds: None,
    )
    assert [segment.text for segment in segments] == ["转写成功"]
    assert len(requests) == 3
    assert {request.headers["X-api-request-id"] for request in requests} == {"stable-request-id"}


def test_volcengine_5xx_and_bad_schema_are_not_retried(monkeypatch):
    monkeypatch.setattr(transcript_service, "_ensure_volcengine_configured", lambda: None)
    monkeypatch.setattr(transcript_service, "_build_volcengine_flash_payload", lambda _path: {})
    calls = 0

    def fail_5xx(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise _http_error(500)

    monkeypatch.setattr(transcript_service, "urlopen", fail_5xx)
    with pytest.raises(transcript_service.RemoteTranscriptionError) as server_error:
        transcript_service.transcribe_audio_with_volcengine_flash(Path("unused.audio"), sleep_fn=lambda _: None)
    assert server_error.value.billing_uncertain is True
    assert calls == 1

    calls = 0

    def bad_schema(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Response("{}")

    monkeypatch.setattr(transcript_service, "urlopen", bad_schema)
    with pytest.raises(transcript_service.RemoteTranscriptionError, match="缺少 result"):
        transcript_service.transcribe_audio_with_volcengine_flash(Path("unused.audio"), sleep_fn=lambda _: None)
    assert calls == 1


def test_volcengine_invalid_success_fields_are_billing_uncertain(monkeypatch):
    monkeypatch.setattr(transcript_service, "_ensure_volcengine_configured", lambda: None)
    monkeypatch.setattr(transcript_service, "_build_volcengine_flash_payload", lambda _path: {})
    monkeypatch.setattr(
        transcript_service,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            json.dumps(
                {
                    "result": {
                        "utterances": [
                            {"text": "已返回文本", "start_time": 0, "end_time": 1000, "confidence": "bad"}
                        ]
                    }
                }
            )
        ),
    )
    with pytest.raises(transcript_service.RemoteTranscriptionError) as captured:
        transcript_service.transcribe_audio_with_volcengine_flash(Path("unused.audio"), sleep_fn=lambda _: None)
    assert captured.value.category == "invalid_response_schema"
    assert captured.value.billing_uncertain is True


def test_transcript_cancellation_is_not_wrapped_as_provider_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        transcript_service,
        "transcribe_audio_with_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            transcript_service.TranscriptCancelledError("cancelled")
        ),
    )
    with pytest.raises(transcript_service.TranscriptCancelledError, match="cancelled"):
        transcript_service.transcribe_audio_with_configured_provider(
            tmp_path / "audio.wav",
            tmp_path,
            tmp_path / "progress.json",
            provider="local",
        )


def test_volcengine_response_reset_is_billing_uncertain(monkeypatch):
    monkeypatch.setattr(transcript_service, "_ensure_volcengine_configured", lambda: None)
    monkeypatch.setattr(transcript_service, "_build_volcengine_flash_payload", lambda _path: {})

    class ResetResponse(_Response):
        def read(self) -> bytes:
            raise ConnectionResetError("reset while reading")

    monkeypatch.setattr(
        transcript_service,
        "urlopen",
        lambda *_args, **_kwargs: ResetResponse(""),
    )
    with pytest.raises(transcript_service.RemoteTranscriptionError) as captured:
        transcript_service.transcribe_audio_with_volcengine_flash(Path("unused.audio"), sleep_fn=lambda _: None)
    assert captured.value.category == "network_error"
    assert captured.value.billing_uncertain is True
    assert captured.value.safe_to_retry is False


def test_remote_checkpoint_save_failure_becomes_uncertain():
    class Checkpoint:
        def __init__(self):
            self.uncertain = ""

        def save_completed(self, *_args, **_kwargs):
            raise RuntimeError("database locked")

        def save_uncertain(self, _chunk_index: int, error: str):
            self.uncertain = error

    checkpoint = Checkpoint()
    with pytest.raises(
        transcript_service.RemoteTranscriptionResultUncertainError,
        match="checkpoint 保存失败",
    ):
        transcript_service._save_remote_checkpoint_completed(
            checkpoint,
            1,
            [transcript_service.TranscriptSegment(0, 1, "ok")],
        )
    assert "普通重试不会再次请求" in checkpoint.uncertain
