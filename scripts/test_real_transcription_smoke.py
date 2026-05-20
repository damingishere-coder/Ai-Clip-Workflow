from __future__ import annotations

import argparse
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.transcript_service import (
    TranscriptChunk,
    _extract_audio_chunk,
    transcribe_audio,
    write_transcript_markdown,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用真实音频验证 faster-whisper 是否能转写。")
    parser.add_argument("audio_path", help="要测试的音频文件路径，例如 E:\\...\\audio\\source.wav")
    parser.add_argument("--seconds", type=int, default=20, help="截取前多少秒做测试，默认 20 秒")
    parser.add_argument("--write-md", action="store_true", help="同时生成一个测试 transcript.md")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    audio_path = Path(args.audio_path)
    if not audio_path.exists():
        raise SystemExit(f"音频文件不存在：{audio_path}")

    with TemporaryDirectory(prefix="real_transcript_smoke_") as temp_dir:
        temp_path = Path(temp_dir)
        clip_path = temp_path / "smoke.wav"
        _extract_audio_chunk(
            audio_path,
            clip_path,
            TranscriptChunk(index=1, start_seconds=0, end_seconds=max(1, args.seconds)),
        )
        segments = transcribe_audio(clip_path)
        print(f"真实转写成功，识别到 {len(segments)} 句。")
        for segment in segments[:5]:
            print(f"{segment.start_seconds:.1f}-{segment.end_seconds:.1f}: {segment.text}")

        if args.write_md:
            transcript_path = temp_path / "transcript.md"
            write_transcript_markdown(
                {"id": "smoke-test", "task_name": "真实转写冒烟测试", "source": str(audio_path)},
                clip_path,
                transcript_path,
            )
            print(f"测试 Markdown 已生成：{transcript_path}")


if __name__ == "__main__":
    main()
