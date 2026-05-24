import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.services.transcript_service as transcript_service
from app.services.transcript_service import (
    TranscriptSegment,
    parse_volcengine_transcript_segments,
    transcribe_audio_with_configured_provider,
)


def test_parse_volcengine_utterances() -> None:
    payload = {
        "result": {
            "text": "大家好，今天我们开始直播。",
            "utterances": [
                {
                    "start_time": 0,
                    "end_time": 1500,
                    "text": "大家好",
                },
                {
                    "start_time": 1500,
                    "end_time": 4200,
                    "text": "今天我们开始直播",
                },
            ],
        }
    }

    segments = parse_volcengine_transcript_segments(payload)

    assert len(segments) == 2
    assert segments[0].start_seconds == 0
    assert segments[0].end_seconds == 1.5
    assert segments[1].start_seconds == 1.5
    assert segments[1].end_seconds == 4.2
    assert segments[1].text == "今天我们开始直播"


def test_remote_failure_falls_back_to_local() -> None:
    original_provider = transcript_service.settings.transcription_provider
    original_fallback = transcript_service.settings.transcription_fallback_provider
    original_remote = transcript_service.transcribe_audio_with_volcengine
    original_local = transcript_service.transcribe_audio_in_chunks

    def fake_remote(*_args, **_kwargs):
        raise RuntimeError("模拟火山引擎不可用")

    def fake_local(*_args, **_kwargs):
        return [TranscriptSegment(start_seconds=0, end_seconds=2, text="本地兜底成功")]

    try:
        object.__setattr__(transcript_service.settings, "transcription_provider", "volcengine")
        object.__setattr__(transcript_service.settings, "transcription_fallback_provider", "local")
        transcript_service.transcribe_audio_with_volcengine = fake_remote
        transcript_service.transcribe_audio_in_chunks = fake_local

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            audio_path = temp_path / "source.wav"
            progress_path = temp_path / "transcript_progress.json"
            audio_path.write_bytes(b"fake audio")

            segments = transcribe_audio_with_configured_provider(audio_path, temp_path, progress_path)

        assert len(segments) == 1
        assert segments[0].text == "本地兜底成功"
    finally:
        object.__setattr__(transcript_service.settings, "transcription_provider", original_provider)
        object.__setattr__(transcript_service.settings, "transcription_fallback_provider", original_fallback)
        transcript_service.transcribe_audio_with_volcengine = original_remote
        transcript_service.transcribe_audio_in_chunks = original_local


def test_explicit_remote_provider_does_not_fall_back() -> None:
    original_remote = transcript_service.transcribe_audio_with_volcengine
    original_local = transcript_service.transcribe_audio_in_chunks

    def fake_remote(*_args, **_kwargs):
        raise RuntimeError("模拟火山引擎不可用")

    def fake_local(*_args, **_kwargs):
        raise AssertionError("显式选择火山引擎时不应该自动走本地兜底")

    try:
        transcript_service.transcribe_audio_with_volcengine = fake_remote
        transcript_service.transcribe_audio_in_chunks = fake_local

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            audio_path = temp_path / "source.wav"
            progress_path = temp_path / "transcript_progress.json"
            audio_path.write_bytes(b"fake audio")
            try:
                transcribe_audio_with_configured_provider(
                    audio_path,
                    temp_path,
                    progress_path,
                    provider="volcengine",
                )
            except RuntimeError as exc:
                assert "模拟火山引擎不可用" in str(exc)
            else:
                raise AssertionError("显式远程转写失败时应该抛出远程错误")
    finally:
        transcript_service.transcribe_audio_with_volcengine = original_remote
        transcript_service.transcribe_audio_in_chunks = original_local


def test_remote_provider_uploads_single_prepared_file() -> None:
    original_ensure = transcript_service._ensure_volcengine_configured
    original_duration = transcript_service.get_audio_duration_seconds
    original_build_chunks = transcript_service.build_transcript_chunks
    original_prepare = transcript_service._prepare_remote_audio_file
    original_validate = transcript_service._validate_volcengine_flash_upload
    original_flash = transcript_service.transcribe_audio_with_volcengine_flash
    calls: list[str] = []

    def fake_build_chunks(*_args, **_kwargs):
        raise AssertionError("火山极速版远程转写不应该再走分段逻辑")

    def fake_prepare(_source: Path, output: Path) -> None:
        calls.append("prepare")
        output.write_bytes(b"fake compressed audio")

    def fake_validate(_path: Path, _size_bytes: int | None = None) -> None:
        calls.append("validate")

    def fake_flash(_path: Path, allow_empty: bool = False):
        calls.append("flash")
        assert allow_empty is False
        return [TranscriptSegment(start_seconds=0, end_seconds=2, text="远程整文件成功")]

    try:
        transcript_service._ensure_volcengine_configured = lambda: None
        transcript_service.get_audio_duration_seconds = lambda _path: 3600
        transcript_service.build_transcript_chunks = fake_build_chunks
        transcript_service._prepare_remote_audio_file = fake_prepare
        transcript_service._validate_volcengine_flash_upload = fake_validate
        transcript_service.transcribe_audio_with_volcengine_flash = fake_flash

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            audio_path = temp_path / "source.wav"
            progress_path = temp_path / "transcript_progress.json"
            audio_path.write_bytes(b"fake audio")

            segments = transcript_service.transcribe_audio_with_volcengine(audio_path, temp_path, progress_path)
            progress = json.loads(progress_path.read_text(encoding="utf-8"))

        assert calls == ["prepare", "validate", "flash"]
        assert len(segments) == 1
        assert segments[0].text == "远程整文件成功"
        assert progress["current_chunk"] == 1
        assert progress["total_chunks"] == 1
    finally:
        transcript_service._ensure_volcengine_configured = original_ensure
        transcript_service.get_audio_duration_seconds = original_duration
        transcript_service.build_transcript_chunks = original_build_chunks
        transcript_service._prepare_remote_audio_file = original_prepare
        transcript_service._validate_volcengine_flash_upload = original_validate
        transcript_service.transcribe_audio_with_volcengine_flash = original_flash


def main() -> None:
    test_parse_volcengine_utterances()
    test_remote_failure_falls_back_to_local()
    test_explicit_remote_provider_does_not_fall_back()
    test_remote_provider_uploads_single_prepared_file()
    print("volcengine transcription provider tests passed")


if __name__ == "__main__":
    main()
