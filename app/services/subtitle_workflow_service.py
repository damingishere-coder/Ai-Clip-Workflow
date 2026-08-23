"""字幕工作流服务

从 task_service 中拆分出来的字幕样式、ASS 渲染和字幕烧录函数。
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.storage_service import get_artifact_paths, resolve_video_file_path

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
DEFAULT_SPEAKER_STYLES = {
    "主播": {"font_color": "#ffffff"},
    "嘉宾": {"font_color": "#ffd60a"},
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
            "outline_width": 3,
            "shadow_depth": 1,
            "safe_area_percent": 5,
            "speaker_styles": DEFAULT_SPEAKER_STYLES,
        }
    style = dict(row)
    style["shadow_enabled"] = bool(style.get("shadow_enabled"))
    try:
        style["speaker_styles"] = json.loads(style.get("speaker_styles_json") or "{}")
    except json.JSONDecodeError:
        style["speaker_styles"] = {}
    if not style["speaker_styles"]:
        style["speaker_styles"] = DEFAULT_SPEAKER_STYLES
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
                    stroke_color = ?, shadow_enabled = ?, outline_width = ?,
                    shadow_depth = ?, safe_area_percent = ?, speaker_styles_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.font_family,
                    payload.font_size,
                    payload.position,
                    payload.font_color,
                    payload.stroke_color,
                    1 if payload.shadow_enabled else 0,
                    payload.outline_width,
                    payload.shadow_depth,
                    payload.safe_area_percent,
                    json.dumps(payload.speaker_styles, ensure_ascii=False, separators=(",", ":")),
                    now,
                    "default",
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO subtitle_style_presets (
                    id, name, font_family, font_size, position, font_color,
                    stroke_color, shadow_enabled, outline_width, shadow_depth,
                    safe_area_percent, speaker_styles_json,
                    is_default, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    payload.outline_width,
                    payload.shadow_depth,
                    payload.safe_area_percent,
                    json.dumps(payload.speaker_styles, ensure_ascii=False, separators=(",", ":")),
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


def _subtitle_job_for_output(task_id: str, output_clip_id: str, active_only: bool = True) -> dict | None:
    from app.db.database import get_connection

    with get_connection() as connection:
        if active_only:
            row = connection.execute(
                """
                SELECT *
                FROM subtitle_jobs
                WHERE task_id = ? AND output_clip_id = ? AND is_active = 1
                """,
                (task_id, output_clip_id),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT *
                FROM subtitle_jobs
                WHERE task_id = ? AND output_clip_id = ?
                ORDER BY is_active DESC, created_at DESC
                LIMIT 1
                """,
                (task_id, output_clip_id),
            ).fetchone()
    return dict(row) if row else None


def _create_subtitle_job(
    task_id: str,
    output_clip_id: str,
    status: str,
    subtitle_file_path: str = "",
    output_file_path: str = "",
    error_message: str = "",
    is_active: int = 0,
    revision_id: str | None = None,
) -> dict:
    """创建新的字幕任务记录（不再 upsert，每次生成都创建新记录）"""
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    job_id = uuid4().hex[:12]
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO subtitle_jobs (
                id, task_id, output_clip_id, revision_id, style_preset_id, status,
                subtitle_file_path, output_file_path, error_message,
                is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                task_id,
                output_clip_id,
                revision_id,
                "default",
                status,
                subtitle_file_path,
                output_file_path,
                error_message,
                is_active,
                now,
                now,
            ),
        )
        connection.commit()
    return {"id": job_id, "task_id": task_id, "output_clip_id": output_clip_id,
            "revision_id": revision_id, "status": status,
            "subtitle_file_path": subtitle_file_path, "output_file_path": output_file_path,
            "error_message": error_message, "is_active": is_active}


def _activate_subtitle_job(task_id: str, output_clip_id: str, job_id: str) -> None:
    """激活指定的字幕任务，同时将该 output_clip 下的其他字幕任务标记为非活跃"""
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        # 将同 output_clip 下所有其他字幕 job 设为非活跃
        connection.execute(
            "UPDATE subtitle_jobs SET is_active = 0, updated_at = ? WHERE task_id = ? AND output_clip_id = ? AND id != ?",
            (now, task_id, output_clip_id, job_id),
        )
        # 激活当前 job
        connection.execute(
            "UPDATE subtitle_jobs SET is_active = 1, updated_at = ? WHERE id = ?",
            (now, job_id),
        )
        connection.commit()


