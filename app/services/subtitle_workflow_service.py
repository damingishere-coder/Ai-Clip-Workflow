"""字幕工作流服务

从 task_service 中拆分出来的字幕样式、ASS 渲染和字幕烧录函数。
"""

import hashlib
import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.services import job_service
from app.services.managed_process_service import popen_process_group, terminate_process_tree
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
    "queued": "字幕排队中",
    "processing": "字幕生成中",
    "completed": "已加字幕",
    "failed": "字幕失败",
    "cancelled": "字幕已取消",
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
    workflow_job_id: str | None = None,
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
                id, task_id, output_clip_id, revision_id, workflow_job_id, style_preset_id, status,
                subtitle_file_path, output_file_path, error_message,
                is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                task_id,
                output_clip_id,
                revision_id,
                workflow_job_id,
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
            "revision_id": revision_id, "workflow_job_id": workflow_job_id, "status": status,
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


def _update_subtitle_job_status(
    job_id: str,
    status: str,
    error_message: str = "",
    *,
    workflow_job_id: str | None = None,
) -> None:
    """更新字幕任务状态（不改变 is_active）"""
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = _now_iso()
        lease = job_service.current_job_lease() if workflow_job_id else None
        if workflow_job_id:
            if not lease or lease[0] != workflow_job_id:
                connection.rollback()
                raise job_service.JobLeaseLostError(f"字幕 Job 缺少当前执行租约：{workflow_job_id}")
            active = connection.execute(
                """
                SELECT 1 FROM workflow_jobs
                WHERE id = ? AND status = 'running' AND lease_owner = ? AND lease_token = ?
                  AND lease_expires_at > ?
                """,
                (workflow_job_id, lease[1], lease[2], now),
            ).fetchone()
            if not active:
                connection.rollback()
                raise job_service.JobLeaseLostError(f"字幕 Job 租约已失效：{workflow_job_id}")
        condition = "id = ?"
        params: tuple[str, ...] = (job_id,)
        if workflow_job_id:
            condition += " AND workflow_job_id = ? AND is_active = 0"
            params = (job_id, workflow_job_id)
        if error_message:
            cursor = connection.execute(
                f"UPDATE subtitle_jobs SET status = ?, error_message = ?, updated_at = ? WHERE {condition}",
                (status, error_message, now, *params),
            )
        else:
            cursor = connection.execute(
                f"UPDATE subtitle_jobs SET status = ?, updated_at = ? WHERE {condition}",
                (status, now, *params),
            )
        if workflow_job_id and cursor.rowcount != 1:
            connection.rollback()
            raise job_service.JobLeaseLostError(f"字幕子任务已被其他执行收口：{job_id}")
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
    track = ensure_clip_track(task_id, output_clip["id"])
    selected_revision_id = revision_id or track.get("active_revision_id")
    if not selected_revision_id:
        raise ValueError("切片字幕轨没有可渲染的 revision")
    subtitle_path = paths["subtitled_dir"] / (
        f"{Path(output_clip.get('output_file_name') or output_clip['id']).stem}"
        f"_{selected_revision_id[:10]}.ass"
    )
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


class SubtitleRenderCancelled(RuntimeError):
    pass


def _workflow_file_marker(workflow_job_id: str) -> str:
    return hashlib.sha256(workflow_job_id.encode("utf-8")).hexdigest()[:12]


