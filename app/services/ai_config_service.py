import os
from pathlib import Path

from app.core.config import settings
from app.models.settings import AIConfigUpdate
from app.services.ai.codex_cli_provider import CodexCliConfig, CodexCliProvider
from app.services.local_transcription_runtime import get_local_transcription_runtime_status


SETTING_ATTRS = {
    "AI_DEFAULT_PROVIDER": "ai_default_provider",
    "AI_PUBLISH_PROVIDER": "ai_publish_provider",
    "AI_REQUEST_TIMEOUT_SECONDS": "ai_request_timeout_seconds",
    "AI_CODEX_PATH": "ai_codex_path",
    "AI_CODEX_HOME": "ai_codex_home",
    "AI_CODEX_MODEL": "ai_codex_model",
    "AI_CODEX_TIMEOUT_SECONDS": "ai_codex_timeout_seconds",
    "TRANSCRIPTION_PROVIDER": "transcription_provider",
    "TRANSCRIPTION_FALLBACK_PROVIDER": "transcription_fallback_provider",
    "TRANSCRIPTION_OFFLINE_ONLY": "transcription_offline_only",
    "TRANSCRIPTION_MODEL": "transcription_model",
    "TRANSCRIPTION_MODEL_REVISION": "transcription_model_revision",
    "TRANSCRIPTION_MODEL_CACHE_DIR": "transcription_model_cache_dir",
    "TRANSCRIPTION_LOCAL_FILES_ONLY": "transcription_local_files_only",
    "TRANSCRIPTION_DEVICE": "transcription_device",
    "TRANSCRIPTION_COMPUTE_TYPE": "transcription_compute_type",
    "VOLCENGINE_ASR_API_URL": "volcengine_asr_api_url",
    "VOLCENGINE_ASR_API_KEY": "volcengine_asr_api_key",
    "VOLCENGINE_ASR_APP_KEY": "volcengine_asr_app_key",
    "VOLCENGINE_ASR_ACCESS_KEY": "volcengine_asr_access_key",
    "VOLCENGINE_ASR_RESOURCE_ID": "volcengine_asr_resource_id",
    "VOLCENGINE_ASR_TIMEOUT_SECONDS": "volcengine_asr_timeout_seconds",
    "VOLCENGINE_ASR_AUDIO_FORMAT": "volcengine_asr_audio_format",
    "AI_ANALYSIS_REMOTE_BASE_URL": "ai_analysis_remote_base_url",
    "AI_ANALYSIS_REMOTE_API_KEY": "ai_analysis_remote_api_key",
    "AI_ANALYSIS_REMOTE_MODEL": "ai_analysis_remote_model",
    "AI_ANALYSIS_REMOTE_PROTOCOL": "ai_analysis_remote_protocol",
    "AI_ANALYSIS_REMOTE_REASONING_EFFORT": "ai_analysis_remote_reasoning_effort",
    "AI_ANALYSIS_REMOTE_RESPONSES_PATH": "ai_analysis_remote_responses_path",
    "AI_ANALYSIS_REMOTE_DISABLE_RESPONSE_STORAGE": "ai_analysis_remote_disable_response_storage",
    "AI_ANALYSIS_REQUEST_TIMEOUT_SECONDS": "ai_analysis_request_timeout_seconds",
    "AI_PUBLISH_REMOTE_BASE_URL": "ai_publish_remote_base_url",
    "AI_PUBLISH_REMOTE_API_KEY": "ai_publish_remote_api_key",
    "AI_PUBLISH_REMOTE_MODEL": "ai_publish_remote_model",
    "AI_PUBLISH_REMOTE_PROTOCOL": "ai_publish_remote_protocol",
    "AI_PUBLISH_REMOTE_REASONING_EFFORT": "ai_publish_remote_reasoning_effort",
    "AI_PUBLISH_REMOTE_RESPONSES_PATH": "ai_publish_remote_responses_path",
    "AI_PUBLISH_REMOTE_DISABLE_RESPONSE_STORAGE": "ai_publish_remote_disable_response_storage",
    "AI_PUBLISH_REQUEST_TIMEOUT_SECONDS": "ai_publish_request_timeout_seconds",
    "AI_LOCAL_BASE_URL": "ai_local_base_url",
    "AI_LOCAL_API_KEY": "ai_local_api_key",
    "AI_LOCAL_MODEL": "ai_local_model",
    "AI_LOCAL_PROTOCOL": "ai_local_protocol",
    "AI_LOCAL_FALLBACK_PROTOCOL": "ai_local_fallback_protocol",
    "AI_LOCAL_HEALTH_TIMEOUT_SECONDS": "ai_local_health_timeout_seconds",
    "AI_NETWORK_ACCESS": "ai_network_access",
    "AI_WINDOWS_WSL_SETUP_ACKNOWLEDGED": "ai_windows_wsl_setup_acknowledged",
    "AI_MODEL_CONTEXT_WINDOW": "ai_model_context_window",
    "AI_MODEL_AUTO_COMPACT_TOKEN_LIMIT": "ai_model_auto_compact_token_limit",
}

