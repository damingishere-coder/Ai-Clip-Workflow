from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.core.transcription_defaults import (
    CPU_FALLBACK_TRANSCRIPTION_MODEL_REVISION,
    PRIMARY_TRANSCRIPTION_MODEL_REVISION,
)
from app.main import app
from app.models.settings import AIConfigUpdate
from app.services import local_transcription_runtime, transcript_service
from app.services.local_transcription_runtime import (
    MODEL_VOCABULARY_FILES,
    REQUIRED_MODEL_FILES,
    TranscriptionOfflinePolicyError,
    get_local_transcription_runtime_status,
    model_cache_directory,
    model_files_ready,
    model_identity,
    resolve_local_model_source,
)
from app.services.transcript_service import TranscriptChunk
from app.services.transcript_workflow_service import (
    get_task_transcript_status,
    validate_transcription_provider_choice,
)
from app.services.transcription_checkpoint_service import (
    TranscriptionCheckpoint,
    fingerprint_file_full,
)
from scripts import setup_local_transcription
from scripts.normalize_transcript_simplified import normalize_transcript_file


@contextmanager
def _override_settings(**values):
    previous = {name: getattr(settings, name) for name in values}
    try:
        for name, value in values.items():
            object.__setattr__(settings, name, value)
        yield
    finally:
        for name, value in previous.items():
            object.__setattr__(settings, name, value)


