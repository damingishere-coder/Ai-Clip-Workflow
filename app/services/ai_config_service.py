import os
from pathlib import Path

from app.core.config import settings
from app.models.settings import AIConfigUpdate


AI_ENV_KEYS = [
    "AI_DEFAULT_PROVIDER",
    "AI_REQUEST_TIMEOUT_SECONDS",
    "AI_REMOTE_BASE_URL",
    "AI_REMOTE_API_KEY",
    "AI_REMOTE_MODEL",
    "AI_REMOTE_REVIEW_MODEL",
    "AI_REMOTE_PROTOCOL",
    "AI_REMOTE_REASONING_EFFORT",
    "AI_REMOTE_RESPONSES_PATH",
    "AI_REMOTE_DISABLE_RESPONSE_STORAGE",
    "AI_LOCAL_BASE_URL",
    "AI_LOCAL_API_KEY",
    "AI_LOCAL_MODEL",
    "AI_LOCAL_PROTOCOL",
    "AI_LOCAL_FALLBACK_PROTOCOL",
    "AI_NETWORK_ACCESS",
    "AI_WINDOWS_WSL_SETUP_ACKNOWLEDGED",
    "AI_MODEL_CONTEXT_WINDOW",
    "AI_MODEL_AUTO_COMPACT_TOKEN_LIMIT",
]

SETTING_ATTRS = {
    "AI_DEFAULT_PROVIDER": "ai_default_provider",
    "AI_REQUEST_TIMEOUT_SECONDS": "ai_request_timeout_seconds",
    "AI_REMOTE_BASE_URL": "ai_remote_base_url",
    "AI_REMOTE_API_KEY": "ai_remote_api_key",
    "AI_REMOTE_MODEL": "ai_remote_model",
    "AI_REMOTE_REVIEW_MODEL": "ai_remote_review_model",
    "AI_REMOTE_PROTOCOL": "ai_remote_protocol",
    "AI_REMOTE_REASONING_EFFORT": "ai_remote_reasoning_effort",
    "AI_REMOTE_RESPONSES_PATH": "ai_remote_responses_path",
    "AI_REMOTE_DISABLE_RESPONSE_STORAGE": "ai_remote_disable_response_storage",
    "AI_LOCAL_BASE_URL": "ai_local_base_url",
    "AI_LOCAL_API_KEY": "ai_local_api_key",
    "AI_LOCAL_MODEL": "ai_local_model",
    "AI_LOCAL_PROTOCOL": "ai_local_protocol",
    "AI_LOCAL_FALLBACK_PROTOCOL": "ai_local_fallback_protocol",
    "AI_NETWORK_ACCESS": "ai_network_access",
    "AI_WINDOWS_WSL_SETUP_ACKNOWLEDGED": "ai_windows_wsl_setup_acknowledged",
    "AI_MODEL_CONTEXT_WINDOW": "ai_model_context_window",
    "AI_MODEL_AUTO_COMPACT_TOKEN_LIMIT": "ai_model_auto_compact_token_limit",
}

SECRET_KEYS = {"AI_REMOTE_API_KEY", "AI_LOCAL_API_KEY"}
LOCAL_MODEL_OPTIONS = ["qwen3:8b", "gemma3:12b", "qwen3:14b"]


def _env_path() -> Path:
    return settings.project_root / ".env"


def _read_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    env_file = _env_path()
    if not env_file.exists():
        return values

    for raw_line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "********"
    return f"{value[:4]}********{value[-4:]}"


def _current_config_values() -> dict[str, str]:
    env_values = _read_env_values()
    values: dict[str, str] = {}
    for env_key, attr_name in SETTING_ATTRS.items():
        current = env_values.get(env_key)
        if current is None:
            current = str(getattr(settings, attr_name))
        values[env_key] = current
    return values


def _apply_runtime_values(values: dict[str, str]) -> None:
    for env_key, attr_name in SETTING_ATTRS.items():
        value = values[env_key]
        os.environ[env_key] = value
        if attr_name in {
            "ai_request_timeout_seconds",
            "ai_model_context_window",
            "ai_model_auto_compact_token_limit",
        }:
            object.__setattr__(settings, attr_name, int(value))
        else:
            object.__setattr__(settings, attr_name, value)


