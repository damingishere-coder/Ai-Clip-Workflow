from dataclasses import dataclass
from datetime import datetime
import base64
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import queue
import threading
import time
from tempfile import TemporaryDirectory
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from app.core.config import settings
from app.services import job_service
from app.services.managed_process_service import popen_process_group, terminate_process_tree
from app.services.local_transcription_runtime import (
    configure_windows_cuda_dll_directories,
    ensure_transcription_provider_allowed,
    model_identity,
    model_revision_for,
    resolve_local_model_source,
)
from app.services.transcription_checkpoint_service import (
    RemoteTranscriptionResultUncertainError,
    TranscriptionCheckpoint,
)


TIME_TABLE_PATTERN = re.compile(
    r"^\|\s*(?P<start>\d{2}:\d{2}(?::\d{2})?)\s*\|\s*"
    r"(?P<end>\d{2}:\d{2}(?::\d{2})?)\s*\|\s*(?P<text>.*?)\s*\|$"
)
INLINE_TIME_PATTERN = re.compile(
    r"(?P<start>\d{2}:\d{2}(?::\d{2})?)\s*(?:-|~|至|到)\s*"
    r"(?P<end>\d{2}:\d{2}(?::\d{2})?)\s*(?P<text>.+)"
)
PLACEHOLDER_TEXT_MARKERS = ("这里会保存", "后续可接入", "占位转写内容")


@dataclass(frozen=True)
class TranscriptWord:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None


