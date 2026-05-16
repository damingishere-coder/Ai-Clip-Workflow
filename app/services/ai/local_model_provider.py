from __future__ import annotations

from app.services.ai.base import (
    AIProviderError,
    ProviderConfig,
    build_url,
    extract_chat_completion_text,
    extract_responses_text,
    post_json,
)


class LocalModelProvider:
    name = "local"

    def __init__(self, config: ProviderConfig):
        self.config = config

    def generate_json(self, prompt: str, retry_instruction: str | None = None) -> str:
        protocols = [self.config.protocol]
        if self.config.fallback_protocol and self.config.fallback_protocol not in protocols:
            protocols.append(self.config.fallback_protocol)

        last_error: AIProviderError | None = None
        for protocol in protocols:
            try:
                if protocol == "responses":
                    return self._responses(prompt, retry_instruction)
                if protocol == "chat_completions":
                    return self._chat_completions(prompt, retry_instruction)
                raise AIProviderError(f"暂不支持本地 AI 协议：{protocol}")
            except AIProviderError as exc:
                last_error = exc

        raise last_error or AIProviderError("本地 AI 调用失败")

    def _responses(self, prompt: str, retry_instruction: str | None) -> str:
        payload = {
            "model": self.config.model,
            "input": _merge_prompt(prompt, retry_instruction),
            "text": {"format": {"type": "json_object"}},
        }
        response = post_json(
            build_url(self.config.base_url, "/responses"),
            payload,
            self.config.api_key,
            self.config.timeout_seconds,
        )
        return extract_responses_text(response)

    def _chat_completions(self, prompt: str, retry_instruction: str | None) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": "你只输出严格 JSON，不输出 Markdown。"},
                {"role": "user", "content": _merge_prompt(prompt, retry_instruction)},
            ],
            "response_format": {"type": "json_object"},
        }
        response = post_json(
            build_url(self.config.base_url, "/chat/completions"),
            payload,
            self.config.api_key,
            self.config.timeout_seconds,
        )
        return extract_chat_completion_text(response)


def _merge_prompt(prompt: str, retry_instruction: str | None) -> str:
    if not retry_instruction:
        return prompt
    return f"{prompt}\n\n{retry_instruction}"
