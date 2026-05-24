from __future__ import annotations

import argparse
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.services.transcript_service import (
    TranscriptChunk,
    _extract_audio_chunk,
    transcribe_audio_with_volcengine,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用短音频验证火山引擎远程转写是否可用。")
    parser.add_argument("audio_path", help="要测试的音频文件路径，例如 E:\\...\\audio\\source.wav")
    parser.add_argument("--seconds", type=int, default=20, help="截取前多少秒做测试，默认 20 秒")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    has_api_key = bool(settings.volcengine_asr_api_key)
    has_app_token = bool(settings.volcengine_asr_app_key and settings.volcengine_asr_access_key)
    if not has_api_key and not has_app_token:
        raise SystemExit(
            "未填写火山引擎转写密钥。请在 .env 中填写 VOLCENGINE_ASR_API_KEY，"
            "或填写 VOLCENGINE_ASR_APP_KEY + VOLCENGINE_ASR_ACCESS_KEY。"
        )

    audio_path = Path(args.audio_path)
    if not audio_path.exists():
        raise SystemExit(f"音频文件不存在：{audio_path}")

    with TemporaryDirectory(prefix="volcengine_asr_smoke_") as temp_dir:
        temp_path = Path(temp_dir)
        clip_path = temp_path / "source.wav"
        progress_path = temp_path / "transcript_progress.json"
        _extract_audio_chunk(
            audio_path,
            clip_path,
            TranscriptChunk(index=1, start_seconds=0, end_seconds=max(1, args.seconds)),
        )
        segments = transcribe_audio_with_volcengine(clip_path, temp_path, progress_path)

    print(f"火山引擎远程转写成功，识别到 {len(segments)} 句。")
    for segment in segments[:5]:
        print(f"{segment.start_seconds:.1f}-{segment.end_seconds:.1f}: {segment.text}")


if __name__ == "__main__":
    main()