@dataclass(frozen=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None = None
    words: tuple[TranscriptWord, ...] = ()


@dataclass(frozen=True)
class TranscriptChunk:
    index: int
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


_WHISPER_MODEL = None
_WHISPER_MODEL_KEY: tuple[str, str, str] | None = None
_EFFECTIVE_TRANSCRIPTION_MODEL_KEY: tuple[str, str, str] | None = None
_CPU_FALLBACK_DEVICE = "cpu"
_CPU_FALLBACK_COMPUTE_TYPE = "int8"
_TRANSCRIPT_PROGRESS_FILE_NAME = "transcript_progress.json"
_TRANSCRIPT_CHUNK_DIR_PREFIX = "transcript_chunks_"
_ACTIVE_TRANSCRIPTION_PROVIDER = "local"
_ACTIVE_TRANSCRIPTION_PROVIDER_LABEL = "本地 faster-whisper"
_ACTIVE_TRANSCRIPTION_MODEL = ""
_ACTIVE_TRANSCRIPTION_DEVICE = ""
_ACTIVE_TRANSCRIPTION_COMPUTE_TYPE = ""
_CUDA_ERROR_MARKERS = (
    "cublas",
    "cudnn",
    "cuda",
    "gpu",
    "nvidia",
)
_RESERVED_REMOTE_PROVIDERS = ("aliyun", "tencent", "xunfei")
_REMOTE_SAFE_RETRY_ATTEMPTS = 3


class RemoteTranscriptionError(RuntimeError):
    """远程转写失败，并明确是否允许在本进程内安全重试。"""

    def __init__(
        self,
        message: str,
        *,
        category: str,
        safe_to_retry: bool = False,
        billing_uncertain: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        suffix = "；本次是否计费不确定，未自动重试" if billing_uncertain else ""
        super().__init__(f"[{category}] {message}{suffix}")
        self.category = category
        self.safe_to_retry = safe_to_retry
        self.billing_uncertain = billing_uncertain
        self.retry_after_seconds = retry_after_seconds


class TranscriptCancelledError(RuntimeError):
    """用户取消转写；必须穿透 Provider 包装并由工作流记为 cancelled。"""


def run_ffmpeg_audio_extract(
    video_path: Path,
    output_path: Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> dict[str, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.stem}.part{output_path.suffix}")
    temporary_path.unlink(missing_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-nostats",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-progress",
        "pipe:1",
        str(temporary_path),
    ]
    duration = get_audio_duration_seconds(video_path)
    process = popen_process_group(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output_lines: queue.Queue[str] = queue.Queue()
    stderr_lines: list[str] = []

    def read_stream(stream, target) -> None:
        if not stream:
            return
        for line in iter(stream.readline, ""):
            target(line.rstrip())

    stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, output_lines.put), daemon=True)
    stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_lines.append), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    last_progress_at = time.monotonic()
    try:
        while process.poll() is None:
            if cancel_check and cancel_check():
                terminate_process_tree(process)
                raise RuntimeError("用户已取消音频提取")
            try:
                line = output_lines.get(timeout=0.5)
            except queue.Empty:
                line = ""
            if line:
                last_progress_at = time.monotonic()
                if line.startswith(("out_time_ms=", "out_time_us=")):
                    try:
                        microseconds = int(line.split("=", 1)[1])
                        percent = round((microseconds / 1_000_000) / max(duration, 0.001) * 100)
                        if progress_callback:
                            progress_callback(max(0, min(99, percent)))
                    except ValueError:
                        pass
            if time.monotonic() - last_progress_at > settings.ffmpeg_audio_extract_timeout:
                terminate_process_tree(process)
                raise RuntimeError(f"FFmpeg 连续 {settings.ffmpeg_audio_extract_timeout} 秒没有进展，已终止进程树")
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        if process.returncode != 0:
            raise RuntimeError("\n".join(stderr_lines[-20:]).strip() or "FFmpeg 音频提取失败")
        temporary_path.replace(output_path)
        if progress_callback:
            progress_callback(100)
    except Exception:
        if process.poll() is None:
            terminate_process_tree(process)
        temporary_path.unlink(missing_ok=True)
        raise
    return {
        "status": "ok",
        "message": "音频提取完成",
        "video_path": str(video_path),
        "output_path": str(output_path),
    }


def write_transcript_markdown(
    task: dict,
    audio_path: Path,
    transcript_path: Path,
    progress_callback: Callable[[dict], None] | None = None,
    provider: str | None = None,
    allow_uncertain_retry: bool = False,
) -> dict[str, str]:
    if not audio_path.exists():
        raise RuntimeError("未找到音频文件，请先提取音频")

    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path = get_transcript_progress_path(transcript_path)
    _set_configured_transcription_runtime(provider)
    _emit_transcript_progress(
        progress_path,
        progress_callback,
        status="running",
        current_chunk=0,
        total_chunks=0,
        percent=0,
        message="正在验证固定版本本地模型和计算设备",
    )
    if _ACTIVE_TRANSCRIPTION_PROVIDER == "local":
        _prepare_local_transcription_model_for_run(audio_path)
    checkpoint = None
    task_id = str(task.get("id") or "").strip()
    if task_id:
        checkpoint = TranscriptionCheckpoint(
            task_id=task_id,
            source_path=audio_path,
            provider=_ACTIVE_TRANSCRIPTION_PROVIDER,
            model=_ACTIVE_TRANSCRIPTION_MODEL,
            device=_ACTIVE_TRANSCRIPTION_DEVICE,
            compute_type=_ACTIVE_TRANSCRIPTION_COMPUTE_TYPE,
            chunk_seconds=settings.transcription_chunk_seconds,
            overlap_seconds=settings.transcription_chunk_overlap_seconds,
            allow_uncertain_retry=allow_uncertain_retry,
        )
    _emit_transcript_progress(
        progress_path,
        progress_callback,
        status="running",
        current_chunk=0,
        total_chunks=0,
        percent=0,
        message="正在读取音频时长",
    )
    segments = transcribe_audio_with_configured_provider(
        audio_path,
        transcript_path.parent,
        progress_path,
        progress_callback,
        provider=provider,
        checkpoint=checkpoint,
    )
    job_service.require_active_job_lease()
    content = build_transcript_markdown(task, audio_path, segments)
    temp_path = transcript_path.with_name(f"{transcript_path.name}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    job_service.require_active_job_lease()
    temp_path.replace(transcript_path)
    final_progress = read_transcript_progress(transcript_path)
    total_chunks = int(final_progress.get("total_chunks") or 0)
    _emit_transcript_progress(
        progress_path,
        progress_callback,
        status="completed",
        current_chunk=total_chunks,
        total_chunks=total_chunks,
        percent=100,
        message="分段转写完成，Markdown 已生成",
    )
    return {
        "status": "ok",
        "message": "真实转写 Markdown 已保存",
        "transcript_path": str(transcript_path),
        "segment_count": str(len(segments)),
        "provider": _ACTIVE_TRANSCRIPTION_PROVIDER,
    }


def transcribe_audio_with_configured_provider(
    audio_path: Path,
    working_dir: Path,
    progress_path: Path,
    progress_callback: Callable[[dict], None] | None = None,
    provider: str | None = None,
    allow_fallback: bool = False,
    checkpoint: TranscriptionCheckpoint | None = None,
) -> list[TranscriptSegment]:
    provider = _normalize_provider_name(provider or settings.transcription_provider)
    fallback_provider = _normalize_provider_name(settings.transcription_fallback_provider)
    try:
        return transcribe_audio_with_provider(
            audio_path, working_dir, progress_path, provider, progress_callback, checkpoint=checkpoint
        )
    except job_service.JobLeaseLostError:
        raise
    except TranscriptCancelledError:
        raise
    except Exception as exc:
        if allow_fallback and fallback_provider and fallback_provider != provider:
            _emit_transcript_progress(
                progress_path,
                progress_callback,
                status="running",
                current_chunk=0,
                total_chunks=0,
                percent=1,
                message=f"{_provider_label(provider)} 转写失败，正在自动切换到 {_provider_label(fallback_provider)}：{exc}",
            )
            try:
                return transcribe_audio_with_provider(
                    audio_path,
                    working_dir,
                    progress_path,
                    fallback_provider,
                    progress_callback,
                )
            except job_service.JobLeaseLostError:
                raise
            except TranscriptCancelledError:
                raise
            except Exception as fallback_exc:
                raise RuntimeError(
                    f"{_provider_label(provider)} 转写失败：{exc}；"
                    f"{_provider_label(fallback_provider)} 兜底也失败：{fallback_exc}"
                ) from fallback_exc
        raise RuntimeError(f"{_provider_label(provider)} 转写失败：{exc}") from exc


def transcribe_audio_with_provider(
    audio_path: Path,
    working_dir: Path,
    progress_path: Path,
    provider: str,
    progress_callback: Callable[[dict], None] | None = None,
    checkpoint: TranscriptionCheckpoint | None = None,
) -> list[TranscriptSegment]:
    provider = _normalize_provider_name(provider)
    if provider == "local":
        model_key = _WHISPER_MODEL_KEY or _primary_model_key()
        _set_active_transcription_runtime(
            provider="local",
            provider_label="本地 faster-whisper",
            model=model_identity(model_key[0]),
            device=model_key[1],
            compute_type=model_key[2],
        )
        return transcribe_audio_in_chunks(
            audio_path, working_dir, progress_path, progress_callback, checkpoint=checkpoint
        )
    if provider == "volcengine":
        ensure_transcription_provider_allowed(provider)
        return transcribe_audio_with_volcengine(
            audio_path, working_dir, progress_path, progress_callback, checkpoint=checkpoint
        )
    if provider in _RESERVED_REMOTE_PROVIDERS:
        raise RuntimeError(f"{_provider_label(provider)} 转写接口已预留，但当前版本还没有完整接入")
    raise RuntimeError(f"未知转写服务商：{provider or '空'}")


def transcribe_audio_in_chunks(
    audio_path: Path,
    working_dir: Path,
    progress_path: Path,
    progress_callback: Callable[[dict], None] | None = None,
    checkpoint: TranscriptionCheckpoint | None = None,
) -> list[TranscriptSegment]:
    duration_seconds = get_audio_duration_seconds(audio_path)
    chunks = build_transcript_chunks(
        duration_seconds,
        chunk_seconds=settings.transcription_chunk_seconds,
        overlap_seconds=settings.transcription_chunk_overlap_seconds,
    )
    if not chunks:
        raise RuntimeError("本地语音转写失败：音频时长无效，无法分段")
    if checkpoint:
        checkpoint.ensure_run(chunks)
    _emit_transcript_progress(
        progress_path,
        progress_callback,
        status="running",
        current_chunk=0,
        total_chunks=len(chunks),
        percent=1,
        message=f"已读取音频时长，准备分成 {len(chunks)} 段转写",
    )

    all_segments: list[TranscriptSegment] = []
    with TemporaryDirectory(prefix=_TRANSCRIPT_CHUNK_DIR_PREFIX, dir=working_dir) as temp_dir:
        temp_dir_path = Path(temp_dir)
        for chunk in chunks:
            completed_segments = checkpoint.load_completed(chunk.index, _segment_from_checkpoint) if checkpoint else None
            if completed_segments is not None:
                all_segments.extend(
                    _offset_chunk_segments(completed_segments, chunk, settings.transcription_chunk_overlap_seconds)
                )
                _emit_transcript_progress(
                    progress_path, progress_callback, status="running", current_chunk=chunk.index,
                    total_chunks=len(chunks), percent=_chunk_percent(chunk.index, len(chunks)),
                    message=f"已复用第 {chunk.index}/{len(chunks)} 段 checkpoint",
                )
                continue
            _emit_transcript_progress(
                progress_path,
                progress_callback,
                status="running",
                current_chunk=chunk.index,
                total_chunks=len(chunks),
                percent=_chunk_start_percent(chunk.index, len(chunks)),
                message=f"正在准备第 {chunk.index}/{len(chunks)} 段音频",
            )
            chunk_path = temp_dir_path / f"chunk_{chunk.index:04d}.wav"
            _extract_audio_chunk(audio_path, chunk_path, chunk)
            _emit_transcript_progress(
                progress_path,
                progress_callback,
                status="running",
                current_chunk=chunk.index,
                total_chunks=len(chunks),
                percent=_chunk_start_percent(chunk.index, len(chunks)),
                message=f"正在加载模型并转写第 {chunk.index}/{len(chunks)} 段",
            )
            try:
                chunk_segments = transcribe_audio(chunk_path, allow_empty=True)
                if checkpoint:
                    checkpoint.save_completed(chunk.index, chunk_segments)
            except Exception as exc:
                if checkpoint:
                    checkpoint.save_failed(chunk.index, str(exc))
                raise
            all_segments.extend(_offset_chunk_segments(chunk_segments, chunk, settings.transcription_chunk_overlap_seconds))
            _emit_transcript_progress(
                progress_path,
                progress_callback,
                status="running",
                current_chunk=chunk.index,
                total_chunks=len(chunks),
                percent=_chunk_percent(chunk.index, len(chunks)),
                message=f"已完成第 {chunk.index}/{len(chunks)} 段",
            )

    if not all_segments:
        raise RuntimeError("本地语音转写完成，但没有识别到可用语音内容")
    if checkpoint:
        checkpoint.complete()
    return sorted(all_segments, key=lambda segment: (segment.start_seconds, segment.end_seconds))


def transcribe_audio_with_volcengine(
    audio_path: Path,
    working_dir: Path,
    progress_path: Path,
    progress_callback: Callable[[dict], None] | None = None,
    checkpoint: TranscriptionCheckpoint | None = None,
) -> list[TranscriptSegment]:
    _ensure_volcengine_configured()
    _set_active_transcription_runtime(
        provider="volcengine",
        provider_label="火山引擎远程转写",
        model=settings.volcengine_asr_resource_id,
        device="remote",
        compute_type=settings.volcengine_asr_audio_format,
    )

    duration_seconds = get_audio_duration_seconds(audio_path)
    chunks = build_transcript_chunks(
        duration_seconds,
        chunk_seconds=settings.transcription_chunk_seconds,
        overlap_seconds=settings.transcription_chunk_overlap_seconds,
    )
    if not chunks:
        raise RuntimeError("火山引擎远程转写失败：音频时长无效，无法分段")
    if checkpoint:
        checkpoint.ensure_run(chunks)
    _emit_transcript_progress(
        progress_path,
        progress_callback,
        status="running",
        current_chunk=0,
        total_chunks=len(chunks),
        percent=1,
        message=f"已读取音频时长，准备通过火山引擎分成 {len(chunks)} 段转写",
    )

    all_segments: list[TranscriptSegment] = []
    with TemporaryDirectory(prefix=_TRANSCRIPT_CHUNK_DIR_PREFIX, dir=working_dir) as temp_dir:
        temp_dir_path = Path(temp_dir)
        for chunk in chunks:
            completed_segments = checkpoint.load_completed(chunk.index, _segment_from_checkpoint) if checkpoint else None
            if completed_segments is not None:
                all_segments.extend(
                    _offset_chunk_segments(completed_segments, chunk, settings.transcription_chunk_overlap_seconds)
                )
                _emit_transcript_progress(
                    progress_path, progress_callback, status="running", current_chunk=chunk.index,
                    total_chunks=len(chunks), percent=_chunk_percent(chunk.index, len(chunks)),
                    message=f"已复用第 {chunk.index}/{len(chunks)} 段 checkpoint",
                )
                continue
            _emit_transcript_progress(
                progress_path,
                progress_callback,
                status="running",
                current_chunk=chunk.index,
                total_chunks=len(chunks),
                percent=_chunk_start_percent(chunk.index, len(chunks)),
                message=f"正在压缩第 {chunk.index}/{len(chunks)} 段音频，准备上传火山引擎",
            )
            chunk_path = temp_dir_path / f"chunk_{chunk.index:04d}.{_remote_audio_extension()}"
            _extract_remote_audio_chunk(audio_path, chunk_path, chunk)
            _emit_transcript_progress(
                progress_path,
                progress_callback,
                status="running",
                current_chunk=chunk.index,
                total_chunks=len(chunks),
                percent=_chunk_start_percent(chunk.index, len(chunks)),
                message=f"正在请求火山引擎转写第 {chunk.index}/{len(chunks)} 段",
            )
            request_id = checkpoint.prepare_remote_request(chunk.index) if checkpoint else None
            try:
                chunk_segments = transcribe_audio_with_volcengine_flash(
                    chunk_path,
                    allow_empty=True,
                    request_id=request_id,
                )
            except Exception as exc:
                if checkpoint:
                    if isinstance(exc, RemoteTranscriptionResultUncertainError) or (
                        isinstance(exc, RemoteTranscriptionError) and exc.billing_uncertain
                    ):
                        checkpoint.save_uncertain(chunk.index, str(exc))
                    else:
                        checkpoint.save_failed(chunk.index, str(exc), attempt_already_counted=True)
                raise
            if checkpoint:
                _save_remote_checkpoint_completed(checkpoint, chunk.index, chunk_segments)
            all_segments.extend(_offset_chunk_segments(chunk_segments, chunk, settings.transcription_chunk_overlap_seconds))
            _emit_transcript_progress(
                progress_path,
                progress_callback,
                status="running",
                current_chunk=chunk.index,
                total_chunks=len(chunks),
                percent=_chunk_percent(chunk.index, len(chunks)),
                message=f"火山引擎已完成第 {chunk.index}/{len(chunks)} 段",
            )

    if not all_segments:
        raise RuntimeError("火山引擎远程转写完成，但没有识别到可用语音内容")
    if checkpoint:
        checkpoint.complete()
    return sorted(all_segments, key=lambda segment: (segment.start_seconds, segment.end_seconds))


def _save_remote_checkpoint_completed(
    checkpoint: TranscriptionCheckpoint,
    chunk_index: int,
    segments: list[TranscriptSegment],
) -> None:
    """Provider 已返回后若落账失败，保留 requesting/uncertain，绝不降为可自动重试。"""
    try:
        checkpoint.save_completed(chunk_index, segments, attempt_already_counted=True)
    except job_service.JobLeaseLostError:
        # 旧 worker 不得写回；requesting 会让新 owner 停止自动重发。
        raise
    except Exception as exc:
        uncertain = RemoteTranscriptionResultUncertainError(
            f"第 {chunk_index} 段远程转写已返回，但 checkpoint 保存失败；"
            "结果与计费状态不确定，普通重试不会再次请求。"
        )
        try:
            checkpoint.save_uncertain(chunk_index, str(uncertain))
        except job_service.JobLeaseLostError:
            raise
        except Exception:
            # 数据库仍不可写时保留请求前的 requesting，恢复端同样会 fail closed。
            pass
        raise uncertain from exc


def transcribe_audio_with_volcengine_flash(
    audio_path: Path,
    allow_empty: bool = False,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    request_id: str | None = None,
) -> list[TranscriptSegment]:
    _ensure_volcengine_configured()
    headers = _volcengine_headers(request_id=request_id)
    payload = _build_volcengine_flash_payload(audio_path)
    for attempt in range(1, _REMOTE_SAFE_RETRY_ATTEMPTS + 1):
        try:
            return _request_volcengine_transcript(payload, headers, allow_empty=allow_empty)
        except RemoteTranscriptionError as exc:
            if not exc.safe_to_retry or attempt >= _REMOTE_SAFE_RETRY_ATTEMPTS:
                raise
            delay = exc.retry_after_seconds if exc.retry_after_seconds is not None else 2 ** (attempt - 1)
            sleep_fn(max(0.0, min(delay, 60.0)))
    raise AssertionError("远程转写安全重试循环异常结束")


def _request_volcengine_transcript(
    payload: dict,
    headers: dict[str, str],
    *,
    allow_empty: bool,
) -> list[TranscriptSegment]:
    request = Request(
        settings.volcengine_asr_api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.volcengine_asr_timeout_seconds) as response:
            response_text = response.read().decode("utf-8", errors="replace")
            status_code = response.headers.get("X-Api-Status-Code", "")
            status_message = response.headers.get("X-Api-Message", "")
            log_id = response.headers.get("X-Tt-Logid", "")
    except HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        retry_after = (
            _parse_retry_after_seconds((exc.headers or {}).get("Retry-After"))
            if exc.code == 429
            else None
        )
        raise RemoteTranscriptionError(
            f"火山引擎接口返回 HTTP {exc.code}：{error_text[:500]}",
            category="rate_limited" if exc.code == 429 else "http_error",
            safe_to_retry=exc.code == 429,
            billing_uncertain=exc.code >= 500 or exc.code == 408,
            retry_after_seconds=retry_after,
        ) from exc
    except URLError as exc:
        reason = exc.reason
        is_timeout = isinstance(reason, TimeoutError)
        is_preconnect_failure = isinstance(reason, (ConnectionRefusedError, socket.gaierror))
        raise RemoteTranscriptionError(
            "火山引擎转写接口连接超时" if is_timeout else f"无法连接火山引擎转写接口：{reason}",
            category="timeout" if is_timeout else "network_error",
            safe_to_retry=is_preconnect_failure,
            billing_uncertain=not is_preconnect_failure,
        ) from exc
    except TimeoutError as exc:
        raise RemoteTranscriptionError(
            "火山引擎转写接口连接或读取超时",
            category="timeout",
            billing_uncertain=True,
        ) from exc
    except OSError as exc:
        raise RemoteTranscriptionError(
            f"火山引擎转写响应读取失败：{exc}",
            category="network_error",
            billing_uncertain=True,
        ) from exc

    if status_code and status_code != "20000000":
        if allow_empty and status_code == "20000003":
            return []
        raise RemoteTranscriptionError(
            "火山引擎接口返回业务错误："
            f"code={status_code}, message={status_message or 'unknown'}, logid={log_id or 'unknown'}",
            category="business_error",
            billing_uncertain=True,
        )

    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RemoteTranscriptionError(
            f"火山引擎接口返回的不是 JSON：{response_text[:500]}",
            category="invalid_response_json",
            billing_uncertain=True,
        ) from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
        raise RemoteTranscriptionError(
            "火山引擎成功响应缺少 result 对象",
            category="invalid_response_schema",
            billing_uncertain=True,
        )
    try:
        segments = parse_volcengine_transcript_segments(payload)
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        raise RemoteTranscriptionError(
            f"火山引擎成功响应的转写字段无效：{exc}",
            category="invalid_response_schema",
            billing_uncertain=True,
        ) from exc
    if not segments and not allow_empty:
        raise RemoteTranscriptionError(
            f"火山引擎远程转写没有返回可用文本：{json.dumps(payload, ensure_ascii=False)[:500]}",
            category="empty_model_output",
            billing_uncertain=True,
        )
    return segments


def _parse_retry_after_seconds(value: str | None) -> float | None:
    try:
        return max(0.0, min(float(str(value or "").strip()), 60.0))
    except ValueError:
        return None


def parse_volcengine_transcript_segments(payload: dict) -> list[TranscriptSegment]:
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        result = payload

    utterances = result.get("utterances") if isinstance(result, dict) else None
    if isinstance(utterances, list) and utterances:
        segments = [_segment_from_volcengine_utterance(item) for item in utterances if isinstance(item, dict)]
        return [segment for segment in segments if segment and segment.text]

    text = normalize_transcript_text(str(result.get("text") or "")) if isinstance(result, dict) else ""
    if text:
        return [TranscriptSegment(start_seconds=0, end_seconds=1, text=text)]
    return []


def get_audio_duration_seconds(audio_path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.ffprobe_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFprobe 读取音频时长超过 {settings.ffprobe_timeout} 秒") from exc
    except OSError as exc:
        raise RuntimeError(f"FFprobe 无法读取音频时长：{exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "FFprobe 无法读取音频时长")
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("FFprobe 返回的音频时长无效") from exc
    if duration <= 0:
        raise RuntimeError("音频时长为 0，无法进行语音转写")
    return duration


def build_transcript_chunks(
    duration_seconds: float,
    chunk_seconds: int,
    overlap_seconds: int,
) -> list[TranscriptChunk]:
    if duration_seconds <= 0:
        return []
    chunk_seconds = max(1, int(chunk_seconds))
    overlap_seconds = max(0, min(int(overlap_seconds), chunk_seconds - 1))
    step_seconds = chunk_seconds - overlap_seconds
    chunks: list[TranscriptChunk] = []
    start_seconds = 0.0
    while start_seconds < duration_seconds:
        end_seconds = min(duration_seconds, start_seconds + chunk_seconds)
        chunks.append(
            TranscriptChunk(
                index=len(chunks) + 1,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
            )
        )
        if end_seconds >= duration_seconds:
            break
        start_seconds += step_seconds
    return chunks


def _extract_audio_chunk(audio_path: Path, chunk_path: Path, chunk: TranscriptChunk) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{chunk.start_seconds:.3f}",
        "-i",
        str(audio_path),
        "-t",
        f"{chunk.duration_seconds:.3f}",
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(chunk_path),
    ]
    chunk_path.unlink(missing_ok=True)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.ffmpeg_chunk_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        chunk_path.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg 音频分段超过 {settings.ffmpeg_chunk_timeout} 秒：第 {chunk.index} 段") from exc
    except OSError as exc:
        chunk_path.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg 无法执行音频分段：第 {chunk.index} 段：{exc}") from exc
    if result.returncode != 0:
        chunk_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr.strip() or f"FFmpeg 音频分段失败：第 {chunk.index} 段")


def _extract_remote_audio_chunk(audio_path: Path, chunk_path: Path, chunk: TranscriptChunk) -> None:
    audio_format = settings.volcengine_asr_audio_format.lower().strip()
    if audio_format in ("ogg", "opus", "ogg_opus"):
        codec_args = ["-acodec", "libopus", "-b:a", "32k"]
    else:
        codec_args = ["-acodec", "libmp3lame", "-b:a", "64k"]
    command = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{chunk.start_seconds:.3f}",
        "-i",
        str(audio_path),
        "-t",
        f"{chunk.duration_seconds:.3f}",
        "-vn",
        "-ar",
        "16000",
        "-ac",
        "1",
        *codec_args,
        str(chunk_path),
    ]
    chunk_path.unlink(missing_ok=True)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.ffmpeg_chunk_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        chunk_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"FFmpeg 远程转写音频压缩超过 {settings.ffmpeg_chunk_timeout} 秒：第 {chunk.index} 段"
        ) from exc
    except OSError as exc:
        chunk_path.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg 无法执行远程转写音频压缩：第 {chunk.index} 段：{exc}") from exc
    if result.returncode != 0:
        chunk_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr.strip() or f"FFmpeg 远程转写音频压缩失败：第 {chunk.index} 段")


