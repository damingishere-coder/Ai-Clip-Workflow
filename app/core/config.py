from dataclasses import dataclass
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXTERNAL_STORAGE_ROOT = Path(r"E:\直播间切片工作流存储")


def _load_env_file() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _env_first(names: tuple[str, ...], default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if not value:
        return default
    return Path(value).expanduser()


_load_env_file()


@dataclass(frozen=True)
class Settings:
    app_name: str = "NiuMa Studio"
    app_name_cn: str = "牛马片场"
    app_description: str = "Windows 本地 AI 高光生产后台"
    project_root: Path = PROJECT_ROOT
    data_dir: Path = _env_path("DATA_DIR", PROJECT_ROOT / "data")
    storage_root: Path = _env_path("STORAGE_ROOT", EXTERNAL_STORAGE_ROOT)
    tasks_dir: Path = _env_path("TASKS_DIR", _env_path("STORAGE_ROOT", EXTERNAL_STORAGE_ROOT))
    database_path: Path = _env_path(
        "DATABASE_PATH",
        _env_path("DATA_DIR", PROJECT_ROOT / "data") / "workflow.sqlite3",
    )
    ui_reference_image: Path = (
        PROJECT_ROOT
        / "docs"
        / "design"
        / "live_streaming_slicing_workflow_ui_16x9.png"
    )
    default_max_clip_minutes: int = 2
    default_candidate_count: int = 8
    default_cut_strategy: str = "accurate"
    ai_default_provider: str = _env("AI_DEFAULT_PROVIDER", "remote")
    ai_request_timeout_seconds: int = int(_env("AI_REQUEST_TIMEOUT_SECONDS", "120"))

    ai_analysis_remote_base_url: str = _env_first(
        ("AI_ANALYSIS_REMOTE_BASE_URL", "AI_REMOTE_BASE_URL"),
        "https://api.deepseek.com",
    )
    ai_analysis_remote_api_key: str = _env_first(
        ("AI_ANALYSIS_REMOTE_API_KEY", "AI_REMOTE_API_KEY", "OPENAI_API_KEY"),
        "",
    )
    ai_analysis_remote_model: str = _env_first(
        ("AI_ANALYSIS_REMOTE_MODEL", "AI_REMOTE_REVIEW_MODEL", "AI_REMOTE_MODEL"),
        "deepseek-v4-flash",
    )
    ai_analysis_remote_protocol: str = _env_first(
        ("AI_ANALYSIS_REMOTE_PROTOCOL", "AI_REMOTE_PROTOCOL"),
        "chat_completions",
    )
    ai_analysis_remote_reasoning_effort: str = _env_first(
        ("AI_ANALYSIS_REMOTE_REASONING_EFFORT", "AI_REMOTE_REASONING_EFFORT"),
        "",
    )
    ai_analysis_remote_responses_path: str = _env_first(
        ("AI_ANALYSIS_REMOTE_RESPONSES_PATH", "AI_REMOTE_RESPONSES_PATH"),
        "/v1/responses",
    )
    ai_analysis_remote_disable_response_storage: str = _env_first(
        ("AI_ANALYSIS_REMOTE_DISABLE_RESPONSE_STORAGE", "AI_REMOTE_DISABLE_RESPONSE_STORAGE"),
        "true",
    )
    ai_analysis_request_timeout_seconds: int = int(
        _env_first(("AI_ANALYSIS_REQUEST_TIMEOUT_SECONDS", "AI_REQUEST_TIMEOUT_SECONDS"), "120")
    )

    ai_publish_remote_base_url: str = _env_first(
        ("AI_PUBLISH_REMOTE_BASE_URL", "AI_REMOTE_BASE_URL"),
        "https://api.deepseek.com",
    )
    ai_publish_remote_api_key: str = _env_first(
        ("AI_PUBLISH_REMOTE_API_KEY", "AI_REMOTE_API_KEY", "OPENAI_API_KEY"),
        "",
    )
    ai_publish_remote_model: str = _env_first(
        ("AI_PUBLISH_REMOTE_MODEL", "AI_REMOTE_PUBLISH_MODEL", "AI_REMOTE_MODEL"),
        "deepseek-v4-flash",
    )
    ai_publish_remote_protocol: str = _env_first(
        ("AI_PUBLISH_REMOTE_PROTOCOL", "AI_REMOTE_PROTOCOL"),
        "chat_completions",
    )
    ai_publish_remote_reasoning_effort: str = _env_first(
        ("AI_PUBLISH_REMOTE_REASONING_EFFORT", "AI_REMOTE_REASONING_EFFORT"),
        "",
    )
    ai_publish_remote_responses_path: str = _env_first(
        ("AI_PUBLISH_REMOTE_RESPONSES_PATH", "AI_REMOTE_RESPONSES_PATH"),
        "/v1/responses",
    )
    ai_publish_remote_disable_response_storage: str = _env_first(
        ("AI_PUBLISH_REMOTE_DISABLE_RESPONSE_STORAGE", "AI_REMOTE_DISABLE_RESPONSE_STORAGE"),
        "true",
    )
    ai_publish_request_timeout_seconds: int = int(
        _env_first(("AI_PUBLISH_REQUEST_TIMEOUT_SECONDS", "AI_REQUEST_TIMEOUT_SECONDS"), "120")
    )

    # Legacy AI_REMOTE_* values are kept for older scripts and as a fallback source.
    ai_remote_base_url: str = _env("AI_REMOTE_BASE_URL", "https://api.deepseek.com")
    ai_remote_api_key: str = _env_first(("AI_REMOTE_API_KEY", "OPENAI_API_KEY"), "")
    ai_remote_model: str = _env("AI_REMOTE_MODEL", "deepseek-v4-flash")
    ai_remote_review_model: str = _env("AI_REMOTE_REVIEW_MODEL", "deepseek-v4-flash")
    ai_remote_publish_model: str = _env("AI_REMOTE_PUBLISH_MODEL", "deepseek-v4-flash")
    ai_remote_protocol: str = _env("AI_REMOTE_PROTOCOL", "chat_completions")
    ai_remote_reasoning_effort: str = _env("AI_REMOTE_REASONING_EFFORT", "")
    ai_remote_responses_path: str = _env("AI_REMOTE_RESPONSES_PATH", "/v1/responses")
    ai_remote_disable_response_storage: str = _env("AI_REMOTE_DISABLE_RESPONSE_STORAGE", "true")
    ai_local_base_url: str = _env("AI_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1")
    ai_local_api_key: str = _env("AI_LOCAL_API_KEY", "ollama")
    ai_local_model: str = _env("AI_LOCAL_MODEL", "qwen3:8b")
    ai_local_protocol: str = _env("AI_LOCAL_PROTOCOL", "chat_completions")
    ai_local_fallback_protocol: str = _env("AI_LOCAL_FALLBACK_PROTOCOL", "")
    ai_local_health_timeout_seconds: int = int(_env("AI_LOCAL_HEALTH_TIMEOUT_SECONDS", "30"))
    opencli_local_base_url: str = _env("OPENCLI_LOCAL_BASE_URL", "http://127.0.0.1:8001")
    opencli_host_bridge_url: str = _env("OPENCLI_HOST_BRIDGE_URL", "")
    ai_network_access: str = _env("AI_NETWORK_ACCESS", "enabled")
    ai_windows_wsl_setup_acknowledged: str = _env("AI_WINDOWS_WSL_SETUP_ACKNOWLEDGED", "true")
    ai_model_context_window: int = int(_env("AI_MODEL_CONTEXT_WINDOW", "1000000"))
    ai_model_auto_compact_token_limit: int = int(_env("AI_MODEL_AUTO_COMPACT_TOKEN_LIMIT", "900000"))
    transcription_provider: str = _env("TRANSCRIPTION_PROVIDER", "volcengine")
    transcription_fallback_provider: str = _env("TRANSCRIPTION_FALLBACK_PROVIDER", "")
    transcription_model: str = _env("TRANSCRIPTION_MODEL", "medium")
    transcription_language: str = _env("TRANSCRIPTION_LANGUAGE", "zh")
    transcription_device: str = _env("TRANSCRIPTION_DEVICE", "cpu")
    transcription_compute_type: str = _env("TRANSCRIPTION_COMPUTE_TYPE", "int8")
    transcription_cpu_fallback_model: str = _env("TRANSCRIPTION_CPU_FALLBACK_MODEL", "medium")
    transcription_chunk_seconds: int = int(_env("TRANSCRIPTION_CHUNK_SECONDS", "120"))
    transcription_chunk_overlap_seconds: int = int(_env("TRANSCRIPTION_CHUNK_OVERLAP_SECONDS", "5"))
    volcengine_asr_api_url: str = _env(
        "VOLCENGINE_ASR_API_URL",
        "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",
    )
    volcengine_asr_api_key: str = _env("VOLCENGINE_ASR_API_KEY", "")
    volcengine_asr_app_key: str = _env("VOLCENGINE_ASR_APP_KEY", "")
    volcengine_asr_access_key: str = _env("VOLCENGINE_ASR_ACCESS_KEY", "")
    volcengine_asr_resource_id: str = _env("VOLCENGINE_ASR_RESOURCE_ID", "volc.bigasr.auc_turbo")
    volcengine_asr_timeout_seconds: int = int(_env("VOLCENGINE_ASR_TIMEOUT_SECONDS", "300"))
    volcengine_asr_audio_format: str = _env("VOLCENGINE_ASR_AUDIO_FORMAT", "mp3")

    # === P0 安全与稳定性配置 ===

    # 本地 API 访问保护：对 /api 下写接口校验 token
    local_admin_token: str = _env("LOCAL_ADMIN_TOKEN", "")

    # 允许浏览的媒体根目录（逗号分隔的绝对路径列表）
    allowed_media_roots: str = _env("ALLOWED_MEDIA_ROOTS", "")

    # 上传文件大小限制（字节），默认 4GB
    max_upload_size_bytes: int = int(_env("MAX_UPLOAD_SIZE_BYTES", str(4 * 1024 * 1024 * 1024)))

    # 上传文件允许的扩展名（逗号分隔）
    allowed_upload_extensions: str = _env(
        "ALLOWED_UPLOAD_EXTENSIONS",
        ".mp4,.mov,.mkv,.avi,.flv,.webm,.m4v,.ts,.wav,.mp3,.aac,.flac,.ogg,.wma",
    )

    # FFmpeg / FFprobe 子进程超时（秒）
    ffmpeg_audio_extract_timeout: int = int(_env("FFMPEG_AUDIO_EXTRACT_TIMEOUT", "600"))
    ffmpeg_cut_timeout: int = int(_env("FFMPEG_CUT_TIMEOUT", "600"))
    ffmpeg_subtitle_timeout: int = int(_env("FFMPEG_SUBTITLE_TIMEOUT", "300"))
    ffmpeg_cover_timeout: int = int(_env("FFMPEG_COVER_TIMEOUT", "120"))
    ffprobe_timeout: int = int(_env("FFPROBE_TIMEOUT", "60"))
    ffmpeg_chunk_timeout: int = int(_env("FFMPEG_CHUNK_TIMEOUT", "120"))


settings = Settings()