def _write_model_files(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_MODEL_FILES:
        (target / name).write_bytes(b"test")
    (target / MODEL_VOCABULARY_FILES[0]).write_bytes(b"test")


def test_default_transcription_configuration_is_fully_offline_local() -> None:
    assert settings.transcription_provider == "local"
    assert settings.transcription_fallback_provider == ""
    assert settings.transcription_offline_only is True
    assert settings.transcription_local_files_only is True
    assert settings.transcription_model == "large-v3"
    assert settings.transcription_model_revision == PRIMARY_TRANSCRIPTION_MODEL_REVISION
    assert settings.transcription_device == "cuda"
    assert settings.transcription_compute_type == "float16"
    assert settings.transcription_chunk_seconds == 120
    assert settings.transcription_chunk_overlap_seconds == 5


def test_local_transcription_converts_segments_and_words_to_simplified(tmp_path) -> None:
    class FakeModel:
        def transcribe(self, *_args, **_kwargs):
            word_items = [
                SimpleNamespace(start=0.0, end=0.5, word="老師", probability=0.9),
                SimpleNamespace(start=0.5, end=1.0, word="計程車", probability=0.8),
                SimpleNamespace(start=1.0, end=1.5, word=" ABC-123 ", probability=0.7),
            ]
            segment = SimpleNamespace(
                start=0.0,
                end=1.5,
                text="老師讓計程車載著軟體 ABC-123",
                words=word_items,
                avg_logprob=-0.1,
            )
            return [segment], SimpleNamespace()

    result = transcript_service._transcribe_audio_with_model(FakeModel(), tmp_path / "audio.wav")

    assert result[0].text == "老师让计程车载著软体 ABC-123"
    assert [word.text for word in result[0].words] == ["老师", "计程车", "ABC-123"]
    assert "计程车" in result[0].text
    assert "软体" in result[0].text


def test_transcript_file_conversion_backs_up_and_preserves_markdown_structure(tmp_path) -> None:
    transcript_path = tmp_path / "transcript.md"
    original = (
        "# 逐句時間戳原文\r\n\r\n"
        "| 開始 | 結束 | 文本 |\r\n"
        "| --- | --- | --- |\r\n"
        "| 00:00:01 | 00:00:03 | 老師搭計程車，使用軟體。 |\r\n"
    )
    transcript_path.write_bytes(original.encode("utf-8"))

    result = normalize_transcript_file(transcript_path, tmp_path / "backups")

    converted = transcript_path.read_text(encoding="utf-8")
    assert "老师搭计程车，使用软体。" in converted
    assert len(converted.splitlines()) == len(original.splitlines())
    assert Path(result["backup_path"]).read_bytes() == original.encode("utf-8")
    assert result["before_sha256"] != result["after_sha256"]


def test_offline_config_rejects_remote_provider_and_network_model_loading() -> None:
    with pytest.raises(ValidationError, match="转写方式必须为 local"):
        AIConfigUpdate(transcription_provider="volcengine")
    with pytest.raises(ValidationError, match="必须只读取本地模型文件"):
        AIConfigUpdate(transcription_local_files_only=False)


def test_provider_resolver_uses_local_and_blocks_explicit_remote() -> None:
    with _override_settings(transcription_provider="local", transcription_offline_only=True):
        assert validate_transcription_provider_choice() == "local"
        with pytest.raises(TranscriptionOfflinePolicyError, match="完全离线"):
            validate_transcription_provider_choice("volcengine")
        with pytest.raises(TranscriptionOfflinePolicyError, match="完全离线"):
            validate_transcription_provider_choice("remote")


@pytest.mark.parametrize(
    "endpoint",
    (
        "/api/tasks/not-needed/process/transcript?provider=volcengine",
        "/api/tasks/not-needed/process/transcript-workflow?provider=volcengine",
    ),
)
def test_explicit_volcengine_request_returns_409_before_job_creation(monkeypatch, endpoint) -> None:
    called = False

    def unexpected_job(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("离线锁开启时不应创建远程转写 Job")

    monkeypatch.setattr("app.routers.tasks.job_service.create_or_get_active_job", unexpected_job)
    with _override_settings(transcription_provider="local", transcription_offline_only=True):
        response = TestClient(app).post(endpoint)

    assert response.status_code == 409
    assert "完全离线" in response.json()["detail"]
    assert called is False


def test_remote_transcription_guard_runs_before_http(monkeypatch, tmp_path) -> None:
    requested = False

    def unexpected_urlopen(*_args, **_kwargs):
        nonlocal requested
        requested = True
        raise AssertionError("离线锁开启时不应发送 HTTP 请求")

    monkeypatch.setattr(transcript_service, "urlopen", unexpected_urlopen)
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"audio")
    with _override_settings(transcription_offline_only=True):
        with pytest.raises(TranscriptionOfflinePolicyError, match="完全离线"):
            transcript_service.transcribe_audio_with_provider(
                audio_path,
                tmp_path,
                tmp_path / "progress.json",
                "volcengine",
            )

    assert requested is False


def test_missing_model_never_downloads_during_task(tmp_path) -> None:
    with _override_settings(
        transcription_model_cache_dir=tmp_path,
        transcription_offline_only=True,
        transcription_local_files_only=True,
    ):
        with pytest.raises(RuntimeError, match="不会联网下载"):
            resolve_local_model_source("large-v3", PRIMARY_TRANSCRIPTION_MODEL_REVISION)


def test_medium_repository_file_set_with_text_vocabulary_is_ready(tmp_path) -> None:
    with _override_settings(transcription_model_cache_dir=tmp_path):
        target = model_cache_directory("medium", CPU_FALLBACK_TRANSCRIPTION_MODEL_REVISION)
        target.mkdir(parents=True, exist_ok=True)
        for name in REQUIRED_MODEL_FILES:
            (target / name).write_bytes(b"test")
        (target / "vocabulary.txt").write_bytes(b"test")

        assert model_files_ready(target) is True
        assert resolve_local_model_source("medium", CPU_FALLBACK_TRANSCRIPTION_MODEL_REVISION) == str(target)


def test_official_model_bin_range_download_resumes_partial_file(monkeypatch, tmp_path) -> None:
    target = tmp_path / "part.bin"
    target.write_bytes(b"abc")
    requested_range = ""

    class FakeResponse:
        status = 206

        def __init__(self):
            self.stream = io.BytesIO(b"defghij")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            return self.stream.read(size)

    def fake_urlopen(request, timeout):
        nonlocal requested_range
        assert timeout == 120
        requested_range = request.get_header("Range")
        return FakeResponse()

    monkeypatch.setattr(setup_local_transcription, "urlopen", fake_urlopen)

    size = setup_local_transcription._download_model_bin_part_with_urllib(
        "https://huggingface.co/example/model.bin",
        target,
        100,
        109,
    )

    assert size == 10
    assert requested_range == "bytes=103-109"
    assert target.read_bytes() == b"abcdefghij"


def test_model_bin_part_falls_back_to_urllib_after_curl_failure(monkeypatch, tmp_path) -> None:
    target = tmp_path / "part.bin"
    calls: list[str] = []

    monkeypatch.setattr(setup_local_transcription.shutil, "which", lambda _name: "curl.exe")

    def failed_curl(*_args, **_kwargs):
        calls.append("curl")
        raise RuntimeError("curl failed")

    def successful_urllib(*_args, **_kwargs):
        calls.append("urllib")
        return 10

    monkeypatch.setattr(setup_local_transcription, "_download_model_bin_part_with_curl", failed_curl)
    monkeypatch.setattr(setup_local_transcription, "_download_model_bin_part_with_urllib", successful_urllib)

    assert setup_local_transcription._download_model_bin_part("https://example.test/model", target, 0, 9) == 10
    assert calls == ["curl", "urllib"]


def test_existing_model_bin_requires_matching_official_sha256(tmp_path) -> None:
    payload = b"fixed official model"
    (tmp_path / "model.bin").write_bytes(payload)

    result = setup_local_transcription._download_verified_model_bin(
        tmp_path,
        "Systran/test-model",
        "a" * 40,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        "测试模型",
    )

    assert result == tmp_path / "model.bin"


def test_runtime_status_reports_cached_model_and_gpu(monkeypatch, tmp_path) -> None:
    with _override_settings(
        transcription_provider="local",
        transcription_model_cache_dir=tmp_path,
        transcription_device="cuda",
    ):
        _write_model_files(model_cache_directory("large-v3", PRIMARY_TRANSCRIPTION_MODEL_REVISION))
        monkeypatch.setattr(
            local_transcription_runtime,
            "configure_windows_cuda_dll_directories",
            lambda: {
                "configured": True,
                "cublas_ready": True,
                "cudnn_ready": True,
                "directories": [],
                "errors": [],
            },
        )
        import ctranslate2

        monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 1)
        status = get_local_transcription_runtime_status()

    assert status["model_ready"] is True
    assert status["gpu_ready"] is True
    assert status["ready"] is True
    assert status["model_identity"] == "large-v3@edaa852e"
    assert status["external_cost_yuan"] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DLL 搜索路径仅在 Windows 验证")
