from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from app.core.config import settings
from app.services.ai.base import AIProviderError, ProviderConfig, build_url
from app.services.ai.local_model_provider import LocalModelProvider
from app.services.ai.remote_responses_provider import RemoteResponsesProvider


def remote_key_looks_valid(api_key: str | None = None) -> bool:
    return len((api_key or settings.ai_analysis_remote_api_key or "").strip()) >= 20


def ollama_origin(base_url: str | None = None) -> str:
    parsed = urlparse((base_url or settings.ai_local_base_url).strip())
    if not parsed.scheme or not parsed.netloc:
        raise AIProviderError("本地 AI Base URL 无效，请使用 http://127.0.0.1:11434/v1")
    return f"{parsed.scheme}://{parsed.netloc}"


def fetch_ollama_models(timeout_seconds: int | None = None) -> list[str]:
    tags_url = build_url(ollama_origin(), "/api/tags")
    try:
        with urlopen(tags_url, timeout=timeout_seconds or settings.ai_local_health_timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except TimeoutError as exc:
        raise AIProviderError("本地 Ollama 健康检查超时，请确认 Ollama 已启动，或先改用 qwen3:8b") from exc
    except Exception as exc:
        raise AIProviderError(f"无法连接本地 Ollama：{exc}") from exc

    models: list[str] = []
    for item in payload.get("models", []) or []:
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name:
            models.append(name)
    return models


def ensure_local_ai_ready(model: str | None = None) -> dict[str, Any]:
    target_model = model or settings.ai_local_model
    models = fetch_ollama_models()
    if target_model not in models:
        raise AIProviderError(
            f"本地 Ollama 已启动，但未找到模型 {target_model}。"
            f"已安装模型：{', '.join(models) or '无'}"
        )
    return {
        "ok": True,
        "base_url": settings.ai_local_base_url,
        "model": target_model,
        "installed_models": models,
    }


def test_local_json_generation(model: str | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    target_model = model or settings.ai_local_model
    ensure_local_ai_ready(target_model)
    provider = LocalModelProvider(
        ProviderConfig(
            base_url=settings.ai_local_base_url,
            api_key=settings.ai_local_api_key,
            model=target_model,
            protocol=settings.ai_local_protocol,
            timeout_seconds=timeout_seconds or settings.ai_local_health_timeout_seconds,
            fallback_protocol=settings.ai_local_fallback_protocol,
        )
    )
    text = provider.generate_json('请只输出严格 JSON：{"status":"ok","provider":"local"}')
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIProviderError(f"本地 AI 返回内容不是合法 JSON：{text[:300]}") from exc


def test_remote_json_generation(timeout_seconds: int | None = None) -> dict[str, Any]:
    if not remote_key_looks_valid():
        raise AIProviderError("远程分析接口 Key 看起来无效或缺失，请检查 AI_ANALYSIS_REMOTE_API_KEY")
    remote_model = settings.ai_analysis_remote_model
    provider = RemoteResponsesProvider(
        ProviderConfig(
            base_url=settings.ai_analysis_remote_base_url,
            api_key=settings.ai_analysis_remote_api_key,
            model=remote_model,
            protocol=settings.ai_analysis_remote_protocol,
            timeout_seconds=timeout_seconds or settings.ai_analysis_request_timeout_seconds,
            responses_path=settings.ai_analysis_remote_responses_path,
            reasoning_effort=settings.ai_analysis_remote_reasoning_effort,
            disable_response_storage=settings.ai_analysis_remote_disable_response_storage.lower() == "true",
            api_key_name="AI_ANALYSIS_REMOTE_API_KEY",
        )
    )
    text = provider.generate_json('请只输出严格 JSON：{"status":"ok","provider":"remote"}')
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIProviderError(f"远程 AI 返回内容不是合法 JSON：{text[:300]}") from exc
