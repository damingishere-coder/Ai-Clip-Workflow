from __future__ import annotations

from app.services.ai.base import (
    AIProviderError,
    ProviderConfig,
    build_url,
    extract_chat_completion_text,
    extract_responses_text,
    post_json,
)


class RemoteResponsesProvider:
    name = "remote"

    def __init__(self, config: ProviderConfig):
        self.config = config

    def generate_json(self, prompt: str, retry_instruction: str | None = None) -> str:
        if not self.config.api_key:
            raise AIProviderError("缺少 AI_REMOTE_API_KEY 或 OPENAI_API_KEY，请先在 .env 中填写远程中转站密钥")
        if self.config.protocol == "responses":
            return self._responses(prompt, retry_instruction)
        if self.config.protocol == "chat_completions":
            return self._chat_completions(prompt, retry_instruction)
        raise AIProviderError(f"暂不支持远程 AI 协议：{self.config.protocol}")

    def _responses(self, prompt: str, retry_instruction: str | None) -> str:
        input_text = _merge_prompt(prompt, retry_instruction)
        payload = {
            "model": self.config.model,
            "input": input_text,
            "text": {"format": {"type": "json_object"}},
        }
        if self.config.disable_response_storage:
            payload["store"] = False
        if self.config.reasoning_effort:
            payload["reasoning"] = {"effort": self.config.reasoning_effort}

        response = post_json(
            build_url(self.config.base_url, self.config.responses_path),
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
            "max_tokens": 4096,
        }
        response = post_json(
            build_url(self.config.base_url, _chat_completions_path(self.config.base_url)),
            payload,
            self.config.api_key,
            self.config.timeout_seconds,
        )
        return extract_chat_completion_text(response)


def _merge_prompt(prompt: str, retry_instruction: str | None) -> str:
    if not retry_instruction:
        return prompt
    return f"{prompt}\n\n{retry_instruction}"


def _is_deepseek_endpoint(base_url: str) -> bool:
    return "deepseek" in (base_url or "").lower()


def _chat_completions_path(base_url: str) -> str:
    normalized = (base_url or "").rstrip("/").lower()
    if _is_deepseek_endpoint(normalized) or normalized.endswith("/v1"):
        return "/chat/completions"
    return "/v1/chat/completions"
