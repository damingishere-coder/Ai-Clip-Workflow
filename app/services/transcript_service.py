from pathlib import Path


def extract_audio_placeholder(video_path: Path, output_path: Path) -> dict[str, str]:
    return {
        "status": "todo",
        "message": "后续将用 FFmpeg 从视频中提取音频。",
        "video_path": str(video_path),
        "output_path": str(output_path),
    }


def transcribe_audio_placeholder(audio_path: Path) -> list[dict[str, str]]:
    return [
        {"start": "00:00:00", "end": "00:01:00", "text": "这里会保存带时间戳的转写文本。"},
        {"start": "00:01:00", "end": "00:02:00", "text": "后续可接入 OpenAI-compatible 或本地转写服务。"},
    ]
