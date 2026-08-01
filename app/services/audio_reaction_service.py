"""轻量音频反应特征。

这里只计算音量、动态、停顿和短句密度等代理信号，不把它描述成精确的
笑声分类或说话人识别。
"""

from __future__ import annotations

from array import array
import math
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import wave
from typing import Any

from app.core.config import settings


REACTION_SAMPLE_RATE = 16_000
REACTION_FRAME_SECONDS = 0.1
_LAUGHTER_PATTERN = re.compile(r"(?:哈){2,}|哈哈|笑死|爆笑|大笑|笑声")


def analyze_audio_reaction(
    audio_path: Path,
    start_seconds: float,
    end_seconds: float,
    key_moment_seconds: float | None,
    transcript_rows: list[Any],
) -> dict[str, Any]:
    duration = max(0.0, float(end_seconds) - float(start_seconds))
    if duration <= 0 or not audio_path.exists():
        return _unavailable("未找到可分析的音频")

    try:
        samples = _read_pcm_samples(audio_path, start_seconds, duration)
    except Exception as exc:
        return _unavailable(f"音频反应分析已降级：{exc}")
    if not samples:
        return _unavailable("音频片段为空")

    rms_frames = _rms_frames(samples, REACTION_SAMPLE_RATE)
    if not rms_frames:
        return _unavailable("没有计算到有效音量帧")

    texts = [str(getattr(row, "text", "") or "") for row in transcript_rows]
    laughter_tokens = len(_LAUGHTER_PATTERN.findall(" ".join(texts)))
    rapid_turns = sum(
        1
        for row in transcript_rows
        if _row_duration(row) <= 3.0 and 0 < len(str(getattr(row, "text", "") or "").strip()) <= 20
    )
    turns_per_minute = rapid_turns / max(duration / 60, 0.25)

    median_rms = statistics.median(rms_frames)
    p90_rms = _percentile(rms_frames, 0.9)
    mean_rms = statistics.fmean(rms_frames)
    dynamic_ratio = statistics.pstdev(rms_frames) / max(mean_rms, 1.0)
    silence_threshold = max(180.0, median_rms * 0.35)
    silence_ratio = sum(value <= silence_threshold for value in rms_frames) / len(rms_frames)

    reaction_ratio = _reaction_ratio(
        rms_frames,
        start_seconds=start_seconds,
        key_moment_seconds=key_moment_seconds,
    )
    laughter_component = min(35.0, laughter_tokens * 18.0)
    burst_component = min(25.0, max(0.0, (reaction_ratio - 1.0) / 1.5 * 25.0))
    dynamic_component = min(15.0, dynamic_ratio / 0.9 * 15.0)
    pause_component = (
        min(10.0, silence_ratio / 0.18 * 10.0)
        if 0.02 <= silence_ratio <= 0.4
        else 0.0
    )
    turn_component = min(15.0, turns_per_minute / 12.0 * 15.0)
    score = round(
        laughter_component
        + burst_component
        + dynamic_component
        + pause_component
        + turn_component,
        1,
    )

    labels = []
    if laughter_tokens:
        labels.append(f"转写命中 {laughter_tokens} 处笑声词")
    if reaction_ratio >= 1.35:
        labels.append("笑点后出现明显音量反应")
    if dynamic_ratio >= 0.45:
        labels.append("现场声音动态变化明显")
    if pause_component >= 5:
        labels.append("片段中存在短暂停顿与节奏变化")
    if turns_per_minute >= 8:
        labels.append("短句往返较密集")
    if not labels:
        labels.append("未检测到明显现场反应代理信号")

    return {
        "available": True,
        "score": score,
        "laughter_token_count": laughter_tokens,
        "reaction_loudness_ratio": round(reaction_ratio, 3),
        "dynamic_ratio": round(dynamic_ratio, 3),
        "silence_ratio": round(silence_ratio, 3),
        "rapid_turns_per_minute": round(turns_per_minute, 2),
        "median_rms": round(median_rms, 2),
        "peak_rms": round(p90_rms, 2),
        "component_scores": {
            "laughter_words": round(laughter_component, 1),
            "post_moment_burst": round(burst_component, 1),
            "dynamics": round(dynamic_component, 1),
            "pauses": round(pause_component, 1),
            "rapid_turns": round(turn_component, 1),
        },
        "labels": labels,
    }


def _read_pcm_samples(audio_path: Path, start_seconds: float, duration_seconds: float) -> array:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, start_seconds):.3f}",
            "-i",
            str(audio_path),
            "-t",
            f"{duration_seconds:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(REACTION_SAMPLE_RATE),
            "-f",
            "s16le",
            "pipe:1",
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=min(settings.ffmpeg_chunk_timeout, 180),
        )
        if result.returncode == 0 and result.stdout:
            samples = array("h")
            samples.frombytes(result.stdout[: len(result.stdout) - (len(result.stdout) % 2)])
            return samples
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if audio_path.suffix.lower() != ".wav":
            raise RuntimeError(detail or "FFmpeg 无法读取音频")

    if audio_path.suffix.lower() != ".wav":
        raise RuntimeError("FFmpeg 不可用，且音频不是 WAV")
    return _read_wave_samples(audio_path, start_seconds, duration_seconds)


def _read_wave_samples(audio_path: Path, start_seconds: float, duration_seconds: float) -> array:
    with wave.open(str(audio_path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise RuntimeError("WAV 不是 16 位 PCM，无法使用轻量降级读取")
        source_rate = handle.getframerate()
        channels = handle.getnchannels()
        handle.setpos(min(handle.getnframes(), int(max(0, start_seconds) * source_rate)))
        raw = handle.readframes(int(duration_seconds * source_rate))
    source = array("h")
    source.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if channels > 1:
        source = array("h", (source[index] for index in range(0, len(source), channels)))
    if source_rate == REACTION_SAMPLE_RATE:
        return source
    step = max(1, round(source_rate / REACTION_SAMPLE_RATE))
    return array("h", source[::step])


def _rms_frames(samples: array, sample_rate: int) -> list[float]:
    frame_size = max(1, int(sample_rate * REACTION_FRAME_SECONDS))
    values = []
    for offset in range(0, len(samples), frame_size):
        frame = samples[offset : offset + frame_size]
        if not frame:
            continue
        mean_square = sum(float(value) * float(value) for value in frame) / len(frame)
        values.append(math.sqrt(mean_square))
    return values


def _reaction_ratio(
    rms_frames: list[float],
    *,
    start_seconds: float,
    key_moment_seconds: float | None,
) -> float:
    if key_moment_seconds is None:
        return _percentile(rms_frames, 0.9) / max(statistics.median(rms_frames), 1.0)
    key_index = int(max(0.0, key_moment_seconds - start_seconds) / REACTION_FRAME_SECONDS)
    before = rms_frames[max(0, key_index - 20) : max(1, key_index)]
    after = rms_frames[key_index : min(len(rms_frames), key_index + 80)]
    if not before or not after:
        return _percentile(rms_frames, 0.9) / max(statistics.median(rms_frames), 1.0)
    return _percentile(after, 0.9) / max(statistics.median(before), 1.0)


def _row_duration(row: Any) -> float:
    start = float(getattr(row, "start_seconds", 0) or 0)
    end = float(getattr(row, "end_seconds", start) or start)
    return max(0.0, end - start)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "score": 0.0, "reason": reason, "labels": [reason]}


__all__ = ["analyze_audio_reaction"]
