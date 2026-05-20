from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.services.transcript_service as transcript_service


class FakeSegment:
    start = 1.0
    end = 2.5
    text = "fallback ok"


class FailingGpuModel:
    def transcribe(self, *_args, **_kwargs):
        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")


class WorkingCpuModel:
    def transcribe(self, *_args, **_kwargs):
        return [FakeSegment()], None


def main() -> None:
    original_get_whisper_model = transcript_service._get_whisper_model
    original_should_retry = transcript_service._should_retry_with_cpu
    original_model_key = transcript_service._WHISPER_MODEL_KEY
    requested_keys = []

    def fake_get_whisper_model(model_key=None):
        requested_keys.append(model_key)
        if model_key == transcript_service._cpu_fallback_model_key():
            transcript_service._WHISPER_MODEL_KEY = model_key
            return WorkingCpuModel()
        return FailingGpuModel()

    try:
        transcript_service._get_whisper_model = fake_get_whisper_model
        transcript_service._should_retry_with_cpu = lambda _exc: True
        segments = transcript_service.transcribe_audio(Path("missing-but-mocked.wav"))
    finally:
        transcript_service._get_whisper_model = original_get_whisper_model
        transcript_service._should_retry_with_cpu = original_should_retry
        transcript_service._WHISPER_MODEL_KEY = original_model_key

    assert requested_keys == [None, transcript_service._cpu_fallback_model_key()]
    assert len(segments) == 1
    assert segments[0].text == "fallback ok"
    print("transcript CPU fallback test passed")


if __name__ == "__main__":
    main()
