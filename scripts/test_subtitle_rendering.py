import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import task_service  # noqa: E402


def main() -> None:
    assert task_service._hex_to_ass_color("#112233") == "&H00332211"
    assert task_service._hex_to_ass_color("ffffff") == "&H00FFFFFF"
    assert task_service._ass_time(65.34) == "0:01:05.34"
    assert task_service._escape_ass_text("line {one}\nline two") == r"line \{one\}\Nline two"

    original_get_clip_candidate = task_service.get_clip_candidate
    original_read_transcript_range = task_service.read_transcript_range
    original_get_artifact_paths = task_service.get_artifact_paths

    def fake_get_clip_candidate(task_id: str, clip_candidate_id: str) -> dict:
        assert task_id == "subtitle-task"
        assert clip_candidate_id == "clip-001"
        return {
            "start_seconds": 10,
            "end_seconds": 20,
            "summary": "Fallback summary",
            "title": "Fallback title",
        }

    def fake_read_transcript_range(path: Path, start: int, end: int, max_rows: int = 120) -> list[dict]:
        assert path == Path("transcript.md")
        assert (start, end, max_rows) == (10, 20, 120)
        return [
            {"start_time": "00:00:09", "end_time": "00:00:12", "text": "first subtitle"},
            {"start_time": "00:00:18", "end_time": "00:00:25", "text": "second subtitle"},
        ]

    def fake_get_artifact_paths(task_id: str) -> dict:
        assert task_id == "subtitle-task"
        return {"transcript_path": Path("transcript.md")}

    task_service.get_clip_candidate = fake_get_clip_candidate
    task_service.read_transcript_range = fake_read_transcript_range
    task_service.get_artifact_paths = fake_get_artifact_paths
    try:
        clip_start, rows = task_service._build_subtitle_rows(
            "subtitle-task",
            {"clip_candidate_id": "clip-001", "output_file_name": "clip.mp4"},
        )
    finally:
        task_service.get_clip_candidate = original_get_clip_candidate
        task_service.read_transcript_range = original_read_transcript_range
        task_service.get_artifact_paths = original_get_artifact_paths

    assert clip_start == 10
    assert rows == [
        {"start_seconds": 0, "end_seconds": 2, "text": "first subtitle"},
        {"start_seconds": 8, "end_seconds": 10, "text": "second subtitle"},
    ]
    print("subtitle rendering test passed")


if __name__ == "__main__":
    main()
