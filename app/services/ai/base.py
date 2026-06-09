from __future__ import annotations

from dataclasses import dataclass
import json
import socket
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class AIProviderError(RuntimeError):
    pass


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
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code in {401, 403}:
            detail = f"{detail}。请检查远程 AI Key 是否缺失、无效，或中转站账号是否有当前模型权限。"
        raise AIProviderError(f"AI 接口返回 HTTP {exc.code}：{detail}") from exc
    except URLError as exc:
        raise AIProviderError(f"无法连接 AI 接口：{exc.reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise AIProviderError("AI 接口连接超时") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise AIProviderError(f"AI 接口响应不是 JSON：{body[:300]}") from exc


def extract_responses_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    chunks: list[str] = []
    for output_item in response.get("output", []) or []:
        for content_item in output_item.get("content", []) or []:
            text = content_item.get("text")
            if isinstance(text, str):
                chunks.append(text)
    if chunks:
        return "\n".join(chunks).strip()
    raise AIProviderError("AI Responses 响应中没有找到文本内容")


def extract_chat_completion_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise AIProviderError("AI Chat Completions 响应中没有 choices")
    first_choice = choices[0]
    message = first_choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    finish_reason = first_choice.get("finish_reason") or "unknown"
    message_keys = ", ".join(sorted(str(key) for key in message.keys())) or "none"
    reasoning_content = message.get("reasoning_content")
    reasoning_chars = len(reasoning_content) if isinstance(reasoning_content, str) else 0
    raise AIProviderError(
        "AI Chat Completions 响应中没有文本内容"
        f"（finish_reason={finish_reason}，message_keys={message_keys}，reasoning_chars={reasoning_chars}）"
    )