def _remote_audio_extension() -> str:
    audio_format = settings.volcengine_asr_audio_format.lower().strip()
    if audio_format in ("ogg", "opus", "ogg_opus"):
        return "ogg"
    return "mp3"


def _build_volcengine_flash_payload(audio_path: Path) -> dict:
    uid = settings.volcengine_asr_api_key or settings.volcengine_asr_app_key or "local-user"
    return {
        "user": {"uid": uid},
        "audio": {
            "data": base64.b64encode(audio_path.read_bytes()).decode("utf-8"),
        },
        "request": {
            "model_name": "bigmodel",
        },
    }


def _ensure_volcengine_configured() -> None:
    if not settings.volcengine_asr_api_key and not settings.volcengine_asr_app_key:
        raise RuntimeError("缺少火山引擎转写密钥，请在系统状态页的“1. 音频转写”填写 API Key")
    if not settings.volcengine_asr_resource_id:
        raise RuntimeError("缺少火山引擎资源 ID，请在系统状态页的“1. 音频转写”填写资源 ID")


def _volcengine_headers(*, request_id: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Api-Resource-Id": settings.volcengine_asr_resource_id,
        "X-Api-Request-Id": request_id or uuid4().hex,
        "X-Api-Sequence": "-1",
    }
    if settings.volcengine_asr_api_key:
        headers["X-Api-Key"] = settings.volcengine_asr_api_key
    if settings.volcengine_asr_app_key:
        headers["X-Api-App-Key"] = settings.volcengine_asr_app_key
    if settings.volcengine_asr_access_key:
        headers["X-Api-Access-Key"] = settings.volcengine_asr_access_key
    return headers