def test_windows_dll_directories_include_venv_cublas_and_ctranslate2(monkeypatch, tmp_path) -> None:
    fake_prefix = tmp_path / ".venv"
    cublas_dir = fake_prefix / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin"
    ctranslate2_dir = tmp_path / "ctranslate2"
    cublas_dir.mkdir(parents=True)
    ctranslate2_dir.mkdir()
    (cublas_dir / "cublasLt64_12.dll").write_bytes(b"dll")
    (cublas_dir / "cublas64_12.dll").write_bytes(b"dll")
    (ctranslate2_dir / "cudnn64_9.dll").write_bytes(b"dll")
    added: list[str] = []

    monkeypatch.setattr(local_transcription_runtime.sys, "prefix", str(fake_prefix))
    monkeypatch.setattr(
        local_transcription_runtime,
        "_package_directory",
        lambda _name: ctranslate2_dir,
    )
    monkeypatch.setattr(
        local_transcription_runtime.os,
        "add_dll_directory",
        lambda path: added.append(path) or SimpleNamespace(close=lambda: None),
        raising=False,
    )
    monkeypatch.setattr(local_transcription_runtime, "_DLL_DIRECTORY_HANDLES", [])
    monkeypatch.setattr(local_transcription_runtime, "_DLL_DIRECTORY_PATHS", set())
    monkeypatch.setattr(local_transcription_runtime, "_DLL_LIBRARY_HANDLES", [])
    monkeypatch.setattr(local_transcription_runtime, "_DLL_LIBRARY_PATHS", set())
    loaded: list[str] = []
    monkeypatch.setattr(
        local_transcription_runtime.ctypes,
        "WinDLL",
        lambda path: loaded.append(path) or SimpleNamespace(),
        raising=False,
    )

    first = local_transcription_runtime.configure_windows_cuda_dll_directories()
    second = local_transcription_runtime.configure_windows_cuda_dll_directories()

    assert first["cublas_ready"] is True
    assert first["cudnn_ready"] is True
    assert second["configured"] is True
    assert added == [str(cublas_dir), str(ctranslate2_dir)]
    assert loaded == [
        str(cublas_dir / "cublasLt64_12.dll"),
        str(cublas_dir / "cublas64_12.dll"),
        str(ctranslate2_dir / "cudnn64_9.dll"),
    ]