def render_subtitles_for_output_clip(
    task_id: str,
    output_clip_id: str,
    *,
    revision_id: str | None = None,
    workflow_job_id: str | None = None,
    progress_start: int = 5,
    progress_end: int = 95,
) -> dict:
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

    from app.services.subtitle_data_service import ensure_clip_track, get_revision

    style = get_default_subtitle_style()
    track = ensure_clip_track(task_id, output_clip_id)
    selected_revision_id = revision_id or track.get("active_revision_id")
    if not selected_revision_id:
        raise ValueError("切片字幕轨没有可渲染的 revision")
    revision = get_revision(selected_revision_id)
    if revision["track_id"] != track["id"]:
        raise ValueError("待渲染 revision 不属于当前切片字幕轨")
    if revision.get("status") != "approved":
        raise ValueError("只有已审核的字幕 revision 才能烧录")
    paths = get_artifact_paths(task_id)
    paths["subtitled_dir"].mkdir(parents=True, exist_ok=True)
    workflow_marker = _workflow_file_marker(workflow_job_id) if workflow_job_id else ""
    render_token = f"{workflow_marker}_{uuid4().hex[:10]}" if workflow_marker else uuid4().hex[:10]
    output_path = paths["subtitled_dir"] / f"{input_path.stem}_subtitled_{render_token}.mp4"
    temp_owner = workflow_marker or render_token
    temporary_path = output_path.with_name(f".{output_path.stem}.{temp_owner}.part.mp4")

    # === 版本化：创建新的字幕 job，不覆盖旧的 ===
    job = _create_subtitle_job(
        task_id,
        output_clip_id,
        "processing",
        is_active=0,
        revision_id=selected_revision_id,
        workflow_job_id=workflow_job_id,
    )
    append_task_log(task_id, f"开始自动加字幕：{input_path.name}")

    try:
        subtitle_path = _write_ass_file(
            task_id,
            output_clip,
            style,
            revision_id=selected_revision_id,
        )
        source_probe = _probe_media(input_path)
        encoder, audio_mode = _render_with_fallback(
            input_path,
            subtitle_path,
            temporary_path,
            workflow_job_id=workflow_job_id,
            duration_seconds=float(source_probe.get("duration") or 0),
            has_audio=bool(source_probe.get("has_audio")),
            source_audio_codec=str(source_probe.get("audio_codec") or ""),
            progress_start=progress_start,
            progress_end=progress_end,
        )
        validation = _validate_rendered_media(
            temporary_path,
            source_duration=float(source_probe.get("duration") or 0),
            source_has_audio=bool(source_probe.get("has_audio")),
        )
        _finalize_subtitle_job(
            task_id=task_id,
            output_clip_id=output_clip_id,
            revision_id=selected_revision_id,
            subtitle_job_id=job["id"],
            workflow_job_id=workflow_job_id,
            subtitle_path=subtitle_path,
            temporary_path=temporary_path,
            output_path=output_path,
            validation=validation,
            encoder=encoder,
            audio_mode=audio_mode,
        )
    except job_service.JobLeaseLostError:
        temporary_path.unlink(missing_ok=True)
        raise
    except SubtitleRenderCancelled as exc:
        temporary_path.unlink(missing_ok=True)
        _update_subtitle_job_status(
            job["id"],
            "cancelled",
            error_message=str(exc),
            workflow_job_id=workflow_job_id,
        )
        append_task_log(task_id, f"字幕烧录已取消：{input_path.name}")
        raise
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        error = str(exc)
        # 失败时：标记当前 job 为 failed，不激活，旧字幕保持 active
        _update_subtitle_job_status(
            job["id"],
            "failed",
            error_message=error,
            workflow_job_id=workflow_job_id,
        )
        append_task_log(task_id, f"自动加字幕失败：{input_path.name}，原因：{error}")
        raise

    append_task_log(task_id, f"自动加字幕完成：{output_path.name}")

    job = _subtitle_job_for_output(task_id, output_clip_id, active_only=False) or job
    return {
        "status": "ok",
        "message": "自动加字幕完成，已生成带字幕视频。",
        "job": job,
        "output_clip": get_output_clip(task_id, output_clip_id),
        "media_url": f"/media/tasks/{task_id}/subtitled-clips/{output_clip_id}",
    }


