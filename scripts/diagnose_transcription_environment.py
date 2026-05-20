from __future__ import annotations

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings


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
    print(f"TRANSCRIPTION_MODEL={settings.transcription_model}")
    print(f"TRANSCRIPTION_LANGUAGE={settings.transcription_language}")
    print(f"TRANSCRIPTION_DEVICE={settings.transcription_device}")
    print(f"TRANSCRIPTION_COMPUTE_TYPE={settings.transcription_compute_type}")
    print(f"TRANSCRIPTION_CPU_FALLBACK_MODEL={settings.transcription_cpu_fallback_model}")
    print(f"TRANSCRIPTION_CHUNK_SECONDS={settings.transcription_chunk_seconds}")
    print(f"TRANSCRIPTION_CHUNK_OVERLAP_SECONDS={settings.transcription_chunk_overlap_seconds}")

    if settings.transcription_device.lower() == "cuda":
        print()
        print("提示：当前配置使用 CUDA。如果仍然看到 cublas64_12.dll 或 cudnn 错误，请先改用 CPU/int8 跑通。")


if __name__ == "__main__":
    main()