def _update_subtitle_job_status(job_id: str, status: str, error_message: str = "") -> None:
    """更新字幕任务状态（不改变 is_active）"""
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        if error_message:
            connection.execute(
                "UPDATE subtitle_jobs SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
                (status, error_message, now, job_id),
            )
        else:
            connection.execute(
                "UPDATE subtitle_jobs SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, job_id),
            )
        connection.commit()


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
    """旧调用方兼容导出；数据来自统一 revision，不再读取并截断 Markdown。"""
    from app.services.subtitle_data_service import ensure_clip_track, get_revision

    track = ensure_clip_track(task_id, output_clip["id"])
    revision = get_revision(track["active_revision_id"], include_cues=True)
    source_start_ms = int(track.get("source_start_ms") or 0)
    rows = [
        {
            "start_seconds": int(cue["start_ms"]) / 1000,
            "end_seconds": int(cue["end_ms"]) / 1000,
            "text": cue["text"],
        }
        for cue in revision["cues"]
    ]
    return round(source_start_ms / 1000), rows


def _write_ass_file(
    task_id: str,
    output_clip: dict,
    style: dict,
    *,
    revision_id: str | None = None,
) -> Path:
    from app.services.subtitle_data_service import ensure_clip_track, serialize_revision_to_ass

    paths = get_artifact_paths(task_id)
    paths["subtitled_dir"].mkdir(parents=True, exist_ok=True)
    subtitle_path = paths["subtitled_dir"] / f"{Path(output_clip.get('output_file_name') or output_clip['id']).stem}.ass"
    track = ensure_clip_track(task_id, output_clip["id"])
    selected_revision_id = revision_id or track.get("active_revision_id")
    if not selected_revision_id:
        raise ValueError("切片字幕轨没有可渲染的 revision")
    content = serialize_revision_to_ass(track["id"], selected_revision_id)
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

    from app.services.subtitle_data_service import ensure_clip_track

    style = get_default_subtitle_style()
    track = ensure_clip_track(task_id, output_clip_id)
    revision_id = track.get("active_revision_id")
    if not revision_id:
        raise ValueError("切片字幕轨没有可渲染的 revision")
    paths = get_artifact_paths(task_id)
    paths["subtitled_dir"].mkdir(parents=True, exist_ok=True)
    output_path = paths["subtitled_dir"] / f"{input_path.stem}_subtitled.mp4"

    # === 版本化：创建新的字幕 job，不覆盖旧的 ===
    job = _create_subtitle_job(
        task_id,
        output_clip_id,
        "processing",
        is_active=0,
        revision_id=revision_id,
    )
    append_task_log(task_id, f"开始自动加字幕：{input_path.name}")

    try:
        subtitle_path = _write_ass_file(
            task_id,
            output_clip,
            style,
            revision_id=revision_id,
        )
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
        # 失败时：标记当前 job 为 failed，不激活，旧字幕保持 active
        _update_subtitle_job_status(job["id"], "failed", error_message=error)
        append_task_log(task_id, f"自动加字幕失败：{input_path.name}，原因：{error}")
        raise

    # 成功：更新 job 信息并切换为 active
    _update_subtitle_job_status(job["id"], "completed")
    # 用 subtitle_file_path 和 output_file_path 更新记录
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            "UPDATE subtitle_jobs SET subtitle_file_path = ?, output_file_path = ?, updated_at = ? WHERE id = ?",
            (str(subtitle_path), str(output_path), now, job["id"]),
        )
        connection.commit()

    # 激活当前字幕 job，旧字幕 job 标记为非活跃
    _activate_subtitle_job(task_id, output_clip_id, job["id"])
    append_task_log(task_id, f"自动加字幕完成：{output_path.name}")

    job = _subtitle_job_for_output(task_id, output_clip_id, active_only=False) or job
    return {
        "status": "ok",
        "message": "自动加字幕完成，已生成带字幕视频。",
        "job": job,
        "output_clip": get_output_clip(task_id, output_clip_id),
        "media_url": f"/media/tasks/{task_id}/subtitled-clips/{output_clip_id}",
    }