def _write_env_values(values: dict[str, str]) -> None:
    env_file = _env_path()
    env_file.write_text(
        "\n".join(
            [
                "# Live Streaming Slicing Workflow local AI config",
                "# This file is ignored by Git. Do not commit real API keys.",
                "",
                f"AI_DEFAULT_PROVIDER={values['AI_DEFAULT_PROVIDER']}",
                f"AI_REQUEST_TIMEOUT_SECONDS={values['AI_REQUEST_TIMEOUT_SECONDS']}",
                "",
                "# Remote OpenAI-compatible API",
                f"AI_REMOTE_BASE_URL={values['AI_REMOTE_BASE_URL']}",
                f"AI_REMOTE_API_KEY={values['AI_REMOTE_API_KEY']}",
                f"AI_REMOTE_MODEL={values['AI_REMOTE_MODEL']}",
                f"AI_REMOTE_REVIEW_MODEL={values['AI_REMOTE_REVIEW_MODEL']}",
                f"AI_REMOTE_PROTOCOL={values['AI_REMOTE_PROTOCOL']}",
                f"AI_REMOTE_REASONING_EFFORT={values['AI_REMOTE_REASONING_EFFORT']}",
                f"AI_REMOTE_RESPONSES_PATH={values['AI_REMOTE_RESPONSES_PATH']}",
                f"AI_REMOTE_DISABLE_RESPONSE_STORAGE={values['AI_REMOTE_DISABLE_RESPONSE_STORAGE']}",
                "",
                "# Local Ollama API",
                f"AI_LOCAL_BASE_URL={values['AI_LOCAL_BASE_URL']}",
                f"AI_LOCAL_API_KEY={values['AI_LOCAL_API_KEY']}",
                f"AI_LOCAL_MODEL={values['AI_LOCAL_MODEL']}",
                f"AI_LOCAL_PROTOCOL={values['AI_LOCAL_PROTOCOL']}",
                f"AI_LOCAL_FALLBACK_PROTOCOL={values['AI_LOCAL_FALLBACK_PROTOCOL']}",
                "",
                "# Extra AI runtime defaults",
                f"AI_NETWORK_ACCESS={values['AI_NETWORK_ACCESS']}",
                f"AI_WINDOWS_WSL_SETUP_ACKNOWLEDGED={values['AI_WINDOWS_WSL_SETUP_ACKNOWLEDGED']}",
                f"AI_MODEL_CONTEXT_WINDOW={values['AI_MODEL_CONTEXT_WINDOW']}",
                f"AI_MODEL_AUTO_COMPACT_TOKEN_LIMIT={values['AI_MODEL_AUTO_COMPACT_TOKEN_LIMIT']}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def get_ai_config_context() -> dict:
    values = _current_config_values()
    display_values = {
        key: (_mask_secret(value) if key in SECRET_KEYS else value)
        for key, value in values.items()
    }
    remote_ready = bool(values["AI_REMOTE_BASE_URL"] and values["AI_REMOTE_MODEL"] and values["AI_REMOTE_API_KEY"])
    local_ready = bool(values["AI_LOCAL_BASE_URL"] and values["AI_LOCAL_MODEL"])
    return {
        "env_path": str(_env_path()),
        "env_exists": _env_path().exists(),
        "values": display_values,
        "remote_ready": remote_ready,
        "local_ready": local_ready,
        "default_provider": values["AI_DEFAULT_PROVIDER"],
        "configured_count": int(remote_ready) + int(local_ready),
        "local_model_options": LOCAL_MODEL_OPTIONS,
    }


def save_ai_config(payload: AIConfigUpdate) -> dict:
    current_values = _current_config_values()
    updates = {
        "AI_DEFAULT_PROVIDER": payload.ai_default_provider.strip() or "remote",
        "AI_REQUEST_TIMEOUT_SECONDS": str(payload.ai_request_timeout_seconds),
        "AI_REMOTE_BASE_URL": payload.ai_remote_base_url.strip(),
        "AI_REMOTE_API_KEY": payload.ai_remote_api_key.strip() or current_values.get("AI_REMOTE_API_KEY", ""),
        "AI_REMOTE_MODEL": payload.ai_remote_model.strip(),
        "AI_REMOTE_REVIEW_MODEL": payload.ai_remote_review_model.strip() or "gpt-5.5",
        "AI_REMOTE_PROTOCOL": payload.ai_remote_protocol.strip() or "responses",
        "AI_REMOTE_REASONING_EFFORT": payload.ai_remote_reasoning_effort.strip(),
        "AI_REMOTE_RESPONSES_PATH": payload.ai_remote_responses_path.strip() or "/v1/responses",
        "AI_REMOTE_DISABLE_RESPONSE_STORAGE": str(payload.ai_remote_disable_response_storage).lower(),
        "AI_LOCAL_BASE_URL": payload.ai_local_base_url.strip() or "http://127.0.0.1:11434/v1",
        "AI_LOCAL_API_KEY": payload.ai_local_api_key.strip() or current_values.get("AI_LOCAL_API_KEY", "ollama"),
        "AI_LOCAL_MODEL": payload.ai_local_model.strip() or "qwen3:8b",
        "AI_LOCAL_PROTOCOL": payload.ai_local_protocol.strip() or "chat_completions",
        "AI_LOCAL_FALLBACK_PROTOCOL": payload.ai_local_fallback_protocol.strip(),
        "AI_NETWORK_ACCESS": payload.ai_network_access.strip() or "enabled",
        "AI_WINDOWS_WSL_SETUP_ACKNOWLEDGED": str(payload.ai_windows_wsl_setup_acknowledged).lower(),
        "AI_MODEL_CONTEXT_WINDOW": str(payload.ai_model_context_window),
        "AI_MODEL_AUTO_COMPACT_TOKEN_LIMIT": str(payload.ai_model_auto_compact_token_limit),
    }
    _write_env_values(updates)
    _apply_runtime_values(updates)
    return {
        "message": "AI 配置已保存到 .env，并已应用到当前运行服务。",
        "config": get_ai_config_context(),
    }
