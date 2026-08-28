from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


_LOCAL_AI_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "host.containers.internal"}
_REMOTE_URL_FIELDS = (
    "ai_analysis_remote_base_url",
    "ai_publish_remote_base_url",
    "ai_remote_base_url",
)
_RESPONSE_PATH_FIELDS = (
    "ai_analysis_remote_responses_path",
    "ai_publish_remote_responses_path",
    "ai_remote_responses_path",
)


def _validate_http_url(value: str, *, local_only: bool = False, https_only: bool = False) -> str:
    text = value.strip()
    if not text:
        return text
    parsed = urlsplit(text)
    hostname = str(parsed.hostname or "").lower().rstrip(".")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("URL 端口无效") from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ValueError("URL 必须是完整的 http:// 或 https:// 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("URL 不能包含账号、密码、查询参数或片段")
    if https_only and parsed.scheme != "https":
        raise ValueError("该接口地址必须使用 https://")
    if local_only and hostname not in _LOCAL_AI_HOSTS:
        raise ValueError("本地 AI 地址只能使用 localhost、127.0.0.1 或容器宿主机地址")
    if not local_only and parsed.scheme == "http" and hostname not in _LOCAL_AI_HOSTS:
        raise ValueError("非本机接口地址必须使用 https://")
    return text


class AIConfigUpdate(BaseModel):
    ai_default_provider: str = Field(default="codex", pattern="^(codex|remote|local)$", max_length=20)
    ai_publish_provider: str = Field(default="codex", pattern="^(codex|remote|local)$", max_length=20)
    ai_request_timeout_seconds: int = Field(default=120, ge=10, le=600)
    ai_codex_path: str = Field(default="codex", max_length=1000)
    ai_codex_home: str = Field(default="", max_length=1000)
    ai_codex_model: str = Field(default="gpt-5.6-sol", max_length=200)
    ai_codex_timeout_seconds: int = Field(default=300, ge=10, le=1800)

    transcription_provider: str = Field(default="volcengine", pattern="^(volcengine|local)$", max_length=20)
    transcription_fallback_provider: str = Field(default="", pattern="^(|volcengine|local)$", max_length=20)
    volcengine_asr_api_url: str = Field(default="", max_length=2000)
    volcengine_asr_api_key: str = Field(default="", max_length=4096)
    volcengine_asr_app_key: str = Field(default="", max_length=4096)
    volcengine_asr_access_key: str = Field(default="", max_length=4096)
    volcengine_asr_resource_id: str = Field(default="volc.bigasr.auc_turbo", max_length=200)
    volcengine_asr_timeout_seconds: int = Field(default=300, ge=30, le=1800)
    volcengine_asr_audio_format: str = Field(default="mp3", pattern="^(mp3|ogg)$", max_length=10)

    ai_analysis_remote_base_url: str = Field(default="", max_length=2000)
    ai_analysis_remote_api_key: str = Field(default="", max_length=4096)
    ai_analysis_remote_model: str = Field(default="deepseek-v4-flash", max_length=200)
    ai_analysis_remote_protocol: str = Field(
        default="chat_completions", pattern="^(chat_completions|responses)$", max_length=30
    )
    ai_analysis_remote_reasoning_effort: str = Field(default="", max_length=80)
    ai_analysis_remote_responses_path: str = Field(default="/v1/responses", max_length=300)
    ai_analysis_remote_disable_response_storage: bool = Field(default=True)
    ai_analysis_request_timeout_seconds: int = Field(default=120, ge=10, le=600)

    ai_publish_remote_base_url: str = Field(default="", max_length=2000)
    ai_publish_remote_api_key: str = Field(default="", max_length=4096)
    ai_publish_remote_model: str = Field(default="deepseek-v4-flash", max_length=200)
    ai_publish_remote_protocol: str = Field(
        default="chat_completions", pattern="^(chat_completions|responses)$", max_length=30
    )
    ai_publish_remote_reasoning_effort: str = Field(default="", max_length=80)
    ai_publish_remote_responses_path: str = Field(default="/v1/responses", max_length=300)
    ai_publish_remote_disable_response_storage: bool = Field(default=True)
    ai_publish_request_timeout_seconds: int = Field(default=120, ge=10, le=600)

    # Legacy fields are accepted so older pages or scripts do not break.
    ai_remote_base_url: str = Field(default="", max_length=2000)
    ai_remote_api_key: str = Field(default="", max_length=4096)
    ai_remote_model: str = Field(default="deepseek-v4-flash", max_length=200)
    ai_remote_review_model: str = Field(default="deepseek-v4-flash", max_length=200)
    ai_remote_publish_model: str = Field(default="deepseek-v4-flash", max_length=200)
    ai_remote_protocol: str = Field(default="chat_completions", pattern="^(chat_completions|responses)$", max_length=30)
    ai_remote_reasoning_effort: str = Field(default="", max_length=80)
    ai_remote_responses_path: str = Field(default="/v1/responses", max_length=300)
    ai_remote_disable_response_storage: bool = Field(default=True)
    ai_local_base_url: str = Field(default="", max_length=2000)
    ai_local_api_key: str = Field(default="", max_length=4096)
    ai_local_model: str = Field(default="", max_length=200)
    ai_local_protocol: str = Field(default="chat_completions", pattern="^(chat_completions|responses)$", max_length=30)
    ai_local_fallback_protocol: str = Field(default="", pattern="^(|chat_completions|responses)$", max_length=30)
    ai_local_health_timeout_seconds: int = Field(default=30, ge=3, le=120)
    ai_network_access: str = Field(default="enabled", pattern="^(enabled|disabled)$", max_length=20)
    ai_windows_wsl_setup_acknowledged: bool = Field(default=True)
    ai_model_context_window: int = Field(default=1000000, ge=1, le=10000000)
    ai_model_auto_compact_token_limit: int = Field(default=900000, ge=1, le=10000000)

    @field_validator("*", mode="before")
    @classmethod
    def reject_control_characters(cls, value):
        if isinstance(value, str) and any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("配置值不能包含换行或控制字符")
        return value

    @field_validator("volcengine_asr_api_url")
    @classmethod
    def validate_volcengine_url(cls, value: str) -> str:
        return _validate_http_url(value, https_only=True)

    @field_validator(*_REMOTE_URL_FIELDS)
    @classmethod
    def validate_remote_url(cls, value: str) -> str:
        return _validate_http_url(value)

    @field_validator("ai_local_base_url")
    @classmethod
    def validate_local_url(cls, value: str) -> str:
        return _validate_http_url(value, local_only=True)

    @field_validator(*_RESPONSE_PATH_FIELDS)
    @classmethod
    def validate_response_path(cls, value: str) -> str:
        text = value.strip()
        if not text.startswith("/") or text.startswith("//") or "\\" in text or "://" in text:
            raise ValueError("Responses 路径必须是站内绝对路径，例如 /v1/responses")
        return text
