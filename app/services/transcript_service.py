from dataclasses import dataclass
from datetime import datetime
import base64
import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from app.core.config import settings


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
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str


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


def run_ffmpeg_audio_extract(video_path: Path, output_path: Path) -> dict[str, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=settings.ffmpeg_audio_extract_timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "FFmpeg 音频提取失败")
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
        message="正在读取音频时长",
    )
    segments = transcribe_audio_with_configured_provider(
        audio_path,
        transcript_path.parent,
        progress_path,
        progress_callback,
        provider=provider,
    )
    content = build_transcript_markdown(task, audio_path, segments)
    temp_path = transcript_path.with_name(f"{transcript_path.name}.tmp")
    temp_path.write_text(content, encoding="utf-8")
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
) -> list[TranscriptSegment]:
    provider = _normalize_provider_name(provider or settings.transcription_provider)
    fallback_provider = _normalize_provider_name(settings.transcription_fallback_provider)
    try:
        return transcribe_audio_with_provider(audio_path, working_dir, progress_path, provider, progress_callback)
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
) -> list[TranscriptSegment]:
    provider = _normalize_provider_name(provider)
    if provider == "local":
        _set_active_transcription_runtime(
            provider="local",
            provider_label="本地 faster-whisper",
            model=(_WHISPER_MODEL_KEY or _primary_model_key())[0],
            device=(_WHISPER_MODEL_KEY or _primary_model_key())[1],
            compute_type=(_WHISPER_MODEL_KEY or _primary_model_key())[2],
        )
        return transcribe_audio_in_chunks(audio_path, working_dir, progress_path, progress_callback)
    if provider == "volcengine":
        return transcribe_audio_with_volcengine(audio_path, working_dir, progress_path, progress_callback)
    if provider in _RESERVED_REMOTE_PROVIDERS:
        raise RuntimeError(f"{_provider_label(provider)} 转写接口已预留，但当前版本还没有完整接入")
    raise RuntimeError(f"未知转写服务商：{provider or '空'}")


def transcribe_audio_in_chunks(
    audio_path: Path,
    working_dir: Path,
    progress_path: Path,
    progress_callback: Callable[[dict], None] | None = None,
) -> list[TranscriptSegment]:
    duration_seconds = get_audio_duration_seconds(audio_path)
    chunks = build_transcript_chunks(
        duration_seconds,
        chunk_seconds=settings.transcription_chunk_seconds,
        overlap_seconds=settings.transcription_chunk_overlap_seconds,
    )
    if not chunks:
        raise RuntimeError("本地语音转写失败：音频时长无效，无法分段")
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
            chunk_segments = transcribe_audio(chunk_path, allow_empty=True)
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
    return sorted(all_segments, key=lambda segment: (segment.start_seconds, segment.end_seconds))


def transcribe_audio_with_volcengine(
    audio_path: Path,
    working_dir: Path,
    progress_path: Path,
    progress_callback: Callable[[dict], None] | None = None,
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
            chunk_segments = transcribe_audio_with_volcengine_flash(chunk_path, allow_empty=True)
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
    return sorted(all_segments, key=lambda segment: (segment.start_seconds, segment.end_seconds))


def transcribe_audio_with_volcengine_flash(audio_path: Path, allow_empty: bool = False) -> list[TranscriptSegment]:
    _ensure_volcengine_configured()
    headers = _volcengine_headers()
    payload = _build_volcengine_flash_payload(audio_path)
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
        raise RuntimeError(f"火山引擎接口返回 HTTP {exc.code}：{error_text[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"无法连接火山引擎转写接口：{exc}") from exc

    if status_code and status_code != "20000000":
        if allow_empty and status_code == "20000003":
            return []
        raise RuntimeError(
            "火山引擎接口返回业务错误："
            f"code={status_code}, message={status_message or 'unknown'}, logid={log_id or 'unknown'}"
        )

    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"火山引擎接口返回的不是 JSON：{response_text[:500]}") from exc

    segments = parse_volcengine_transcript_segments(payload)
    if not segments and not allow_empty:
        raise RuntimeError(f"火山引擎远程转写没有返回可用文本：{json.dumps(payload, ensure_ascii=False)[:500]}")
    return segments


def parse_volcengine_transcript_segments(payload: dict) -> list[TranscriptSegment]:
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        result = payload

    utterances = result.get("utterances") if isinstance(result, dict) else None
    if isinstance(utterances, list) and utterances:
        segments = [_segment_from_volcengine_utterance(item) for item in utterances if isinstance(item, dict)]
        return [segment for segment in segments if segment and segment.text]

    text = normalize_transcript_text(str(result.get("text") or result.get("message") or "")) if isinstance(result, dict) else ""
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
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=settings.ffprobe_timeout)
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
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=settings.ffmpeg_chunk_timeout)
    if result.returncode != 0:
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
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=settings.ffmpeg_chunk_timeout)
    if result.returncode != 0:
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


def _volcengine_headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Api-Resource-Id": settings.volcengine_asr_resource_id,
        "X-Api-Request-Id": uuid4().hex,
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
    return TranscriptSegment(start_seconds=start_seconds, end_seconds=end_seconds, text=text)


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
            )
        )
    return adjusted_segments


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
        segments = _transcribe_audio_with_model(_get_whisper_model(), audio_path)
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


def _transcribe_audio_with_model(model, audio_path: Path) -> list[TranscriptSegment]:
    raw_segments, _info = model.transcribe(
        str(audio_path),
        language=settings.transcription_language or None,
        vad_filter=True,
        beam_size=5,
    )
    return [
        TranscriptSegment(
            start_seconds=float(segment.start),
            end_seconds=float(segment.end),
            text=normalize_transcript_text(segment.text),
        )
        for segment in raw_segments
        if normalize_transcript_text(segment.text)
    ]


def _get_whisper_model(model_key: tuple[str, str, str] | None = None):
    global _WHISPER_MODEL, _WHISPER_MODEL_KEY

    model_key = model_key or _primary_model_key()
    if _WHISPER_MODEL is not None and _WHISPER_MODEL_KEY == model_key:
        return _WHISPER_MODEL

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "未安装本地转写依赖 faster-whisper。请先安装 requirements.txt 里的依赖，再重新生成转写。"
        ) from exc

    try:
        _WHISPER_MODEL = WhisperModel(
            model_key[0],
            device=model_key[1],
            compute_type=model_key[2],
        )
        _WHISPER_MODEL_KEY = model_key
        if _ACTIVE_TRANSCRIPTION_PROVIDER == "local":
            _set_active_transcription_runtime(
                provider="local",
                provider_label="本地 faster-whisper",
                model=model_key[0],
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
    return (
        settings.transcription_model,
        settings.transcription_device,
        settings.transcription_compute_type,
    )


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
            model=model,
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
    return model


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
