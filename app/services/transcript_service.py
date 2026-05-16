from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import subprocess

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


_WHISPER_MODEL = None
_WHISPER_MODEL_KEY: tuple[str, str, str] | None = None


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
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
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
) -> dict[str, str]:
    if not audio_path.exists():
        raise RuntimeError("未找到音频文件，请先提取音频")

    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    segments = transcribe_audio(audio_path)
    content = build_transcript_markdown(task, audio_path, segments)
    transcript_path.write_text(content, encoding="utf-8")
    return {
        "status": "ok",
        "message": "真实转写 Markdown 已保存",
        "transcript_path": str(transcript_path),
        "segment_count": str(len(segments)),
    }


def transcribe_audio(audio_path: Path) -> list[TranscriptSegment]:
    model = _get_whisper_model()
    try:
        raw_segments, _info = model.transcribe(
            str(audio_path),
            language=settings.transcription_language or None,
            vad_filter=True,
            beam_size=5,
        )
        segments = [
            TranscriptSegment(
                start_seconds=float(segment.start),
                end_seconds=float(segment.end),
                text=normalize_transcript_text(segment.text),
            )
            for segment in raw_segments
            if normalize_transcript_text(segment.text)
        ]
    except Exception as exc:
        raise RuntimeError(f"本地语音转写失败：{exc}") from exc

    if not segments:
        raise RuntimeError("本地语音转写完成，但没有识别到可用语音内容")
    return segments


def _get_whisper_model():
    global _WHISPER_MODEL, _WHISPER_MODEL_KEY

    model_key = (
        settings.transcription_model,
        settings.transcription_device,
        settings.transcription_compute_type,
    )
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
            settings.transcription_model,
            device=settings.transcription_device,
            compute_type=settings.transcription_compute_type,
        )
        _WHISPER_MODEL_KEY = model_key
        return _WHISPER_MODEL
    except Exception as exc:
        raise RuntimeError(
            "本地转写模型加载失败。请确认已安装 faster-whisper，并确认 NVIDIA 显卡 / CUDA 环境可用；"
            "如果要改用 CPU，请在 .env 中设置 TRANSCRIPTION_DEVICE=cpu 和 TRANSCRIPTION_COMPUTE_TYPE=int8。"
            f" 原始错误：{exc}"
        ) from exc


def build_transcript_markdown(
    task: dict,
    audio_path: Path,
    segments: list[TranscriptSegment],
) -> str:
    source_path = task.get("source")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    minute_rows = build_minute_rows(segments)

    minute_table = "\n".join(
        f"| {format_seconds(start)} | {format_seconds(end)} | {escape_markdown_table_text(text)} |"
        for start, end, text in minute_rows
    )
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
- 转写模型：`{settings.transcription_model}`
- 转写语言：`{settings.transcription_language or "auto"}`
- 转写设备：`{settings.transcription_device}`

## 分钟级转写

| 开始 | 结束 | 文本 |
| --- | --- | --- |
{minute_table}

## 逐句时间戳原文

| 开始 | 结束 | 文本 |
| --- | --- | --- |
{segment_table}
"""


def build_minute_rows(segments: list[TranscriptSegment]) -> list[tuple[float, float, str]]:
    grouped: dict[int, list[str]] = {}
    for segment in segments:
        minute_index = max(0, int(segment.start_seconds // 60))
        grouped.setdefault(minute_index, []).append(segment.text)

    rows = []
    for minute_index in sorted(grouped):
        start = minute_index * 60
        end = start + 60
        text = normalize_transcript_text(" ".join(grouped[minute_index]))
        rows.append((start, end, text))
    return rows


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
