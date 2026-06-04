from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.services.transcript_service as transcript_service
from app.services.transcript_service import (
    TranscriptSegment,
    _build_volcengine_flash_payload,
    _volcengine_headers,
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


def test_volcengine_flash_request_shape() -> None:
    with TemporaryDirectory() as temp_dir:
        audio_path = Path(temp_dir) / "sample.mp3"
        audio_path.write_bytes(b"ID3fake-audio")

        headers = _volcengine_headers()
        payload = _build_volcengine_flash_payload(audio_path)

    assert headers["Content-Type"] == "application/json"
    assert headers["X-Api-Sequence"] == "-1"
    assert payload["request"]["model_name"] == "bigmodel"
    assert payload["audio"]["data"] == "SUQzZmFrZS1hdWRpbw=="


def main() -> None:
    test_parse_volcengine_utterances()
    test_remote_failure_falls_back_to_local()
    test_volcengine_flash_request_shape()
    print("volcengine transcription provider tests passed")


if __name__ == "__main__":
    main()
