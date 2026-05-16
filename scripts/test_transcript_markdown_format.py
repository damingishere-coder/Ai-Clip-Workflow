from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.transcript_service import (
    TranscriptSegment,
    build_minute_rows,
    build_transcript_markdown,
    read_transcript_preview,
)


def main() -> None:
    segments = [
        TranscriptSegment(start_seconds=3.2, end_seconds=8.6, text="大家好，今天我们来讲直播切片。"),
        TranscriptSegment(start_seconds=20.0, end_seconds=28.0, text="这一段是第一分钟里的真实原文。"),
        TranscriptSegment(start_seconds=66.0, end_seconds=72.0, text="这里进入第二分钟。"),
    ]
    rows = build_minute_rows(segments)
    assert rows == [
        (0, 60, "大家好，今天我们来讲直播切片。 这一段是第一分钟里的真实原文。"),
        (60, 120, "这里进入第二分钟。"),
    ]

    markdown = build_transcript_markdown(
        {"id": "test-task", "task_name": "测试任务", "source": "source.mp4"},
        Path("audio/source.wav"),
        segments,
    )
    assert "## 分钟级转写" in markdown
    assert "## 逐句时间戳原文" in markdown
    assert "| 00:00:00 | 00:01:00 | 大家好，今天我们来讲直播切片。 这一段是第一分钟里的真实原文。 |" in markdown
    assert "| 00:01:06 | 00:01:12 | 这里进入第二分钟。 |" in markdown

    with TemporaryDirectory() as temp_dir:
        transcript_path = Path(temp_dir) / "transcript.md"
        transcript_path.write_text(markdown, encoding="utf-8")
        preview = read_transcript_preview(transcript_path, max_lines=3)

    assert preview[0] == {
        "time": "00:00:00 - 00:01:00",
        "text": "大家好，今天我们来讲直播切片。 这一段是第一分钟里的真实原文。",
    }
    assert preview[1] == {
        "time": "00:01:00 - 00:02:00",
        "text": "这里进入第二分钟。",
    }
    print("转写 Markdown 格式测试通过")


if __name__ == "__main__":
    main()
