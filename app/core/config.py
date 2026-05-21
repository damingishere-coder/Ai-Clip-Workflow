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
    app_name: str = "Live Streaming Slicing Workflow"
    app_name_cn: str = "直播切片工作流"
    app_description: str = "Windows 本地直播长视频自动切片工作流系统"
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
    ai_remote_base_url: str = _env("AI_REMOTE_BASE_URL", "https://ai.oneinfinityai.com")
    ai_remote_api_key: str = _env_first(("AI_REMOTE_API_KEY", "OPENAI_API_KEY"), "")
    ai_remote_model: str = _env("AI_REMOTE_MODEL", "gpt-5.5")
    ai_remote_review_model: str = _env("AI_REMOTE_REVIEW_MODEL", "gpt-5.5")
    ai_remote_protocol: str = _env("AI_REMOTE_PROTOCOL", "responses")
    ai_remote_reasoning_effort: str = _env("AI_REMOTE_REASONING_EFFORT", "xhigh")
    ai_remote_responses_path: str = _env("AI_REMOTE_RESPONSES_PATH", "/v1/responses")
    ai_remote_disable_response_storage: str = _env("AI_REMOTE_DISABLE_RESPONSE_STORAGE", "true")
    ai_local_base_url: str = _env("AI_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1")
    ai_local_api_key: str = _env("AI_LOCAL_API_KEY", "ollama")
    ai_local_model: str = _env("AI_LOCAL_MODEL", "qwen3:8b")
    ai_local_protocol: str = _env("AI_LOCAL_PROTOCOL", "chat_completions")
    ai_local_fallback_protocol: str = _env("AI_LOCAL_FALLBACK_PROTOCOL", "")
    ai_local_health_timeout_seconds: int = int(_env("AI_LOCAL_HEALTH_TIMEOUT_SECONDS", "30"))
    ai_network_access: str = _env("AI_NETWORK_ACCESS", "enabled")
    ai_windows_wsl_setup_acknowledged: str = _env("AI_WINDOWS_WSL_SETUP_ACKNOWLEDGED", "true")
    ai_model_context_window: int = int(_env("AI_MODEL_CONTEXT_WINDOW", "1000000"))
    ai_model_auto_compact_token_limit: int = int(_env("AI_MODEL_AUTO_COMPACT_TOKEN_LIMIT", "900000"))
    transcription_model: str = _env("TRANSCRIPTION_MODEL", "medium")
    transcription_language: str = _env("TRANSCRIPTION_LANGUAGE", "zh")
    transcription_device: str = _env("TRANSCRIPTION_DEVICE", "cpu")
    transcription_compute_type: str = _env("TRANSCRIPTION_COMPUTE_TYPE", "int8")
    transcription_cpu_fallback_model: str = _env("TRANSCRIPTION_CPU_FALLBACK_MODEL", "medium")
    transcription_chunk_seconds: int = int(_env("TRANSCRIPTION_CHUNK_SECONDS", "120"))
    transcription_chunk_overlap_seconds: int = int(_env("TRANSCRIPTION_CHUNK_OVERLAP_SECONDS", "5"))


settings = Settings()