LEGACY_FALLBACK_KEYS = {
    "AI_ANALYSIS_REMOTE_BASE_URL": ("AI_REMOTE_BASE_URL",),
    "AI_ANALYSIS_REMOTE_API_KEY": ("AI_REMOTE_API_KEY", "OPENAI_API_KEY"),
    "AI_ANALYSIS_REMOTE_MODEL": ("AI_REMOTE_REVIEW_MODEL", "AI_REMOTE_MODEL"),
    "AI_ANALYSIS_REMOTE_PROTOCOL": ("AI_REMOTE_PROTOCOL",),
    "AI_ANALYSIS_REMOTE_REASONING_EFFORT": ("AI_REMOTE_REASONING_EFFORT",),
    "AI_ANALYSIS_REMOTE_RESPONSES_PATH": ("AI_REMOTE_RESPONSES_PATH",),
    "AI_ANALYSIS_REMOTE_DISABLE_RESPONSE_STORAGE": ("AI_REMOTE_DISABLE_RESPONSE_STORAGE",),
    "AI_ANALYSIS_REQUEST_TIMEOUT_SECONDS": ("AI_REQUEST_TIMEOUT_SECONDS",),
    "AI_PUBLISH_REMOTE_BASE_URL": ("AI_REMOTE_BASE_URL",),
    "AI_PUBLISH_REMOTE_API_KEY": ("AI_REMOTE_API_KEY", "OPENAI_API_KEY"),
    "AI_PUBLISH_REMOTE_MODEL": ("AI_REMOTE_PUBLISH_MODEL", "AI_REMOTE_MODEL"),
    "AI_PUBLISH_REMOTE_PROTOCOL": ("AI_REMOTE_PROTOCOL",),
    "AI_PUBLISH_REMOTE_REASONING_EFFORT": ("AI_REMOTE_REASONING_EFFORT",),
    "AI_PUBLISH_REMOTE_RESPONSES_PATH": ("AI_REMOTE_RESPONSES_PATH",),
    "AI_PUBLISH_REMOTE_DISABLE_RESPONSE_STORAGE": ("AI_REMOTE_DISABLE_RESPONSE_STORAGE",),
    "AI_PUBLISH_REQUEST_TIMEOUT_SECONDS": ("AI_REQUEST_TIMEOUT_SECONDS",),
}

INTEGER_ATTRS = {
    "ai_request_timeout_seconds",
    "ai_codex_timeout_seconds",
    "volcengine_asr_timeout_seconds",
    "ai_analysis_request_timeout_seconds",
    "ai_publish_request_timeout_seconds",
    "ai_local_health_timeout_seconds",
    "ai_model_context_window",
    "ai_model_auto_compact_token_limit",
}

