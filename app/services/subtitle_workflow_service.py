"""字幕工作流服务

从 task_service 中拆分出来的字幕样式、ASS 渲染和字幕烧录函数。
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.storage_service import get_artifact_paths, resolve_video_file_path
from app.services.transcript_service import read_transcript_range

# ---------- 字幕字体常量 ----------

WINDOWS_FONTS_DIR = Path("C:/Windows/Fonts")
SUBTITLE_FONT_FILE_CANDIDATES = {
    "Microsoft YaHei": ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc"),
    "SimHei": ("simhei.ttf",),
    "Noto Sans SC": ("NotoSansSC-VF.ttf",),
    "Source Han Sans CN": ("SourceHanSansCN-Regular.otf", "SourceHanSansCN-Normal.otf", "SourceHanSansCN-Medium.otf"),
    "SimSun": ("simsun.ttc",),
    "DengXian": ("Deng.ttf",),
}
SUBTITLE_CJK_FONT_FALLBACKS = ("Microsoft YaHei", "SimHei", "Noto Sans SC", "Source Han Sans CN", "SimSun", "DengXian")

SUBTITLE_STATUS_LABELS = {
    "pending": "待加字幕",
    "processing": "字幕生成中",
    "completed": "已加字幕",
    "failed": "字幕失败",
}


# ---------- 数据库读/写 ----------

def get_default_subtitle_style() -> dict:
    from app.db.database import get_connection

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM subtitle_style_presets
            WHERE is_default = 1
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return {
            "id": "default",
            "name": "默认字幕样式",
            "font_family": "Microsoft YaHei",
            "font_size": 42,
            "position": "bottom_center",
            "font_color": "#ffffff",
            "stroke_color": "#111827",
            "shadow_enabled": True,
        }
    style = dict(row)
    style["shadow_enabled"] = bool(style.get("shadow_enabled"))
    return style


def update_default_subtitle_style(payload) -> dict:
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT id FROM subtitle_style_presets WHERE id = ?",
            ("default",),
        ).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE subtitle_style_presets
                SET font_family = ?, font_size = ?, position = ?, font_color = ?,
                    stroke_color = ?, shadow_enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.font_family,
                    payload.font_size,
                    payload.position,
                    payload.font_color,
                    payload.stroke_color,
                    1 if payload.shadow_enabled else 0,
                    now,
                    "default",
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO subtitle_style_presets (
                    id, name, font_family, font_size, position, font_color,
                    stroke_color, shadow_enabled, is_default, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "default",
                    "默认字幕样式",
                    payload.font_family,
                    payload.font_size,
                    payload.position,
                    payload.font_color,
                    payload.stroke_color,
                    1 if payload.shadow_enabled else 0,
                    1,
                    now,
                    now,
                ),
            )
        connection.commit()
    return {
        "status": "ok",
        "message": "字幕样式已保存到数据库。",
        "style": get_default_subtitle_style(),
    }


def _subtitle_job_for_output(task_id: str, output_clip_id: str) -> dict | None:
    from app.db.database import get_connection

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM subtitle_jobs
            WHERE task_id = ? AND output_clip_id = ?
            """,
            (task_id, output_clip_id),
        ).fetchone()
    return dict(row) if row else None


def _upsert_subtitle_job(
    task_id: str,
    output_clip_id: str,
    status: str,
    subtitle_file_path: str = "",
    output_file_path: str = "",
    error_message: str = "",
) -> dict:
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    existing = _subtitle_job_for_output(task_id, output_clip_id)
    with get_connection() as connection:
        if existing:
            connection.execute(
                """
                UPDATE subtitle_jobs
                SET status = ?, style_preset_id = ?, subtitle_file_path = ?,
                    output_file_path = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    "default",
                    subtitle_file_path or existing.get("subtitle_file_path") or "",
                    output_file_path or existing.get("output_file_path") or "",
                    error_message,
                    now,
                    existing["id"],
                ),
            )
            job_id = existing["id"]
        else:
            job_id = uuid4().hex[:12]
            connection.execute(
                """
                INSERT INTO subtitle_jobs (
                    id, task_id, output_clip_id, style_preset_id, status,
                    subtitle_file_path, output_file_path, error_message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    task_id,
                    output_clip_id,
                    "default",
                    status,
                    subtitle_file_path,
                    output_file_path,
                    error_message,
                    now,
                    now,
                ),
            )
        connection.commit()
    return _subtitle_job_for_output(task_id, output_clip_id) or {"id": job_id, "status": status}


# ---------- ASS 字幕渲染 ----------

def _hex_to_ass_color(value: str) -> str:
    cleaned = (value or "#ffffff").lstrip("#")
    if len(cleaned) != 6:
        cleaned = "ffffff"
    red, green, blue = cleaned[0:2], cleaned[2:4], cleaned[4:6]
    return f"&H00{blue}{green}{red}".upper()


