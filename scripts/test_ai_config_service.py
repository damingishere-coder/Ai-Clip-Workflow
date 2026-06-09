from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.services.ai_config_service as ai_config_service
from app.models.settings import AIConfigUpdate


def test_save_ai_config_preserves_unrelated_env_values() -> None:
    original_env_path = ai_config_service._env_path
    original_fetch_models = ai_config_service.fetch_ollama_models
    try:
        with TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "STORAGE_ROOT=E:\\直播间切片工作流存储",
                        "VOLCENGINE_ASR_API_KEY=volcengine-secret-key-123456",
                        "AI_REMOTE_API_KEY=legacy-analysis-key-1234567890",
                        "AI_REMOTE_PUBLISH_MODEL=deepseek-chat",
                        "CUSTOM_KEEP_ME=yes",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            ai_config_service._env_path = lambda: env_path
            ai_config_service.fetch_ollama_models = lambda timeout_seconds=2: []

            ai_config_service.save_ai_config(
                AIConfigUpdate(
                    ai_analysis_remote_model="deepseek-v4-flash",
                    ai_publish_remote_model="deepseek-v4-pro",
                    ai_publish_remote_api_key="publish-key-12345678901234567890",
                )
            )
            content = env_path.read_text(encoding="utf-8")
            context = ai_config_service.get_ai_config_context()

        assert "STORAGE_ROOT=E:\\直播间切片工作流存储" in content
        assert "VOLCENGINE_ASR_API_KEY=volcengine-secret-key-123456" in content
        assert "CUSTOM_KEEP_ME=yes" in content
        assert "AI_ANALYSIS_REMOTE_API_KEY=legacy-analysis-key-1234567890" in content
        assert "AI_PUBLISH_REMOTE_MODEL=deepseek-v4-pro" in content
        assert context["values"]["AI_PUBLISH_REMOTE_API_KEY"] == "publish-key-12345678901234567890"
    finally:
        ai_config_service._env_path = original_env_path
        ai_config_service.fetch_ollama_models = original_fetch_models


def main() -> None:
    test_save_ai_config_preserves_unrelated_env_values()
    print("ai config service tests passed")


if __name__ == "__main__":
    main()