BOOLEAN_ATTRS = {
    "transcription_offline_only",
    "transcription_local_files_only",
}

PATH_ATTRS = {
    "transcription_model_cache_dir",
}

LOCAL_MODEL_OPTIONS = ["qwen3:8b", "gemma3:12b", "qwen3:14b"]
REMOTE_MODEL_OPTIONS = ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"]
REMOTE_PROTOCOL_OPTIONS = ["chat_completions", "responses"]
TRANSCRIPTION_AUDIO_FORMAT_OPTIONS = ["mp3", "ogg"]

SECRET_SETTING_KEYS = frozenset(
    {
        "VOLCENGINE_ASR_API_KEY",
        "VOLCENGINE_ASR_APP_KEY",
        "VOLCENGINE_ASR_ACCESS_KEY",
        "AI_ANALYSIS_REMOTE_API_KEY",
        "AI_PUBLISH_REMOTE_API_KEY",
        "AI_LOCAL_API_KEY",
    }
)

ENV_APPEND_GROUPS = [
    (
        "# AI common switch",
        [
            "AI_DEFAULT_PROVIDER",
            "AI_PUBLISH_PROVIDER",
            "AI_REQUEST_TIMEOUT_SECONDS",
        ],
    ),
    (
        "# Codex CLI - reuses the current Windows ChatGPT login",
        [
            "AI_CODEX_PATH",
            "AI_CODEX_HOME",
            "AI_CODEX_MODEL",
            "AI_CODEX_TIMEOUT_SECONDS",
        ],
    ),
    (
        "# 1. Audio transcription - offline local primary, Volcengine rollback only",
        [
            "TRANSCRIPTION_PROVIDER",
            "TRANSCRIPTION_FALLBACK_PROVIDER",
            "TRANSCRIPTION_OFFLINE_ONLY",
            "TRANSCRIPTION_MODEL",
            "TRANSCRIPTION_MODEL_REVISION",
            "TRANSCRIPTION_MODEL_CACHE_DIR",
            "TRANSCRIPTION_LOCAL_FILES_ONLY",
            "TRANSCRIPTION_DEVICE",
            "TRANSCRIPTION_COMPUTE_TYPE",
            "VOLCENGINE_ASR_API_URL",
            "VOLCENGINE_ASR_API_KEY",
            "VOLCENGINE_ASR_APP_KEY",
            "VOLCENGINE_ASR_ACCESS_KEY",
            "VOLCENGINE_ASR_RESOURCE_ID",
            "VOLCENGINE_ASR_TIMEOUT_SECONDS",
            "VOLCENGINE_ASR_AUDIO_FORMAT",
        ],
    ),
    (
        "# 2. Transcript analysis remote AI",
        [
            "AI_ANALYSIS_REMOTE_BASE_URL",
            "AI_ANALYSIS_REMOTE_API_KEY",
            "AI_ANALYSIS_REMOTE_MODEL",
            "AI_ANALYSIS_REMOTE_PROTOCOL",
            "AI_ANALYSIS_REMOTE_REASONING_EFFORT",
            "AI_ANALYSIS_REMOTE_RESPONSES_PATH",
            "AI_ANALYSIS_REMOTE_DISABLE_RESPONSE_STORAGE",
            "AI_ANALYSIS_REQUEST_TIMEOUT_SECONDS",
        ],
    ),
    (
        "# 3. Publish copy remote AI",
        [
            "AI_PUBLISH_REMOTE_BASE_URL",
            "AI_PUBLISH_REMOTE_API_KEY",
            "AI_PUBLISH_REMOTE_MODEL",
            "AI_PUBLISH_REMOTE_PROTOCOL",
            "AI_PUBLISH_REMOTE_REASONING_EFFORT",
            "AI_PUBLISH_REMOTE_RESPONSES_PATH",
            "AI_PUBLISH_REMOTE_DISABLE_RESPONSE_STORAGE",
            "AI_PUBLISH_REQUEST_TIMEOUT_SECONDS",
        ],
    ),
    (
        "# Local Ollama fallback",
        [
            "AI_LOCAL_BASE_URL",
            "AI_LOCAL_API_KEY",
            "AI_LOCAL_MODEL",
            "AI_LOCAL_PROTOCOL",
            "AI_LOCAL_FALLBACK_PROTOCOL",
            "AI_LOCAL_HEALTH_TIMEOUT_SECONDS",
        ],
    ),
    (
        "# Extra AI runtime defaults",
        [
            "AI_NETWORK_ACCESS",
            "AI_WINDOWS_WSL_SETUP_ACKNOWLEDGED",
            "AI_MODEL_CONTEXT_WINDOW",
            "AI_MODEL_AUTO_COMPACT_TOKEN_LIMIT",
        ],
    ),
]


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


