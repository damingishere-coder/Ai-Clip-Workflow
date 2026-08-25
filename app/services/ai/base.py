from __future__ import annotations

from dataclasses import dataclass
import json
import socket
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class AIProviderError(RuntimeError):
    """携带重试与计费语义的 AI Provider 错误。

    `safe_to_retry` 只在能够确认请求未被模型正常接受时为真。调用方不得根据
    “网络错误”或“5xx”几个字自行重试，因为这两类错误可能发生在模型已经执行之后。
    """

    def __init__(
        self,
        message: str,
        *,
        category: str = "provider_error",
        http_status: int | None = None,
        safe_to_retry: bool = False,
        billing_uncertain: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.http_status = http_status
        self.safe_to_retry = safe_to_retry
        self.billing_uncertain = billing_uncertain
        self.retry_after_seconds = retry_after_seconds

    def checkpoint_message(self) -> str:
        suffix = "；本次是否计费不确定，未自动重试" if self.billing_uncertain else ""
        return f"[{self.category}] {self}{suffix}"


class AIProvider(Protocol):
    name: str

    def generate_json(self, prompt: str, retry_instruction: str | None = None) -> str:
        ...


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str
    protocol: str
    timeout_seconds: int
    responses_path: str = "/v1/responses"
    reasoning_effort: str | None = None
    fallback_protocol: str | None = None
    disable_response_storage: bool = True
    api_key_name: str = "远程接口 API Key"


def generate_json_with_safe_retry(
    provider: AIProvider,
    prompt: str,
    retry_instruction: str | None = None,
    *,
    max_attempts: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> str:
    """只重试 Provider 明确标记为未执行/被拒绝的调用。"""
    attempts = max(1, min(int(max_attempts), 3))
    for attempt in range(1, attempts + 1):
        try:
            if retry_instruction is None:
                return provider.generate_json(prompt)
            return provider.generate_json(prompt, retry_instruction=retry_instruction)
        except AIProviderError as exc:
            if not exc.safe_to_retry or attempt >= attempts:
                raise
            delay = exc.retry_after_seconds if exc.retry_after_seconds is not None else 2 ** (attempt - 1)
            sleep_fn(max(0.0, min(delay, 60.0)))
    raise AssertionError("AI Provider 安全重试循环异常结束")


def build_url(base_url: str, path: str) -> str:
    normalized_base = base_url.rstrip("/")
    normalized_path = path.strip("/")
    if not normalized_path:
        return normalized_base

    base_parts = [part for part in urlparse(normalized_base).path.strip("/").split("/") if part]
    path_parts = [part for part in normalized_path.split("/") if part]
    overlap_limit = min(len(base_parts), len(path_parts))
    for overlap in range(overlap_limit, 0, -1):
        if base_parts[-overlap:] == path_parts[:overlap]:
            path_parts = path_parts[overlap:]
            break

    if not path_parts:
        return normalized_base
    return f"{normalized_base}/{'/'.join(path_parts)}"


def post_json(url: str, payload: dict[str, Any], api_key: str, timeout_seconds: int) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code in {401, 403}:
            detail = f"{detail}。请检查远程 AI Key 是否缺失、无效，或中转站账号是否有当前模型权限。"
        retry_after = _parse_retry_after((exc.headers or {}).get("Retry-After")) if exc.code == 429 else None
        raise AIProviderError(
            f"AI 接口返回 HTTP {exc.code}：{detail}",
            category="rate_limited" if exc.code == 429 else "http_error",
            http_status=exc.code,
            safe_to_retry=exc.code == 429,
            billing_uncertain=exc.code >= 500 or exc.code == 408,
            retry_after_seconds=retry_after,
        ) from exc
    except URLError as exc:
        reason = exc.reason
        is_timeout = isinstance(reason, (TimeoutError, socket.timeout))
        is_preconnect_failure = isinstance(reason, (ConnectionRefusedError, socket.gaierror))
        raise AIProviderError(
            "AI 接口连接超时" if is_timeout else f"无法连接 AI 接口：{reason}",
            category="timeout" if is_timeout else "network_error",
            safe_to_retry=is_preconnect_failure,
            billing_uncertain=not is_preconnect_failure,
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise AIProviderError(
            "AI 接口连接或读取超时",
            category="timeout",
            billing_uncertain=True,
        ) from exc
    except OSError as exc:
        raise AIProviderError(
            f"AI 接口请求失败：{exc}",
            category="network_error",
            billing_uncertain=True,
        ) from exc

    if not body.strip():
        raise AIProviderError(
            "AI 接口返回空响应",
            category="empty_response",
            billing_uncertain=True,
        )

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AIProviderError(
            f"AI 接口响应不是 JSON：{body[:300]}",
            category="invalid_response_json",
            billing_uncertain=True,
        ) from exc
    if not isinstance(parsed, dict):
        raise AIProviderError(
            "AI 接口响应 JSON 顶层不是对象",
            category="invalid_response_schema",
            billing_uncertain=True,
        )
    return parsed


def _parse_retry_after(value: str | None) -> float | None:
    try:
        seconds = float(str(value or "").strip())
    except ValueError:
        return None
    return max(0.0, min(seconds, 60.0))


def extract_responses_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    chunks: list[str] = []
    for output_item in response.get("output", []) or []:
        if not isinstance(output_item, dict):
            continue
        for content_item in output_item.get("content", []) or []:
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text")
            if isinstance(text, str):
                chunks.append(text)
    if chunks:
        return "\n".join(chunks).strip()
    raise AIProviderError(
        "AI Responses 响应中没有找到文本内容",
        category="empty_model_output",
        billing_uncertain=True,
    )


def extract_chat_completion_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise AIProviderError(
            "AI Chat Completions 响应中没有 choices",
            category="empty_model_output",
            billing_uncertain=True,
        )
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise AIProviderError(
            "AI Chat Completions choices 条目格式无效",
            category="invalid_response_schema",
            billing_uncertain=True,
        )
    message = first_choice.get("message") or {}
    if not isinstance(message, dict):
        raise AIProviderError(
            "AI Chat Completions message 结构无效",
            category="invalid_response_schema",
            billing_uncertain=True,
        )
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    finish_reason = first_choice.get("finish_reason") or "unknown"
    message_keys = ", ".join(sorted(str(key) for key in message.keys())) or "none"
    reasoning_content = message.get("reasoning_content")
    reasoning_chars = len(reasoning_content) if isinstance(reasoning_content, str) else 0
    raise AIProviderError(
        "AI Chat Completions 响应中没有文本内容"
        f"（finish_reason={finish_reason}，message_keys={message_keys}，reasoning_chars={reasoning_chars}）",
        category="empty_model_output",
        billing_uncertain=True,
    )