def _segment_from_volcengine_utterance(item: dict) -> TranscriptSegment | None:
    text = normalize_transcript_text(str(item.get("text") or item.get("utterance") or ""))
    if not text:
        return None
    start_seconds = _volcengine_utterance_time_to_seconds(item, "start_time", "start")
    end_seconds = _volcengine_utterance_time_to_seconds(item, "end_time", "end")
    if end_seconds <= start_seconds:
        end_seconds = start_seconds + 1
    raw_words = item.get("words") or item.get("word_info") or []
    words: list[TranscriptWord] = []
    if isinstance(raw_words, list):
        for word in raw_words:
            if not isinstance(word, dict):
                continue
            word_text = normalize_transcript_text(str(word.get("text") or word.get("word") or ""))
            if not word_text:
                continue
            word_start = _volcengine_utterance_time_to_seconds(word, "start_time", "start")
            word_end = _volcengine_utterance_time_to_seconds(word, "end_time", "end")
            confidence = word.get("confidence") or word.get("probability")
            words.append(
                TranscriptWord(
                    start_ms=round(word_start * 1000),
                    end_ms=round(max(word_start, word_end) * 1000),
                    text=word_text,
                    confidence=float(confidence) if confidence is not None else None,
                )
            )
    segment_confidence = item.get("confidence")
    return TranscriptSegment(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        text=text,
        confidence=float(segment_confidence) if segment_confidence is not None else None,
        words=tuple(words),
    )