def _ass_time(seconds: float) -> str:
    total_centiseconds = max(0, int(round(seconds * 100)))
    total_seconds, centiseconds = divmod(total_centiseconds, 100)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def _escape_ass_text(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _subtitle_font_exists(font_family: str) -> bool:
    candidates = SUBTITLE_FONT_FILE_CANDIDATES.get(font_family, ())
    return any((WINDOWS_FONTS_DIR / file_name).exists() for file_name in candidates)


def _resolve_subtitle_font_family(requested_font_family: str | None) -> str:
    font_family = (requested_font_family or "").strip()
    if font_family and _subtitle_font_exists(font_family):
        return font_family
    for fallback_font_family in SUBTITLE_CJK_FONT_FALLBACKS:
        if _subtitle_font_exists(fallback_font_family):
            return fallback_font_family
    return font_family or "Microsoft YaHei"


def _build_subtitle_rows(task_id: str, output_clip: dict) -> tuple[int, list[dict[str, Any]]]:
    from app.services.task_service import _parse_time_to_seconds, get_clip_candidate  # noqa: F811

    clip = get_clip_candidate(task_id, output_clip["clip_candidate_id"]) if output_clip.get("clip_candidate_id") else None
    if not clip:
        return 0, [{"start_seconds": 0, "end_seconds": 3, "text": output_clip.get("output_file_name") or "精彩片段"}]

    clip_start = int(clip["start_seconds"])
    clip_end = int(clip["end_seconds"])
    rows = read_transcript_range(get_artifact_paths(task_id)["transcript_path"], clip_start, clip_end, max_rows=120)
    subtitle_rows = []
    for row in rows:
        row_start = _parse_time_to_seconds(row["start_time"])
        row_end = _parse_time_to_seconds(row["end_time"])
        start_seconds = max(0, row_start - clip_start)
        end_seconds = max(start_seconds + 1, min(clip_end, row_end) - clip_start)
        subtitle_rows.append({"start_seconds": start_seconds, "end_seconds": end_seconds, "text": row["text"]})
    if subtitle_rows:
        return clip_start, subtitle_rows

    fallback_text = clip.get("summary") or clip.get("title") or "精彩片段"
    return clip_start, [{"start_seconds": 0, "end_seconds": min(5, max(3, clip_end - clip_start)), "text": fallback_text}]


def _write_ass_file(task_id: str, output_clip: dict, style: dict) -> Path:
    paths = get_artifact_paths(task_id)
    paths["subtitled_dir"].mkdir(parents=True, exist_ok=True)
    subtitle_path = paths["subtitled_dir"] / f"{Path(output_clip.get('output_file_name') or output_clip['id']).stem}.ass"
    _, rows = _build_subtitle_rows(task_id, output_clip)

    alignment = "8" if style.get("position") == "top_center" else "2"
    margin_v = "92" if style.get("position") == "bottom_center" else "190"
    if style.get("position") == "top_center":
        margin_v = "70"
    outline = "3" if style.get("shadow_enabled") else "1"
    shadow = "1" if style.get("shadow_enabled") else "0"
    font_family = _resolve_subtitle_font_family(style.get("font_family"))
    font_size = int(style.get("font_size") or 42)
    primary_color = _hex_to_ass_color(style.get("font_color") or "#ffffff")
    outline_color = _hex_to_ass_color(style.get("stroke_color") or "#111827")
    events = "\n".join(
        f"Dialogue: 0,{_ass_time(row['start_seconds'])},{_ass_time(row['end_seconds'])},Default,,0,0,0,,{_escape_ass_text(row['text'])}"
        for row in rows
    )
    content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_family},{font_size},{primary_color},&H000000FF,{outline_color},&H7F000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},{alignment},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
{events}
"""
    subtitle_path.write_text(content, encoding="utf-8")
    return subtitle_path


def _ffmpeg_filter_path(path: Path) -> str:
    normalized = str(path.resolve()).replace("\\", "/")
    return normalized.replace(":", r"\:").replace("'", r"\'")


def _ffmpeg_subtitles_filter(subtitle_path: Path) -> str:
    filter_parts = [f"filename='{_ffmpeg_filter_path(subtitle_path)}'"]
    if WINDOWS_FONTS_DIR.exists():
        filter_parts.append(f"fontsdir='{_ffmpeg_filter_path(WINDOWS_FONTS_DIR)}'")
    return f"subtitles={':'.join(filter_parts)}"


# ---------- 字幕烧录入口 ----------

def render_subtitles_for_output_clip(task_id: str, output_clip_id: str) -> dict:
    from app.services.task_log_service import append_task_log
    from app.services.task_service import get_output_clip, get_task  # noqa: F811

    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    output_clip = get_output_clip(task_id, output_clip_id)
    if not output_clip:
        raise ValueError("切片记录不存在")
    input_path = resolve_video_file_path(output_clip.get("output_file_path")) or Path(output_clip.get("output_file_path") or "")
    if output_clip.get("status") != "completed" or not input_path.exists():
        raise ValueError("切片视频文件不存在，不能加字幕")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg 不可用，无法生成字幕视频")

    style = get_default_subtitle_style()
    paths = get_artifact_paths(task_id)
    paths["subtitled_dir"].mkdir(parents=True, exist_ok=True)
    output_path = paths["subtitled_dir"] / f"{input_path.stem}_subtitled.mp4"
    job = _upsert_subtitle_job(task_id, output_clip_id, "processing")
    append_task_log(task_id, f"开始自动加字幕：{input_path.name}")

    try:
        subtitle_path = _write_ass_file(task_id, output_clip, style)
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            _ffmpeg_subtitles_filter(subtitle_path),
            "-c:a",
            "copy",
            str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "FFmpeg 字幕生成失败")
    except Exception as exc:
        error = str(exc)
        _upsert_subtitle_job(task_id, output_clip_id, "failed", error_message=error)
        append_task_log(task_id, f"自动加字幕失败：{input_path.name}，原因：{error}")
        raise

    job = _upsert_subtitle_job(
        task_id,
        output_clip_id,
        "completed",
        subtitle_file_path=str(subtitle_path),
        output_file_path=str(output_path),
    )
    append_task_log(task_id, f"自动加字幕完成：{output_path.name}")
    return {
        "status": "ok",
        "message": "自动加字幕完成，已生成带字幕视频。",
        "job": job,
        "output_clip": get_output_clip(task_id, output_clip_id),
        "media_url": f"/media/tasks/{task_id}/subtitled-clips/{output_clip_id}",
    }
