from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.ai import remote_responses_provider as provider_module  # noqa: E402
from app.services.ai.base import AIProviderError, ProviderConfig, extract_chat_completion_text  # noqa: E402
from app.services.ai.remote_responses_provider import RemoteResponsesProvider  # noqa: E402


def _config(base_url: str, reasoning_effort: str | None = None) -> ProviderConfig:
    return ProviderConfig(
        base_url=base_url,
        api_key="test-key",
        model="deepseek-v4-pro",
        protocol="chat_completions",
        timeout_seconds=30,
        reasoning_effort=reasoning_effort,
    )


def _capture_payload(config: ProviderConfig) -> dict:
    captured: dict = {}

    def fake_post_json(url: str, payload: dict, api_key: str, timeout_seconds: int) -> dict:
        captured["url"] = url
        captured["payload"] = payload
        captured["api_key"] = api_key
        captured["timeout_seconds"] = timeout_seconds
        return {"choices": [{"message": {"content": '{"status":"ok"}'}, "finish_reason": "stop"}]}

    original_post_json = provider_module.post_json
    provider_module.post_json = fake_post_json
    try:
        text = RemoteResponsesProvider(config).generate_json("只输出 JSON")
    finally:
        provider_module.post_json = original_post_json

    assert text == '{"status":"ok"}'
    return captured


def test_deepseek_chat_completions_disables_thinking() -> None:
    captured = _capture_payload(_config("https://api.deepseek.com"))

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert "不输出推理过程" in captured["payload"]["messages"][0]["content"]


def test_non_deepseek_chat_completions_keeps_standard_payload() -> None:
    captured = _capture_payload(_config("https://example.com/v1"))

    assert captured["url"] == "https://example.com/v1/chat/completions"
    assert "thinking" not in captured["payload"]


def test_empty_chat_completion_content_includes_diagnostics() -> None:
    try:
        extract_chat_completion_text(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "", "reasoning_content": "reasoning only"},
                    }
                ]
            }
        )
    except AIProviderError as exc:
        message = str(exc)
    else:
        raise AssertionError("empty content should raise AIProviderError")

    assert "finish_reason=stop" in message
    assert "message_keys=content, reasoning_content" in message
    assert "reasoning_chars=14" in message


def main() -> None:
    test_deepseek_chat_completions_disables_thinking()
    print("deepseek thinking disabled payload: OK")
    test_non_deepseek_chat_completions_keeps_standard_payload()
    print("standard chat completions payload: OK")
    test_empty_chat_completion_content_includes_diagnostics()
    print("empty content diagnostics: OK")


if __name__ == "__main__":
    main()