def _volcengine_utterance_time_to_seconds(item: dict, millisecond_key: str, fallback_key: str) -> float:
    if millisecond_key in item:
        return _volcengine_time_to_seconds(item.get(millisecond_key), milliseconds=True)
    return _volcengine_time_to_seconds(item.get(fallback_key, 0), milliseconds=False)


def _volcengine_time_to_seconds(value, *, milliseconds: bool) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str):
        stripped = value.strip()
        if ":" in stripped:
            parts = [float(part) for part in stripped.split(":")]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
        try:
            value = float(stripped)
        except ValueError:
            return 0.0
    numeric_value = float(value)
    if milliseconds:
        return numeric_value / 1000
    return numeric_value


def _offset_chunk_segments(
    segments: list[TranscriptSegment],
    chunk: TranscriptChunk,
    overlap_seconds: int,
) -> list[TranscriptSegment]:
    unique_start = chunk.start_seconds + (overlap_seconds if chunk.index > 1 else 0)
    adjusted_segments: list[TranscriptSegment] = []
    for segment in segments:
        adjusted_start = segment.start_seconds + chunk.start_seconds
        adjusted_end = segment.end_seconds + chunk.start_seconds
        if chunk.index > 1 and adjusted_start < unique_start:
            continue
        adjusted_segments.append(
            TranscriptSegment(
                start_seconds=adjusted_start,
                end_seconds=adjusted_end,
                text=segment.text,
                confidence=segment.confidence,
                words=tuple(
                    TranscriptWord(
                        start_ms=word.start_ms + round(chunk.start_seconds * 1000),
                        end_ms=word.end_ms + round(chunk.start_seconds * 1000),
                        text=word.text,
                        confidence=word.confidence,
                    )
                    for word in segment.words
                ),
            )
        )
    return adjusted_segments