def _current_config_values() -> dict[str, str]:
    env_values = _read_env_values()
    values: dict[str, str] = {}
    for env_key, attr_name in SETTING_ATTRS.items():
        current = env_values.get(env_key)
        if current is None:
            for fallback_key in LEGACY_FALLBACK_KEYS.get(env_key, ()):
                fallback_value = env_values.get(fallback_key)
                if fallback_value:
                    current = fallback_value
                    break
        if current is None:
            current = str(getattr(settings, attr_name))
        values[env_key] = current
    return values


def _sync_legacy_runtime_aliases(values: dict[str, str]) -> None:
    object.__setattr__(settings, "ai_remote_base_url", values["AI_ANALYSIS_REMOTE_BASE_URL"])
    object.__setattr__(settings, "ai_remote_api_key", values["AI_ANALYSIS_REMOTE_API_KEY"])
    object.__setattr__(settings, "ai_remote_model", values["AI_ANALYSIS_REMOTE_MODEL"])
    object.__setattr__(settings, "ai_remote_review_model", values["AI_ANALYSIS_REMOTE_MODEL"])
    object.__setattr__(settings, "ai_remote_publish_model", values["AI_PUBLISH_REMOTE_MODEL"])
    object.__setattr__(settings, "ai_remote_protocol", values["AI_ANALYSIS_REMOTE_PROTOCOL"])
    object.__setattr__(settings, "ai_remote_reasoning_effort", values["AI_ANALYSIS_REMOTE_REASONING_EFFORT"])
    object.__setattr__(settings, "ai_remote_responses_path", values["AI_ANALYSIS_REMOTE_RESPONSES_PATH"])
    object.__setattr__(
        settings,
        "ai_remote_disable_response_storage",
        values["AI_ANALYSIS_REMOTE_DISABLE_RESPONSE_STORAGE"],
    )
    object.__setattr__(settings, "ai_request_timeout_seconds", int(values["AI_REQUEST_TIMEOUT_SECONDS"]))


def _apply_runtime_values(values: dict[str, str]) -> None:
    for env_key, attr_name in SETTING_ATTRS.items():
        if env_key not in values:
            continue
        value = values[env_key]
        os.environ[env_key] = value
        if attr_name in INTEGER_ATTRS:
            object.__setattr__(settings, attr_name, int(value))
        elif attr_name in BOOLEAN_ATTRS:
            object.__setattr__(settings, attr_name, value.strip().lower() in {"1", "true", "yes", "on"})
        elif attr_name in PATH_ATTRS:
            object.__setattr__(settings, attr_name, Path(value))
        else:
            object.__setattr__(settings, attr_name, value)