def test_cuda_load_failure_selects_versioned_cpu_fallback(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str, str]] = []

    class FakeWhisperModel:
        def __init__(self, model_source, *, device, compute_type, **_kwargs):
            calls.append((str(model_source), device, compute_type))
            if device == "cuda":
                raise RuntimeError("cublas64_12.dll could not be loaded")

    fake_module = SimpleNamespace(WhisperModel=FakeWhisperModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(
        transcript_service,
        "configure_windows_cuda_dll_directories",
        lambda: {"configured": True},
    )
    monkeypatch.setattr(transcript_service, "_WHISPER_MODEL", None)
    monkeypatch.setattr(transcript_service, "_WHISPER_MODEL_KEY", None)
    monkeypatch.setattr(transcript_service, "_EFFECTIVE_TRANSCRIPTION_MODEL_KEY", None)

    with _override_settings(
        transcription_model_cache_dir=tmp_path,
        transcription_model="large-v3",
        transcription_model_revision=PRIMARY_TRANSCRIPTION_MODEL_REVISION,
        transcription_device="cuda",
        transcription_compute_type="float16",
        transcription_cpu_fallback_model="medium",
        transcription_local_files_only=True,
    ):
        _write_model_files(model_cache_directory("large-v3", PRIMARY_TRANSCRIPTION_MODEL_REVISION))
        _write_model_files(model_cache_directory("medium", CPU_FALLBACK_TRANSCRIPTION_MODEL_REVISION))
        transcript_service._set_configured_transcription_runtime("local")
        transcript_service._prepare_local_transcription_model_for_run()

    assert [call[1:] for call in calls] == [("cuda", "float16"), ("cpu", "int8")]
    assert transcript_service._EFFECTIVE_TRANSCRIPTION_MODEL_KEY == ("medium", "cpu", "int8")
    assert transcript_service._ACTIVE_TRANSCRIPTION_MODEL == model_identity(
        "medium",
        CPU_FALLBACK_TRANSCRIPTION_MODEL_REVISION,
    )
    assert transcript_service._transcription_model_revision_label() == CPU_FALLBACK_TRANSCRIPTION_MODEL_REVISION


def test_cuda_inference_failure_selects_cpu_before_checkpoint(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str, str]] = []

    class FakeWhisperModel:
        def __init__(self, model_source, *, device, compute_type, **_kwargs):
            self.device = device
            calls.append((str(model_source), device, compute_type))

        def transcribe(self, *_args, **_kwargs):
            if self.device == "cuda":
                raise RuntimeError("CUDA failed to initialize during inference")
            return iter(()), None

    fake_module = SimpleNamespace(WhisperModel=FakeWhisperModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(
        transcript_service,
        "configure_windows_cuda_dll_directories",
        lambda: {"configured": True},
    )
    monkeypatch.setattr(transcript_service, "_WHISPER_MODEL", None)
    monkeypatch.setattr(transcript_service, "_WHISPER_MODEL_KEY", None)
    monkeypatch.setattr(transcript_service, "_EFFECTIVE_TRANSCRIPTION_MODEL_KEY", None)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")

    with _override_settings(
        transcription_model_cache_dir=tmp_path,
        transcription_model="large-v3",
        transcription_model_revision=PRIMARY_TRANSCRIPTION_MODEL_REVISION,
        transcription_device="cuda",
        transcription_compute_type="float16",
        transcription_cpu_fallback_model="medium",
        transcription_local_files_only=True,
    ):
        _write_model_files(model_cache_directory("large-v3", PRIMARY_TRANSCRIPTION_MODEL_REVISION))
        _write_model_files(model_cache_directory("medium", CPU_FALLBACK_TRANSCRIPTION_MODEL_REVISION))
        transcript_service._set_configured_transcription_runtime("local")
        transcript_service._prepare_local_transcription_model_for_run(audio_path)

    assert [call[1:] for call in calls] == [("cuda", "float16"), ("cpu", "int8")]
    assert transcript_service._EFFECTIVE_TRANSCRIPTION_MODEL_KEY == ("medium", "cpu", "int8")
    assert transcript_service._ACTIVE_TRANSCRIPTION_MODEL == model_identity(
        "medium",
        CPU_FALLBACK_TRANSCRIPTION_MODEL_REVISION,
    )


def test_transcript_status_exposes_offline_runtime_fields(monkeypatch, tmp_path) -> None:
    transcript_path = tmp_path / "transcript.md"
    monkeypatch.setattr(
        "app.services.task_service.get_task",
        lambda _task_id: {
            "status": "pending_processing",
            "status_label": "待处理",
            "progress": 5,
            "error_message": "",
        },
    )
    monkeypatch.setattr(
        "app.services.transcript_workflow_service.get_artifact_paths",
        lambda _task_id: {"transcript_path": transcript_path},
    )
    monkeypatch.setattr(
        "app.services.transcript_workflow_service.get_local_transcription_runtime_status",
        lambda: {
            "offline_only": True,
            "model_ready": True,
            "gpu_ready": True,
            "model_revision": PRIMARY_TRANSCRIPTION_MODEL_REVISION,
        },
    )

    status = get_task_transcript_status("offline-status-test")

    assert status["offline_only"] is True
    assert status["model_ready"] is True
    assert status["gpu_ready"] is True
    assert status["model_revision"] == PRIMARY_TRANSCRIPTION_MODEL_REVISION


def test_model_revision_and_full_audio_hash_invalidate_checkpoint(tmp_path) -> None:
    from datetime import datetime, timezone

    from app.db.database import get_connection

    task_id = "offline-model-revision-checkpoint"
    source = tmp_path / "same-size.wav"
    head = b"h" * (1024 * 1024)
    tail = b"t" * (1024 * 1024)
    source.write_bytes(head + (b"a" * (1024 * 1024)) + tail)
    first_hash = fingerprint_file_full(source)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    chunks = [TranscriptChunk(1, 0, 120)]
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, source_type, platform, selection_profile,
                status, progress, is_deleted, created_at, updated_at
            ) VALUES (?, 'offline checkpoint', ?, 'upload', 'general', 'long_live_talk',
                      'pending_processing', 0, 0, ?, ?)
            """,
            (task_id, task_id, now, now),
        )
        connection.commit()
    try:
        first = TranscriptionCheckpoint(
            task_id=task_id,
            source_path=source,
            provider="local",
            model="large-v3@edaa852e",
            device="cuda",
            compute_type="float16",
            chunk_seconds=120,
            overlap_seconds=5,
        )
        first.ensure_run(chunks)
        revision_changed = TranscriptionCheckpoint(
            task_id=task_id,
            source_path=source,
            provider="local",
            model="large-v3@12345678",
            device="cuda",
            compute_type="float16",
            chunk_seconds=120,
            overlap_seconds=5,
        )
        revision_changed.ensure_run(chunks)

        normalization_changed = TranscriptionCheckpoint(
            task_id=task_id,
            source_path=source,
            provider="local",
            model="large-v3@edaa852e|text=opencc-t2s-v1",
            device="cuda",
            compute_type="float16",
            chunk_seconds=120,
            overlap_seconds=5,
        )
        normalization_changed.ensure_run(chunks)

        source.write_bytes(head + (b"b" * (1024 * 1024)) + tail)
        second_hash = fingerprint_file_full(source)
        content_changed = TranscriptionCheckpoint(
            task_id=task_id,
            source_path=source,
            provider="local",
            model="large-v3@edaa852e",
            device="cuda",
            compute_type="float16",
            chunk_seconds=120,
            overlap_seconds=5,
        )
        content_changed.ensure_run(chunks)

        assert first_hash != second_hash
        assert revision_changed.run_id != first.run_id
        assert normalization_changed.run_id != first.run_id
        assert content_changed.run_id != first.run_id
        with get_connection() as connection:
            models = {
                row["model"]
                for row in connection.execute(
                    "SELECT model FROM transcription_runs WHERE task_id = ?",
                    (task_id,),
                ).fetchall()
            }
        assert {
            "large-v3@edaa852e",
            "large-v3@12345678",
            "large-v3@edaa852e|text=opencc-t2s-v1",
        } <= models
    finally:
        with get_connection() as connection:
            connection.execute("DELETE FROM transcription_chunks WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM transcription_runs WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            connection.commit()