def _segment_from_checkpoint(item: dict) -> TranscriptSegment:
    words = tuple(
        TranscriptWord(
            start_ms=int(word.get("start_ms") or 0),
            end_ms=int(word.get("end_ms") or 0),
            text=str(word.get("text") or ""),
            confidence=float(word["confidence"]) if word.get("confidence") is not None else None,
        )
        for word in (item.get("words") or [])
        if isinstance(word, dict)
    )
    return TranscriptSegment(
        start_seconds=float(item.get("start_seconds") or 0),
        end_seconds=float(item.get("end_seconds") or 0),
        text=str(item.get("text") or ""),
        confidence=float(item["confidence"]) if item.get("confidence") is not None else None,
        words=words,
    )


def get_transcript_progress_path(transcript_path: Path) -> Path:
    return transcript_path.with_name(_TRANSCRIPT_PROGRESS_FILE_NAME)


def read_transcript_progress(transcript_path: Path) -> dict:
    progress_path = get_transcript_progress_path(transcript_path)
    if not progress_path.exists():
        return {}
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return progress if isinstance(progress, dict) else {}


def write_transcript_progress(
    transcript_path: Path,
    *,
    status: str,
    current_chunk: int,
    total_chunks: int,
    percent: int,
    message: str,
) -> dict:
    progress_path = get_transcript_progress_path(transcript_path)
    return _write_transcript_progress(
        progress_path,
        status=status,
        current_chunk=current_chunk,
        total_chunks=total_chunks,
        percent=percent,
        message=message,
    )


def _emit_transcript_progress(
    progress_path: Path,
    progress_callback: Callable[[dict], None] | None,
    *,
    status: str,
    current_chunk: int,
    total_chunks: int,
    percent: int,
    message: str,
) -> dict:
    progress = _write_transcript_progress(
        progress_path,
        status=status,
        current_chunk=current_chunk,
        total_chunks=total_chunks,
        percent=percent,
        message=message,
    )
    if progress_callback:
        progress_callback(progress)
    return progress


