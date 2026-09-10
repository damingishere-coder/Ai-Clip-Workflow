from __future__ import annotations

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.local_transcription_runtime import get_local_transcription_runtime_status  # noqa: E402


def check_command(name: str) -> tuple[bool, str]:
    path = shutil.which(name)
    return bool(path), path or "未找到"


def check_import(module_name: str) -> tuple[bool, str]:
    try:
        __import__(module_name)
    except Exception as exc:
        return False, str(exc)
    return True, "可导入"


def main() -> None:
    print("=== 转写环境检查 ===")
    print(f"项目目录：{PROJECT_ROOT}")
    print(f"当前 Python：{sys.executable}")
    print(f"是否使用项目 .venv：{'是' if '.venv' in sys.executable else '否'}")
    print()

    for command in ("ffmpeg", "ffprobe"):
        ok, detail = check_command(command)
        print(f"{command}：{'正常' if ok else '异常'} - {detail}")

    for module in ("faster_whisper", "pydantic", "fastapi"):
        ok, detail = check_import(module)
        print(f"Python 依赖 {module}：{'正常' if ok else '异常'} - {detail}")

    print()
    print("=== 当前转写配置 ===")
    print(f"TRANSCRIPTION_PROVIDER={settings.transcription_provider}")
    print(f"TRANSCRIPTION_FALLBACK_PROVIDER={settings.transcription_fallback_provider}")
    print(f"TRANSCRIPTION_OFFLINE_ONLY={settings.transcription_offline_only}")
    print(f"TRANSCRIPTION_MODEL={settings.transcription_model}")
    print(f"TRANSCRIPTION_MODEL_REVISION={settings.transcription_model_revision}")
    print(f"TRANSCRIPTION_MODEL_CACHE_DIR={settings.transcription_model_cache_dir}")
    print(f"TRANSCRIPTION_LOCAL_FILES_ONLY={settings.transcription_local_files_only}")
    print(f"TRANSCRIPTION_LANGUAGE={settings.transcription_language}")
    print(f"TRANSCRIPTION_DEVICE={settings.transcription_device}")
    print(f"TRANSCRIPTION_COMPUTE_TYPE={settings.transcription_compute_type}")
    print(f"TRANSCRIPTION_CPU_FALLBACK_MODEL={settings.transcription_cpu_fallback_model}")
    print(f"TRANSCRIPTION_CHUNK_SECONDS={settings.transcription_chunk_seconds}")
    print(f"TRANSCRIPTION_CHUNK_OVERLAP_SECONDS={settings.transcription_chunk_overlap_seconds}")
    print(f"VOLCENGINE_ASR_API_URL={settings.volcengine_asr_api_url}")
    print(f"VOLCENGINE_ASR_RESOURCE_ID={settings.volcengine_asr_resource_id}")
    print(f"VOLCENGINE_ASR_API_KEY={'已填写' if settings.volcengine_asr_api_key else '未填写'}")
    print(f"VOLCENGINE_ASR_AUDIO_FORMAT={settings.volcengine_asr_audio_format}")

    runtime = get_local_transcription_runtime_status()
    print()
    print("=== 本地离线转写就绪状态 ===")
    print(f"模型固定身份：{runtime['model_identity']}")
    print(f"模型已缓存：{'是' if runtime['model_ready'] else '否'}")
    print(f"GPU 可用：{'是' if runtime['gpu_ready'] else '否'}")
    print(f"CUDA 设备数：{runtime['cuda_device_count']}")
    print(f"cuBLAS 12：{'正常' if runtime['cublas_ready'] else '缺失'}")
    print(f"cuDNN 9：{'正常' if runtime['cudnn_ready'] else '缺失'}")
    print(f"外部转写费用：{runtime['external_cost']}")
    if runtime["errors"]:
        print("待处理：" + "；".join(runtime["errors"]))


if __name__ == "__main__":
    main()
