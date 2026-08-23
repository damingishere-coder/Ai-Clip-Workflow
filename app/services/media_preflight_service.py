"""长素材媒体与磁盘预检。

创建任务前只做只读探测，不改写原片。超过 6 小时是提示，不是拒绝条件。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.config import settings


MAX_TESTED_DURATION_SECONDS = 6 * 60 * 60
PCM_BYTES_PER_SECOND = 16_000 * 2
MIN_SAFETY_MARGIN_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True)
class MediaPreflight:
    path: str
    duration_seconds: float
    file_size_bytes: int
    video_codec: str
    audio_codec: str
    width: int
    height: int
    frame_rate: float
    audio_channels: int
    audio_sample_rate: int
    required_free_bytes: int
    available_free_bytes: int
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_rate(value: str | None) -> float:
    text = str(value or "0/1")
    try:
        numerator, denominator = text.split("/", 1)
        return float(numerator) / max(float(denominator), 1.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _run_decode_sample(path: Path, start_seconds: float) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("未找到 FFmpeg，无法验证视频可解码性")
    command = [ffmpeg, "-v", "error", "-xerror"]
    if start_seconds > 0:
        command.extend(["-ss", f"{start_seconds:.3f}"])
    command.extend(
        ["-i", str(path), "-t", "3", "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"]
    )
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=settings.ffprobe_timeout,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "未知解码错误").strip()
        raise ValueError(f"视频无法正常解码：{message[-500:]}")


def probe_media(path_value: str | Path) -> dict:
    path = Path(path_value).resolve()
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ValueError("未找到 FFprobe，无法创建视频任务")
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=settings.ffprobe_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"媒体探测超过 {settings.ffprobe_timeout} 秒，文件可能损坏") from exc
    if completed.returncode != 0:
        message = (completed.stderr or "FFprobe 无法读取文件").strip()
        raise ValueError(f"媒体探测失败：{message[-500:]}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("FFprobe 返回了无法解析的媒体信息") from exc

    streams = payload.get("streams") or []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video_stream:
        raise ValueError("源文件没有视频轨，不能创建视频处理任务")
    if not audio_stream:
        raise ValueError("源文件没有音轨，无法进行语言转写和高光选片")
    try:
        duration = float((payload.get("format") or {}).get("duration") or video_stream.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise ValueError("源文件时长无效，无法创建任务")

    _run_decode_sample(path, 0)
    if duration > 10:
        _run_decode_sample(path, max(0.0, duration - 5.0))
    return {
        "duration_seconds": duration,
        "file_size_bytes": path.stat().st_size,
        "video_codec": str(video_stream.get("codec_name") or "unknown"),
        "audio_codec": str(audio_stream.get("codec_name") or "unknown"),
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "frame_rate": _parse_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")),
        "audio_channels": int(audio_stream.get("channels") or 0),
        "audio_sample_rate": int(audio_stream.get("sample_rate") or 0),
    }


def estimate_required_bytes(
    *,
    duration_seconds: float,
    source_size_bytes: int,
    total_output_limit: int,
) -> int:
    pcm_bytes = int(duration_seconds * PCM_BYTES_PER_SECOND)
    # 默认按每条最多 5 分钟估算切片；同时预留一份字幕成片。
    output_fraction = min(1.0, total_output_limit * 300 / max(duration_seconds, 1.0))
    clip_and_subtitle_bytes = int(source_size_bytes * output_fraction * 2.2)
    working_margin = max(MIN_SAFETY_MARGIN_BYTES, int((pcm_bytes + clip_and_subtitle_bytes) * 0.25))
    return pcm_bytes + clip_and_subtitle_bytes + working_margin


def preflight_media(
    path_value: str | Path,
    *,
    total_output_limit: int,
) -> MediaPreflight:
    path = Path(path_value).resolve()
    probe = probe_media(path)
    required = estimate_required_bytes(
        duration_seconds=probe["duration_seconds"],
        source_size_bytes=probe["file_size_bytes"],
        total_output_limit=total_output_limit,
    )
    storage_anchor = settings.tasks_dir
    storage_anchor.mkdir(parents=True, exist_ok=True)
    available = shutil.disk_usage(storage_anchor).free
    if available < required:
        required_gib = required / (1024 ** 3)
        available_gib = available / (1024 ** 3)
        raise ValueError(
            f"任务存储空间不足：预计至少需要 {required_gib:.1f} GiB，当前可用 {available_gib:.1f} GiB"
        )
    warnings: list[str] = []
    if probe["duration_seconds"] > MAX_TESTED_DURATION_SECONDS:
        warnings.append("素材超过当前 6 小时验收范围，可以创建，但请重点关注耗时和磁盘空间")
    return MediaPreflight(
        path=str(path),
        required_free_bytes=required,
        available_free_bytes=available,
        warnings=warnings,
        **probe,
    )
