from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess

from app.core.config import settings


WINDOWS_UNSAFE_CHARS = r'<>:"/\|?*'


@dataclass(frozen=True)
class CutPlan:
    clip_candidate_id: str
    title: str
    start_time: str
    end_time: str
    output_path: Path
    duration_seconds: float


@dataclass(frozen=True)
class CutResult:
    clip_candidate_id: str
    output_file_path: str
    output_file_name: str
    status: str
    error_message: str | None = None


def ensure_ffmpeg_available() -> str:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError("FFmpeg 不可用：请确认已安装 FFmpeg，并已加入 Windows PATH")
    return ffmpeg_path


def parse_time_to_seconds(value: str) -> float:
    text = (value or "").strip()
    if not text:
        raise ValueError("时间不能为空")

    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)

    parts = text.split(":")
    if len(parts) not in {2, 3}:
        raise ValueError(f"时间格式不正确：{value}")

    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"时间格式不正确：{value}") from exc

    if any(number < 0 for number in numbers):
        raise ValueError(f"时间不能为负数：{value}")

    if len(numbers) == 2:
        minutes, seconds = numbers
        return minutes * 60 + seconds

    hours, minutes, seconds = numbers
    return hours * 3600 + minutes * 60 + seconds


def format_seconds_for_ffmpeg(seconds: float) -> str:
    return f"{seconds:.3f}"


def format_seconds_for_filename(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}-{minutes:02d}-{secs:02d}"


def sanitize_filename_part(value: str, fallback: str = "clip") -> str:
    text = (value or "").strip()
    text = re.sub(f"[{re.escape(WINDOWS_UNSAFE_CHARS)}]", "", text)
    text = re.sub(r"[\x00-\x1f]", "", text)
    text = re.sub(r"\s+", "_", text)
    text = text.strip(" ._")
    if not text:
        text = fallback
    return text[:60]


def unique_output_path(output_dir: Path, file_name: str) -> Path:
    output_path = output_dir / file_name
    if not output_path.exists():
        return output_path

    stem = output_path.stem
    suffix = output_path.suffix
    index = 2
    while True:
        candidate = output_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def build_output_path(output_dir: Path, index: int, clip: dict) -> CutPlan:
    start_seconds = parse_time_to_seconds(str(clip.get("start_time") or ""))
    end_seconds = parse_time_to_seconds(str(clip.get("end_time") or ""))
    if end_seconds <= start_seconds:
        raise ValueError("片段时间非法：结束时间必须晚于开始时间")

    title = str(clip.get("title") or "未命名片段")
    safe_title = sanitize_filename_part(title, fallback=f"clip_{index:02d}")
    start_label = format_seconds_for_filename(start_seconds)
    end_label = format_seconds_for_filename(end_seconds)
    file_name = f"{index:02d}_{safe_title}_{start_label}_{end_label}.mp4"
    output_path = unique_output_path(output_dir, file_name)

    return CutPlan(
        clip_candidate_id=str(clip.get("id") or ""),
        title=title,
        start_time=format_seconds_for_ffmpeg(start_seconds),
        end_time=format_seconds_for_ffmpeg(end_seconds),
        output_path=output_path,
        duration_seconds=end_seconds - start_seconds,
    )


def build_ffmpeg_cut_command(
    ffmpeg_path: str,
    source_video: Path,
    plan: CutPlan,
    strategy: str = "accurate",
) -> list[str]:
    if strategy == "fast":
        return [
            ffmpeg_path,
            "-y",
            "-ss",
            plan.start_time,
            "-i",
            str(source_video),
            "-t",
            format_seconds_for_ffmpeg(plan.duration_seconds),
            "-c",
            "copy",
            str(plan.output_path),
        ]

    return [
        ffmpeg_path,
        "-y",
        "-i",
        str(source_video),
        "-ss",
        plan.start_time,
        "-t",
        format_seconds_for_ffmpeg(plan.duration_seconds),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(plan.output_path),
    ]


def summarize_stderr(stderr: str, max_length: int = 700) -> str:
    cleaned = " ".join((stderr or "").split())
    if not cleaned:
        return "FFmpeg 切割失败，但未返回详细错误"
    return cleaned[:max_length]


def cut_single_clip(
    ffmpeg_path: str,
    source_video: Path,
    plan: CutPlan,
    strategy: str = "accurate",
) -> CutResult:
    plan.output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_cut_command(ffmpeg_path, source_video, plan, strategy=strategy)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=settings.ffmpeg_cut_timeout)
    if result.returncode != 0:
        return CutResult(
            clip_candidate_id=plan.clip_candidate_id,
            output_file_path=str(plan.output_path),
            output_file_name=plan.output_path.name,
            status="failed",
            error_message=summarize_stderr(result.stderr),
        )

    if not plan.output_path.exists() or plan.output_path.stat().st_size == 0:
        return CutResult(
            clip_candidate_id=plan.clip_candidate_id,
            output_file_path=str(plan.output_path),
            output_file_name=plan.output_path.name,
            status="failed",
            error_message="FFmpeg 已结束，但没有生成有效的视频文件",
        )

    return CutResult(
        clip_candidate_id=plan.clip_candidate_id,
        output_file_path=str(plan.output_path),
        output_file_name=plan.output_path.name,
        status="completed",
    )


def cut_clips(
    source_video: Path,
    clips: list[dict],
    output_dir: Path,
    strategy: str = "accurate",
) -> list[CutResult]:
    if not source_video.exists():
        raise FileNotFoundError("找不到原视频文件")
    if not source_video.is_file():
        raise ValueError("原视频路径不是文件")
    if not clips:
        raise ValueError("没有任何启用片段，无法生成切片")

    ffmpeg_path = ensure_ffmpeg_available()
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[CutResult] = []
    for index, clip in enumerate(clips, start=1):
        try:
            plan = build_output_path(output_dir, index, clip)
            results.append(cut_single_clip(ffmpeg_path, source_video, plan, strategy=strategy))
        except Exception as exc:
            results.append(
                CutResult(
                    clip_candidate_id=str(clip.get("id") or ""),
                    output_file_path="",
                    output_file_name="",
                    status="failed",
                    error_message=str(exc),
                )
            )
    return results