def _write_transcript_progress(
    progress_path: Path,
    *,
    status: str,
    current_chunk: int,
    total_chunks: int,
    percent: int,
    message: str,
) -> dict:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress = {
        "status": status,
        "current_chunk": max(0, int(current_chunk)),
        "total_chunks": max(0, int(total_chunks)),
        "percent": max(0, min(100, int(percent))),
        "message": message,
        "provider": _ACTIVE_TRANSCRIPTION_PROVIDER,
        "provider_label": _ACTIVE_TRANSCRIPTION_PROVIDER_LABEL,
        "model": _transcription_model_label(),
        "model_revision": _transcription_model_revision_label(),
        "device": _transcription_device_label(),
        "compute_type": _transcription_compute_type_label(),
        "chunk_seconds": settings.transcription_chunk_seconds,
        "chunk_overlap_seconds": settings.transcription_chunk_overlap_seconds,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    temp_path = progress_path.with_name(f"{progress_path.name}.tmp")
    temp_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(progress_path)
    return progress


def _chunk_percent(done_chunks: int, total_chunks: int) -> int:
    if total_chunks <= 0:
        return 0
    return int(min(99, max(0, round((done_chunks / total_chunks) * 100))))


def _chunk_start_percent(current_chunk: int, total_chunks: int) -> int:
    if total_chunks <= 0:
        return 1
    return int(min(99, max(1, round(((current_chunk - 0.5) / total_chunks) * 100))))


def transcribe_audio(audio_path: Path, allow_empty: bool = False) -> list[TranscriptSegment]:
    try:
        segments = _transcribe_audio_with_model(
            _get_whisper_model(_EFFECTIVE_TRANSCRIPTION_MODEL_KEY or _primary_model_key()),
            audio_path,
        )
    except Exception as exc:
        if _should_retry_with_cpu(exc):
            try:
                segments = _transcribe_audio_with_model(
                    _get_whisper_model(_cpu_fallback_model_key()),
                    audio_path,
                )
            except Exception as cpu_exc:
                raise RuntimeError(
                    "本地语音转写失败：GPU/CUDA 加载失败后已自动切换 CPU，"
                    f"但 CPU 转写也失败了。CPU 错误：{cpu_exc}；原始 GPU 错误：{exc}"
                ) from cpu_exc
        else:
            raise RuntimeError(f"本地语音转写失败：{exc}") from exc

    if not segments and not allow_empty:
        raise RuntimeError("本地语音转写完成，但没有识别到可用语音内容")
    return segments


def _prepare_local_transcription_model_for_run(audio_path: Path | None = None) -> None:
    """在创建 checkpoint 前确定本次实际使用 GPU 主模型还是 CPU 兜底。"""
    global _EFFECTIVE_TRANSCRIPTION_MODEL_KEY

    _EFFECTIVE_TRANSCRIPTION_MODEL_KEY = None
    model = _get_whisper_model(_primary_model_key())
    if audio_path is not None:
        try:
            _probe_whisper_model_inference(model, audio_path)
        except Exception as exc:
            if _WHISPER_MODEL_KEY != _cpu_fallback_model_key() and _should_retry_with_cpu(exc):
                model = _get_whisper_model(_cpu_fallback_model_key())
                _probe_whisper_model_inference(model, audio_path)
            else:
                raise
    _EFFECTIVE_TRANSCRIPTION_MODEL_KEY = _WHISPER_MODEL_KEY or _primary_model_key()
    model, device, compute_type = _EFFECTIVE_TRANSCRIPTION_MODEL_KEY
    _set_active_transcription_runtime(
        provider="local",
        provider_label="本地 faster-whisper",
        model=model_identity(model),
        device=device,
        compute_type=compute_type,
    )


def _probe_whisper_model_inference(model, audio_path: Path) -> None:
    """只解码开头 1 秒，确保 checkpoint 记录的是可实际推理的设备。"""
    segments, _info = model.transcribe(
        str(audio_path),
        language=settings.transcription_language,
        vad_filter=False,
        beam_size=1,
        word_timestamps=False,
        clip_timestamps="0,1",
    )
    list(segments)


def _transcribe_audio_with_model(model, audio_path: Path) -> list[TranscriptSegment]:
    raw_segments, _info = model.transcribe(
        str(audio_path),
        language=settings.transcription_language or None,
        vad_filter=True,
        beam_size=5,
        word_timestamps=True,
    )
    results: list[TranscriptSegment] = []
    for segment in raw_segments:
        text = normalize_transcript_text(segment.text)
        if not text:
            continue
        words = tuple(
            TranscriptWord(
                start_ms=round(float(word.start) * 1000),
                end_ms=round(float(word.end) * 1000),
                text=normalize_transcript_text(word.word),
                confidence=float(word.probability) if getattr(word, "probability", None) is not None else None,
            )
            for word in (getattr(segment, "words", None) or [])
            if normalize_transcript_text(getattr(word, "word", ""))
        )
        avg_logprob = getattr(segment, "avg_logprob", None)
        confidence = max(0.0, min(1.0, 2.718281828 ** float(avg_logprob))) if avg_logprob is not None else None
        results.append(
            TranscriptSegment(
                start_seconds=float(segment.start),
                end_seconds=float(segment.end),
                text=text,
                confidence=confidence,
                words=words,
            )
        )
    return results


def _get_whisper_model(model_key: tuple[str, str, str] | None = None):
    global _WHISPER_MODEL, _WHISPER_MODEL_KEY

    model_key = model_key or _primary_model_key()
    if _WHISPER_MODEL is not None and _WHISPER_MODEL_KEY == model_key:
        return _WHISPER_MODEL

    try:
        configure_windows_cuda_dll_directories()
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "未安装本地转写依赖 faster-whisper。请先安装 requirements.txt 里的依赖，再重新生成转写。"
        ) from exc

    try:
        revision = model_revision_for(model_key[0])
        model_source = resolve_local_model_source(model_key[0], revision)
        _WHISPER_MODEL = WhisperModel(
            model_source,
            device=model_key[1],
            compute_type=model_key[2],
            download_root=str(settings.transcription_model_cache_dir),
            local_files_only=settings.transcription_local_files_only,
        )
        _WHISPER_MODEL_KEY = model_key
        if _ACTIVE_TRANSCRIPTION_PROVIDER == "local":
            _set_active_transcription_runtime(
                provider="local",
                provider_label="本地 faster-whisper",
                model=model_identity(model_key[0], revision),
                device=model_key[1],
                compute_type=model_key[2],
            )
        return _WHISPER_MODEL
    except Exception as exc:
        if model_key != _cpu_fallback_model_key() and _should_retry_with_cpu(exc):
            return _get_whisper_model(_cpu_fallback_model_key())
        raise RuntimeError(
            "本地转写模型加载失败。请确认已安装 faster-whisper，并确认 NVIDIA 显卡 / CUDA 环境可用；"
            "如果要改用 CPU，请在 .env 中设置 TRANSCRIPTION_DEVICE=cpu 和 TRANSCRIPTION_COMPUTE_TYPE=int8。"
            f" 原始错误：{exc}"
        ) from exc


def _primary_model_key() -> tuple[str, str, str]:
    configured_device = settings.transcription_device.strip().lower()
    device = _detect_transcription_device() if configured_device == "auto" else configured_device
    configured_compute = settings.transcription_compute_type.strip().lower()
    compute_type = ("float16" if device == "cuda" else "int8") if configured_compute == "auto" else configured_compute
    return (
        settings.transcription_model,
        device,
        compute_type,
    )


def _detect_transcription_device() -> str:
    """仅在显式配置 TRANSCRIPTION_DEVICE=auto 时检测 CUDA；cpu 配置永不覆盖。"""
    executable = shutil.which("nvidia-smi")
    if not executable:
        return "cpu"
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "cpu"
    return "cuda" if result.returncode == 0 and result.stdout.strip() else "cpu"


def _cpu_fallback_model_key() -> tuple[str, str, str]:
    return (
        settings.transcription_cpu_fallback_model,
        _CPU_FALLBACK_DEVICE,
        _CPU_FALLBACK_COMPUTE_TYPE,
    )


def _should_retry_with_cpu(exc: Exception) -> bool:
    if settings.transcription_device.lower() == _CPU_FALLBACK_DEVICE:
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _CUDA_ERROR_MARKERS)


def _normalize_provider_name(provider: str) -> str:
    return (provider or "").strip().lower()


def _provider_label(provider: str) -> str:
    labels = {
        "local": "本地 faster-whisper",
        "volcengine": "火山引擎远程转写",
        "aliyun": "阿里云录音文件识别",
        "tencent": "腾讯云录音文件识别",
        "xunfei": "讯飞语音转写",
    }
    return labels.get(_normalize_provider_name(provider), provider or "未知转写服务")


def _set_active_transcription_runtime(
    *,
    provider: str,
    provider_label: str,
    model: str,
    device: str,
    compute_type: str,
) -> None:
    global _ACTIVE_TRANSCRIPTION_PROVIDER
    global _ACTIVE_TRANSCRIPTION_PROVIDER_LABEL
    global _ACTIVE_TRANSCRIPTION_MODEL
    global _ACTIVE_TRANSCRIPTION_DEVICE
    global _ACTIVE_TRANSCRIPTION_COMPUTE_TYPE

    _ACTIVE_TRANSCRIPTION_PROVIDER = provider
    _ACTIVE_TRANSCRIPTION_PROVIDER_LABEL = provider_label
    _ACTIVE_TRANSCRIPTION_MODEL = model
    _ACTIVE_TRANSCRIPTION_DEVICE = device
    _ACTIVE_TRANSCRIPTION_COMPUTE_TYPE = compute_type