def _finalize_subtitle_job(
    *,
    task_id: str,
    output_clip_id: str,
    revision_id: str,
    subtitle_job_id: str,
    workflow_job_id: str | None,
    subtitle_path: Path,
    temporary_path: Path,
    output_path: Path,
    validation: dict[str, Any],
    encoder: str,
    audio_mode: str,
) -> None:
    """在 lease/当前 revision 保护下原子切换最终文件与 active 字幕记录。"""
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    lease = job_service.current_job_lease() if workflow_job_id else None
    if workflow_job_id and (not lease or lease[0] != workflow_job_id):
        raise job_service.JobLeaseLostError(f"字幕 Job 缺少当前执行租约：{workflow_job_id}")
    final_file_created = False
    try:
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = _now_iso()
            if workflow_job_id:
                active_lease = connection.execute(
                    """
                    SELECT 1 FROM workflow_jobs
                    WHERE id = ? AND status = 'running' AND lease_owner = ? AND lease_token = ?
                      AND lease_expires_at > ? AND cancel_requested = 0
                    """,
                    (workflow_job_id, lease[1], lease[2], now),
                ).fetchone()
                if not active_lease:
                    raise job_service.JobLeaseLostError(f"字幕 Job 最终提交前租约已失效：{workflow_job_id}")
            current_revision = connection.execute(
                """
                SELECT sr.status
                FROM subtitle_tracks st
                JOIN subtitle_revisions sr ON sr.id = st.active_revision_id
                WHERE st.task_id = ? AND st.output_clip_id = ? AND st.track_type = 'clip'
                  AND st.is_active = 1 AND st.active_revision_id = ? AND sr.track_id = st.id
                """,
                (task_id, output_clip_id, revision_id),
            ).fetchone()
            if not current_revision or current_revision["status"] != "approved":
                raise ValueError("字幕 revision 已变化，旧渲染结果不会被激活")
            subtitle_job = connection.execute(
                """
                SELECT 1 FROM subtitle_jobs
                WHERE id = ? AND task_id = ? AND output_clip_id = ? AND revision_id = ?
                  AND status = 'processing' AND is_active = 0
                  AND ((? IS NULL AND workflow_job_id IS NULL) OR workflow_job_id = ?)
                """,
                (
                    subtitle_job_id,
                    task_id,
                    output_clip_id,
                    revision_id,
                    workflow_job_id,
                    workflow_job_id,
                ),
            ).fetchone()
            if not subtitle_job:
                raise RuntimeError("字幕子任务已被其他执行收口，拒绝激活旧结果")
            if output_path.exists():
                raise RuntimeError("字幕最终输出路径已存在，拒绝覆盖")
            temporary_path.replace(output_path)
            final_file_created = True
            connection.execute(
                """
                UPDATE subtitle_jobs SET is_active = 0, updated_at = ?
                WHERE task_id = ? AND output_clip_id = ? AND id != ?
                """,
                (now, task_id, output_clip_id, subtitle_job_id),
            )
            cursor = connection.execute(
                """
                UPDATE subtitle_jobs
                SET status = 'completed', subtitle_file_path = ?, output_file_path = ?,
                    error_message = '', validation_status = 'verified', validation_json = ?,
                    encoder = ?, verified_at = ?, updated_at = ?, is_active = 1
                WHERE id = ? AND task_id = ? AND output_clip_id = ? AND revision_id = ?
                  AND status = 'processing' AND is_active = 0
                """,
                (
                    str(subtitle_path),
                    str(output_path),
                    json.dumps({**validation, "audio_mode": audio_mode}, ensure_ascii=False),
                    encoder,
                    now,
                    now,
                    subtitle_job_id,
                    task_id,
                    output_clip_id,
                    revision_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("字幕最终状态提交冲突，拒绝激活旧结果")
            connection.commit()
    except Exception as exc:
        if final_file_created:
            try:
                with get_connection() as verification_connection:
                    persisted = verification_connection.execute(
                        """
                        SELECT 1 FROM subtitle_jobs
                        WHERE id = ? AND status = 'completed' AND validation_status = 'verified'
                          AND is_active = 1 AND output_file_path = ?
                        """,
                        (subtitle_job_id, str(output_path)),
                    ).fetchone()
            except Exception as verification_exc:
                exc.add_note(f"无法确认字幕最终提交是否持久化，已保留文件供恢复：{verification_exc}")
            else:
                if not persisted:
                    try:
                        output_path.unlink(missing_ok=True)
                    except OSError as cleanup_exc:
                        exc.add_note(f"回滚字幕最终文件失败：{cleanup_exc}")
        raise


def _probe_media(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("FFprobe 不可用，无法验证字幕成片")
    try:
        result = subprocess.run(
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
            encoding="utf-8",
            errors="replace",
            timeout=settings.ffprobe_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFprobe 验证字幕视频超时") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "FFprobe 无法读取字幕视频")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("FFprobe 返回了无效 JSON") from exc
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    duration = (payload.get("format") or {}).get("duration") or video.get("duration") or 0
    return {
        "duration": float(duration or 0),
        "video_codec": str(video.get("codec_name") or ""),
        "pixel_format": str(video.get("pix_fmt") or ""),
        "has_audio": bool(audio),
        "audio_codec": str(audio.get("codec_name") or ""),
    }


@lru_cache(maxsize=8)
def _ffmpeg_has_encoder(name: str) -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and name in result.stdout


def _render_with_fallback(
    input_path: Path,
    subtitle_path: Path,
    temporary_path: Path,
    *,
    workflow_job_id: str | None,
    duration_seconds: float,
    has_audio: bool,
    source_audio_codec: str,
    progress_start: int,
    progress_end: int,
) -> tuple[str, str]:
    copy_safe_codecs = {"aac", "mp3", "ac3", "eac3", "alac"}
    preferred_audio = "copy" if not has_audio or source_audio_codec in copy_safe_codecs else "aac"
    attempts: list[tuple[str, str]] = []
    if _ffmpeg_has_encoder("h264_nvenc"):
        attempts.append(("h264_nvenc", preferred_audio))
    attempts.append(("libx264", preferred_audio))
    if preferred_audio == "copy" and has_audio:
        attempts.append(("libx264", "aac"))
    errors = []
    for encoder, audio_mode in attempts:
        temporary_path.unlink(missing_ok=True)
        command = _build_ffmpeg_render_command(
            input_path,
            subtitle_path,
            temporary_path,
            encoder=encoder,
            audio_mode=audio_mode,
        )
        try:
            _run_ffmpeg_progress(
                command,
                workflow_job_id=workflow_job_id,
                duration_seconds=duration_seconds,
                progress_start=progress_start,
                progress_end=progress_end,
            )
            return encoder, audio_mode
        except (SubtitleRenderCancelled, job_service.JobLeaseLostError):
            raise
        except RuntimeError as exc:
            errors.append(f"{encoder}/{audio_mode}：{exc}")
    raise RuntimeError("；".join(errors) or "FFmpeg 字幕烧录失败")


def _build_ffmpeg_render_command(
    input_path: Path,
    subtitle_path: Path,
    output_path: Path,
    *,
    encoder: str,
    audio_mode: str,
) -> list[str]:
    video_args = (
        ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"]
        if encoder == "h264_nvenc"
        else ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
    )
    audio_args = ["-c:a", "copy"] if audio_mode == "copy" else ["-c:a", "aac", "-b:a", "192k"]
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", _ffmpeg_subtitles_filter(subtitle_path),
        *video_args, "-pix_fmt", "yuv420p", *audio_args,
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
        str(output_path),
    ]


def _run_ffmpeg_progress(
    command: list[str],
    *,
    workflow_job_id: str | None,
    duration_seconds: float,
    progress_start: int,
    progress_end: int,
) -> None:
    from app.services import job_service

    process = popen_process_group(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    output_tail: list[str] = []
    assert process.stdout is not None
    try:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if line:
                output_tail.append(line)
                output_tail = output_tail[-40:]
            if workflow_job_id and job_service.is_cancel_requested(workflow_job_id):
                terminate_process_tree(process)
                raise SubtitleRenderCancelled("用户已取消字幕烧录")
            if workflow_job_id and line.startswith("out_time_ms=") and duration_seconds > 0:
                try:
                    processed_seconds = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                ratio = max(0.0, min(1.0, processed_seconds / duration_seconds))
                progress = round(progress_start + (progress_end - progress_start) * ratio)
                job_service.update_job_progress(workflow_job_id, progress, "正在烧录并验证字幕成片")
        return_code = process.wait(timeout=10)
    finally:
        if process.poll() is None:
            terminate_process_tree(process)
    if return_code != 0:
        raise RuntimeError("\n".join(output_tail[-8:]) or f"FFmpeg 退出码 {return_code}")


def _validate_rendered_media(
    path: Path,
    *,
    source_duration: float,
    source_has_audio: bool,
) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        raise RuntimeError("字幕临时输出为空")
    probe = _probe_media(path)
    if probe["video_codec"] != "h264":
        raise RuntimeError(f"字幕成片视频编码不是 H.264：{probe['video_codec'] or '未知'}")
    if probe["pixel_format"] != "yuv420p":
        raise RuntimeError(f"字幕成片像素格式不是 yuv420p：{probe['pixel_format'] or '未知'}")
    if source_has_audio and not probe["has_audio"]:
        raise RuntimeError("原切片包含音频，但字幕成片缺少音轨")
    tolerance = max(1.5, source_duration * 0.03)
    if source_duration > 0 and abs(probe["duration"] - source_duration) > tolerance:
        raise RuntimeError("字幕成片时长与原切片不一致")
    return {
        "video_codec": probe["video_codec"],
        "pixel_format": probe["pixel_format"],
        "has_audio": probe["has_audio"],
        "audio_codec": probe["audio_codec"],
        "duration_seconds": probe["duration"],
        "size_bytes": path.stat().st_size,
    }
