from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.services.transcript_service as transcript_service
from app.services.transcript_service import TranscriptSegment, build_transcript_chunks


def test_chunk_plan() -> None:
    chunks = build_transcript_chunks(65 * 60, chunk_seconds=600, overlap_seconds=5)
    assert len(chunks) == 7
    assert chunks[0].start_seconds == 0
    assert chunks[0].end_seconds == 600
    assert chunks[1].start_seconds == 595
    assert chunks[-1].end_seconds == 65 * 60


def test_chunk_offset() -> None:
    chunk = build_transcript_chunks(65 * 60, chunk_seconds=600, overlap_seconds=5)[1]
    segments = [
        TranscriptSegment(start_seconds=2, end_seconds=4, text="overlap"),
        TranscriptSegment(start_seconds=10, end_seconds=20, text="kept"),
    ]
    adjusted = transcript_service._offset_chunk_segments(segments, chunk, overlap_seconds=5)
    assert len(adjusted) == 1
    assert adjusted[0].start_seconds == 605
    assert adjusted[0].end_seconds == 615
    assert adjusted[0].text == "kept"


def test_markdown_from_chunked_transcript() -> None:
    original_duration = transcript_service.get_audio_duration_seconds
    original_extract = transcript_service._extract_audio_chunk
    original_transcribe = transcript_service.transcribe_audio
    original_model_key = transcript_service._WHISPER_MODEL_KEY
    original_chunk_seconds = transcript_service.settings.transcription_chunk_seconds
    original_overlap_seconds = transcript_service.settings.transcription_chunk_overlap_seconds

    def fake_duration(_audio_path: Path) -> float:
        return 65 * 60

    def fake_extract(_audio_path, chunk_path, _chunk) -> None:
        chunk_path.write_bytes(b"fake wav")

    def fake_transcribe(chunk_path: Path, allow_empty: bool = False):
        chunk_index = int(chunk_path.stem.split("_")[-1])
        return [
            TranscriptSegment(
                start_seconds=10,
                end_seconds=20,
                text=f"chunk {chunk_index}",
            )
        ]

    try:
        transcript_service.get_audio_duration_seconds = fake_duration
        transcript_service._extract_audio_chunk = fake_extract
        transcript_service.transcribe_audio = fake_transcribe
        transcript_service._WHISPER_MODEL_KEY = ("medium", "cpu", "int8")
        object.__setattr__(transcript_service.settings, "transcription_chunk_seconds", 600)
        object.__setattr__(transcript_service.settings, "transcription_chunk_overlap_seconds", 5)

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            audio_path = temp_path / "source.wav"
            transcript_path = temp_path / "transcript.md"
            audio_path.write_bytes(b"fake audio")

            result = transcript_service.write_transcript_markdown(
                {"id": "task-1", "task_name": "chunk test", "source": "source.mp4"},
                audio_path,
                transcript_path,
            )
            progress = transcript_service.read_transcript_progress(transcript_path)
            markdown = transcript_path.read_text(encoding="utf-8")

        assert result["segment_count"] == "7"
        assert progress["status"] == "completed"
        assert progress["current_chunk"] == 7
        assert progress["total_chunks"] == 7
        assert progress["percent"] == 100
        assert "chunk 1" in markdown
        assert "chunk 7" in markdown
        assert "| 00:10:05 | 00:10:15 | chunk 2 |" in markdown
    finally:
        transcript_service.get_audio_duration_seconds = original_duration
        transcript_service._extract_audio_chunk = original_extract
        transcript_service.transcribe_audio = original_transcribe
        transcript_service._WHISPER_MODEL_KEY = original_model_key
        object.__setattr__(transcript_service.settings, "transcription_chunk_seconds", original_chunk_seconds)
        object.__setattr__(transcript_service.settings, "transcription_chunk_overlap_seconds", original_overlap_seconds)


def main() -> None:
    test_chunk_plan()
    test_chunk_offset()
    test_markdown_from_chunked_transcript()
    print("transcript chunking tests passed")


if __name__ == "__main__":
    main()