def _write_env_values(updates: dict[str, str]) -> None:
    env_file = _env_path()
    lines = env_file.read_text(encoding="utf-8", errors="replace").splitlines() if env_file.exists() else []
    output_lines: list[str] = []
    updated_keys: set[str] = set()

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output_lines.append(raw_line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            output_lines.append(f"{key}={updates[key]}")
            updated_keys.add(key)
        else:
            output_lines.append(raw_line)

    missing_keys = [key for _, keys in ENV_APPEND_GROUPS for key in keys if key in updates and key not in updated_keys]
    if missing_keys:
        if output_lines and output_lines[-1].strip():
            output_lines.append("")
        output_lines.append("# NiuMa Studio AI interface config")
        for group_title, group_keys in ENV_APPEND_GROUPS:
            group_missing = [key for key in group_keys if key in missing_keys]
            if not group_missing:
                continue
            output_lines.append(group_title)
            output_lines.extend(f"{key}={updates[key]}" for key in group_missing)
            output_lines.append("")

    while output_lines and output_lines[-1] == "":
        output_lines.pop()
    env_file.write_text("\n".join(output_lines) + "\n", encoding="utf-8")


def _key_valid(value: str) -> bool:
    return len((value or "").strip()) >= 20


def _remote_ready(values: dict[str, str], prefix: str) -> bool:
    return bool(
        values[f"{prefix}_BASE_URL"]
        and values[f"{prefix}_MODEL"]
        and _key_valid(values[f"{prefix}_API_KEY"])
    )


def _public_config_values(values: dict[str, str]) -> dict[str, str]:
    """只把非敏感配置送到浏览器；Secret 用单独布尔状态表达。"""
    return {
        key: "" if key in SECRET_SETTING_KEYS else value
        for key, value in values.items()
    }


def get_ai_config_context() -> dict:
    active = {name.upper() for name in AIConfigUpdate.model_fields}
    values = {key: str(getattr(settings, attr)) for key, attr in SETTING_ATTRS.items() if key in active}
    runtime = get_local_transcription_runtime_status()
    provider = CodexCliProvider(CodexCliConfig(
        executable=settings.ai_codex_path, model=settings.ai_codex_model,
        timeout_seconds=settings.ai_codex_timeout_seconds, codex_home=settings.ai_codex_home,
    ))
    codex_status = provider.version_status()
    login = provider.login_status()
    codex_status["detail"] += " · " + login["detail"]
    codex_status["ok"] = codex_status["ok"] and login["ok"]
    ready = bool(codex_status["ok"])
    return {
        "env_exists": _env_path().exists(), "values": values,
        "transcription_ready": bool(runtime["ready"]), "transcription_runtime": runtime,
        "analysis_ready": ready, "publish_ready": ready,
        "default_provider": "codex", "publish_provider": "codex", "codex_status": codex_status,
        "configured_count": int(runtime["ready"]) + 2 * int(ready),
    }


def _normalize_remote_protocol(base_url: str, protocol: str) -> str:
    resolved = protocol.strip() or "chat_completions"
    if "deepseek" in base_url.lower() and resolved == "responses":
        return "chat_completions"
    return resolved


def _normalize_reasoning_effort(base_url: str, model: str, protocol: str, value: str) -> str:
    if "deepseek" in base_url.lower() and model.startswith("deepseek") and protocol == "chat_completions":
        return ""
    return value.strip()


def save_ai_config(payload: AIConfigUpdate) -> dict:
    # Omitted fields retain their runtime/file values. Never rewrite legacy secrets or Codex Home.
    updates = {}
    for name in payload.model_fields_set:
        value = getattr(payload, name)
        updates[name.upper()] = str(value).lower() if isinstance(value, bool) else str(value).strip()
    updates.update(AI_DEFAULT_PROVIDER="codex", AI_PUBLISH_PROVIDER="codex",
                   AI_CODEX_MODEL="gpt-6-astra", TRANSCRIPTION_PROVIDER="local",
                   TRANSCRIPTION_OFFLINE_ONLY="true", TRANSCRIPTION_LOCAL_FILES_ONLY="true")
    _write_env_values(updates)
    _apply_runtime_values(updates)
    return {"message": "本地转写与 Codex 设置已保存。", "config": get_ai_config_context()}