def _set_configured_transcription_runtime(provider: str | None = None) -> None:
    provider = _normalize_provider_name(provider or settings.transcription_provider)
    if provider == "volcengine":
        _set_active_transcription_runtime(
            provider="volcengine",
            provider_label="火山引擎远程转写",
            model=settings.volcengine_asr_resource_id,
            device="remote",
            compute_type=settings.volcengine_asr_audio_format,
        )
        return
    if provider == "local":
        model, device, compute_type = _WHISPER_MODEL_KEY or _primary_model_key()
        _set_active_transcription_runtime(
            provider="local",
            provider_label="本地 faster-whisper",
            model=model_identity(model),
            device=device,
            compute_type=compute_type,
        )
        return
    _set_active_transcription_runtime(
        provider=provider,
        provider_label=_provider_label(provider),
        model="未接入",
        device="remote",
        compute_type="pending",
    )


def _transcription_runtime_label() -> str:
    if _ACTIVE_TRANSCRIPTION_DEVICE and _ACTIVE_TRANSCRIPTION_COMPUTE_TYPE:
        return f"{_ACTIVE_TRANSCRIPTION_DEVICE} / {_ACTIVE_TRANSCRIPTION_COMPUTE_TYPE}"
    _model, device, compute_type = _WHISPER_MODEL_KEY or _primary_model_key()
    return f"{device} / {compute_type}"


def _transcription_model_label() -> str:
    if _ACTIVE_TRANSCRIPTION_MODEL:
        return _ACTIVE_TRANSCRIPTION_MODEL
    model, _device, _compute_type = _WHISPER_MODEL_KEY or _primary_model_key()
    return model_identity(model)


def _transcription_model_revision_label() -> str:
    if _ACTIVE_TRANSCRIPTION_PROVIDER != "local":
        return ""
    model, _device, _compute_type = _WHISPER_MODEL_KEY or _primary_model_key()
    return model_revision_for(model)


def _transcription_device_label() -> str:
    if _ACTIVE_TRANSCRIPTION_DEVICE:
        return _ACTIVE_TRANSCRIPTION_DEVICE
    _model, device, _compute_type = _WHISPER_MODEL_KEY or _primary_model_key()
    return device


def _transcription_compute_type_label() -> str:
    if _ACTIVE_TRANSCRIPTION_COMPUTE_TYPE:
        return _ACTIVE_TRANSCRIPTION_COMPUTE_TYPE
    _model, _device, compute_type = _WHISPER_MODEL_KEY or _primary_model_key()
    return compute_type


def cleanup_transcript_chunk_dirs(transcript_path: Path) -> int:
    transcript_dir = transcript_path.parent
    if not transcript_dir.exists():
        return 0
    removed = 0
    for path in transcript_dir.iterdir():
        if path.is_dir() and path.name.startswith(_TRANSCRIPT_CHUNK_DIR_PREFIX):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def build_transcript_markdown(
    task: dict,
    audio_path: Path,
    segments: list[TranscriptSegment],
) -> str:
    source_path = task.get("source")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    segment_table = "\n".join(
        "| "
        f"{format_seconds(segment.start_seconds)} | "
        f"{format_seconds(segment.end_seconds)} | "
        f"{escape_markdown_table_text(segment.text)} |"
        for segment in segments
    )

    return f"""# {task.get("task_name") or "未命名任务"} 转写文本

## 任务信息

- 任务 ID：`{task.get("id")}`
- 生成时间：{now}
- 源视频：`{source_path}`
- 音频文件：`{audio_path}`
- 转写来源：`{_ACTIVE_TRANSCRIPTION_PROVIDER_LABEL}`
- 转写模型：`{_transcription_model_label()}`
- 转写语言：`{settings.transcription_language or "auto"}`
- 转写设备：`{_transcription_runtime_label()}`

## 逐句时间戳原文

| 开始 | 结束 | 文本 |
| --- | --- | --- |
{segment_table}
"""


def normalize_transcript_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def escape_markdown_table_text(text: str) -> str:
    return normalize_transcript_text(text).replace("|", "\\|")


def format_seconds(value: float) -> str:
    total_seconds = max(0, int(round(value)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def read_transcript_preview(transcript_path: Path, max_lines: int = 8) -> list[dict[str, str]]:
    if not transcript_path.exists():
        return []

    lines = transcript_path.read_text(encoding="utf-8", errors="replace").splitlines()
    sentence_lines = _extract_sentence_section_lines(lines)
    if sentence_lines:
        lines = sentence_lines
    preview = []
    for line in lines:
        stripped = line.strip()
        if not stripped or any(marker in stripped for marker in PLACEHOLDER_TEXT_MARKERS):
            continue
        table_match = TIME_TABLE_PATTERN.match(stripped)
        inline_match = INLINE_TIME_PATTERN.search(stripped)

        if table_match:
            text = table_match.group("text").strip()
            if not text:
                continue
            preview.append(
                {
                    "time": f"{table_match.group('start')} - {table_match.group('end')}",
                    "text": text,
                }
            )
        elif inline_match:
            text = inline_match.group("text").strip(" ：:-")
            if not text:
                continue
            preview.append(
                {
                    "time": f"{inline_match.group('start')} - {inline_match.group('end')}",
                    "text": text,
                }
            )

        if len(preview) >= max_lines:
            break
    return preview


def _extract_sentence_section_lines(lines: list[str]) -> list[str]:
    section_lines: list[str] = []
    in_sentence_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_sentence_section:
                break
            in_sentence_section = "逐句时间戳原文" in stripped
            continue
        if in_sentence_section:
            section_lines.append(line)
    return section_lines


def _time_text_to_seconds(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def read_transcript_range(
    transcript_path: Path,
    start_seconds: int,
    end_seconds: int,
    max_rows: int = 80,
) -> list[dict[str, str]]:
    if not transcript_path.exists():
        return []

    rows = []
    in_sentence_section = False
    for line in transcript_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_sentence_section = "逐句时间戳原文" in stripped
            continue
        if not in_sentence_section:
            continue

        match = TIME_TABLE_PATTERN.match(stripped)
        if not match:
            continue

        row_start = _time_text_to_seconds(match.group("start"))
        row_end = _time_text_to_seconds(match.group("end"))
        if row_start < end_seconds and row_end > start_seconds:
            text = match.group("text").strip()
            if text:
                rows.append(
                    {
                        "start_time": match.group("start"),
                        "end_time": match.group("end"),
                        "text": text,
                    }
                )
        if len(rows) >= max_rows:
            break
    return rows
