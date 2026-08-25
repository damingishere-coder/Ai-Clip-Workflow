"""不可变字幕轨、revision、cue、导入导出与长音频波形服务。"""

from __future__ import annotations

from array import array
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable
from uuid import uuid4

import pysubs2

from app.core.config import settings
from app.db.database import get_connection
from app.services.ai.ai_clip_analyzer import _extract_transcript_rows, _read_transcript
from app.services.storage_service import (
    get_artifact_paths,
    get_source_video_path,
    resolve_video_file_path,
)
from app.services.transcription_checkpoint_service import fingerprint_file


MAX_CUES_PER_REVISION = 20_000
MAX_RANGE_LIMIT = 2_000
DEFAULT_RANGE_LIMIT = 500
QUALITY_MAX_LINES = 2
QUALITY_MAX_CHINESE_CHARS_PER_LINE = 18
QUALITY_MIN_DURATION_MS = 800
QUALITY_MAX_DURATION_MS = 7_000
QUALITY_MIN_GAP_MS = 80
QUALITY_MAX_CHINESE_CHARS_PER_SECOND = 12


class SubtitleRevisionConflict(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_task_tracks(task_id: str, *, ensure: bool = True) -> list[dict[str, Any]]:
    if ensure:
        ensure_source_track(task_id)
        with get_connection() as connection:
            output_rows = connection.execute(
                """
                SELECT id FROM output_clip
                WHERE task_id = ? AND is_active = 1 AND status = 'completed'
                ORDER BY created_at ASC
                """,
                (task_id,),
            ).fetchall()
        for row in output_rows:
            ensure_clip_track(task_id, row["id"])

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT st.*, sr.revision_number, sr.origin AS revision_origin,
                   sr.status AS revision_status, sr.cue_count,
                   oc.output_file_name, oc.source_start_ms, oc.source_end_ms,
                   oc.source_duration_ms, oc.snapshot_source
            FROM subtitle_tracks st
            LEFT JOIN subtitle_revisions sr ON sr.id = st.active_revision_id
            LEFT JOIN output_clip oc ON oc.id = st.output_clip_id
            WHERE st.task_id = ? AND st.is_active = 1
            ORDER BY CASE st.track_type WHEN 'source' THEN 0 ELSE 1 END,
                     oc.source_start_ms, st.created_at
            """,
            (task_id,),
        ).fetchall()
    return [_track_to_dict(dict(row)) for row in rows]


def get_track(track_id: str) -> dict[str, Any]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT st.*, sr.revision_number, sr.origin AS revision_origin,
                   sr.status AS revision_status, sr.cue_count,
                   oc.output_file_name, oc.source_start_ms, oc.source_end_ms,
                   oc.source_duration_ms, oc.snapshot_source
            FROM subtitle_tracks st
            LEFT JOIN subtitle_revisions sr ON sr.id = st.active_revision_id
            LEFT JOIN output_clip oc ON oc.id = st.output_clip_id
            WHERE st.id = ?
            """,
            (track_id,),
        ).fetchone()
    if not row:
        raise ValueError("字幕轨不存在")
    return _track_to_dict(dict(row))


def ensure_source_track(task_id: str, *, force: bool = False) -> dict[str, Any]:
    cues, source_fingerprint, origin = _load_source_cues(task_id)
    if not cues:
        raise ValueError("当前任务没有可用的结构化转写或时间戳 Markdown")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone():
            connection.rollback()
            raise ValueError("任务不存在")
        existing = connection.execute(
            """
            SELECT * FROM subtitle_tracks
            WHERE task_id = ? AND track_type = 'source' AND is_active = 1
            ORDER BY created_at DESC LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if (
            existing
            and existing["source_fingerprint"] == source_fingerprint
            and existing["active_revision_id"]
        ):
            track_id = str(existing["id"])
            connection.commit()
            return get_track(track_id)
        if existing and existing["has_manual_edits"] and not force:
            connection.execute(
                "UPDATE subtitle_tracks SET sync_status = 'pending_source_refresh', updated_at = ? WHERE id = ?",
                (_now_iso(), existing["id"]),
            )
            connection.commit()
            return get_track(str(existing["id"]))

        now = _now_iso()
        track_id = str(existing["id"]) if existing else uuid4().hex
        base_revision_id = str(existing["active_revision_id"] or "") if existing else ""
        if not existing:
            connection.execute(
                """
                INSERT INTO subtitle_tracks (
                    id, task_id, track_type, output_clip_id, name, language,
                    source_fingerprint, sync_status, has_manual_edits,
                    is_active, created_at, updated_at
                ) VALUES (?, ?, 'source', NULL, '原片主字幕', 'zh-CN', ?,
                          'up_to_date', 0, 1, ?, ?)
                """,
                (track_id, task_id, source_fingerprint, now, now),
            )
        revision = _insert_revision_with_connection(
            connection,
            track_id,
            cues,
            origin=origin,
            parent_revision_id=base_revision_id or None,
            status="draft",
            note="从结构化转写生成原片主时间轴" if origin == "asr" else "从旧版 Markdown 兼容生成",
            activate=False,
        )
        cursor = connection.execute(
            """
            UPDATE subtitle_tracks
            SET source_fingerprint = ?, active_revision_id = ?, sync_status = 'up_to_date',
                has_manual_edits = 0, updated_at = ?
            WHERE id = ? AND is_active = 1
              AND ((active_revision_id = ?) OR (active_revision_id IS NULL AND ? = ''))
            """,
            (
                source_fingerprint,
                revision["id"],
                now,
                track_id,
                base_revision_id,
                base_revision_id,
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise SubtitleRevisionConflict("原片字幕已产生新版本，请刷新后重试")
        connection.commit()

    with get_connection() as connection:
        clip_tracks = connection.execute(
            "SELECT id FROM subtitle_tracks WHERE task_id = ? AND track_type = 'clip' AND is_active = 1",
            (task_id,),
        ).fetchall()
    for clip_track in clip_tracks:
        sync_clip_track(clip_track["id"], force=False)
    return get_track(track_id)


def ensure_clip_track(task_id: str, output_clip_id: str) -> dict[str, Any]:
    source_track = ensure_source_track(task_id)
    output = ensure_output_clip_snapshot(task_id, output_clip_id)
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT * FROM subtitle_tracks
            WHERE task_id = ? AND track_type = 'clip' AND output_clip_id = ? AND is_active = 1
            """,
            (task_id, output_clip_id),
        ).fetchone()
        if not existing:
            now = _now_iso()
            track_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO subtitle_tracks (
                    id, task_id, track_type, output_clip_id, name, language,
                    source_track_id, source_revision_id, source_fingerprint,
                    sync_status, has_manual_edits, is_active, created_at, updated_at
                ) VALUES (?, ?, 'clip', ?, ?, 'zh-CN', ?, NULL, ?,
                          'pending_sync', 0, 1, ?, ?)
                """,
                (
                    track_id,
                    task_id,
                    output_clip_id,
                    output.get("output_file_name") or "切片字幕",
                    source_track["id"],
                    source_track.get("source_fingerprint") or "",
                    now,
                    now,
                ),
            )
        else:
            track_id = existing["id"]
        connection.commit()
    sync_clip_track(track_id, force=False)
    return get_track(track_id)


def sync_clip_track(track_id: str, *, force: bool = False) -> dict[str, Any]:
    track_hint = get_track(track_id)
    if track_hint["track_type"] != "clip":
        raise ValueError("只有切片字幕轨可以从原片同步")
    ensure_output_clip_snapshot(track_hint["task_id"], track_hint["output_clip_id"])
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        track_row = connection.execute(
            "SELECT * FROM subtitle_tracks WHERE id = ? AND is_active = 1",
            (track_id,),
        ).fetchone()
        if not track_row:
            connection.rollback()
            raise ValueError("字幕轨不存在或已停用")
        track = dict(track_row)
        if track["track_type"] != "clip":
            connection.rollback()
            raise ValueError("只有切片字幕轨可以从原片同步")
        source_track_row = connection.execute(
            "SELECT * FROM subtitle_tracks WHERE id = ? AND track_type = 'source' AND is_active = 1",
            (track["source_track_id"],),
        ).fetchone()
        if not source_track_row or not source_track_row["active_revision_id"]:
            connection.rollback()
            raise ValueError("原片字幕还没有可同步 revision")
        source_track = dict(source_track_row)
        source_revision_id = str(source_track["active_revision_id"])
        if track.get("has_manual_edits") and not force:
            if track.get("source_revision_id") != source_revision_id:
                connection.execute(
                    "UPDATE subtitle_tracks SET sync_status = 'pending_sync', updated_at = ? WHERE id = ?",
                    (_now_iso(), track_id),
                )
            connection.commit()
            return get_track(track_id)
        if track.get("source_revision_id") == source_revision_id and track.get("active_revision_id"):
            connection.commit()
            return get_track(track_id)

        output = connection.execute(
            """
            SELECT source_start_ms, source_end_ms
            FROM output_clip WHERE id = ? AND task_id = ?
            """,
            (track["output_clip_id"], track["task_id"]),
        ).fetchone()
        if not output or output["source_start_ms"] is None or output["source_end_ms"] is None:
            connection.rollback()
            raise ValueError("切片记录缺少原片时间范围")
        source_cues = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM subtitle_cues WHERE revision_id = ? ORDER BY cue_index ASC",
                (source_revision_id,),
            ).fetchall()
        ]
        local_cues = inherit_cues_for_clip(
            source_cues,
            int(output["source_start_ms"]),
            int(output["source_end_ms"]),
        )
        base_revision_id = str(track.get("active_revision_id") or "")
        revision = _insert_revision_with_connection(
            connection,
            track_id,
            local_cues,
            origin="source_sync",
            parent_revision_id=base_revision_id or None,
            status="draft",
            note=f"继承原片 revision {source_revision_id}",
            activate=False,
        )
        cursor = connection.execute(
            """
            UPDATE subtitle_tracks
            SET source_revision_id = ?, source_fingerprint = ?, active_revision_id = ?,
                sync_status = 'up_to_date', has_manual_edits = 0, updated_at = ?
            WHERE id = ? AND is_active = 1
              AND ((active_revision_id = ?) OR (active_revision_id IS NULL AND ? = ''))
            """,
            (
                source_revision_id,
                source_track.get("source_fingerprint") or "",
                revision["id"],
                _now_iso(),
                track_id,
                base_revision_id,
                base_revision_id,
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise SubtitleRevisionConflict("切片字幕已产生新版本，请刷新后重试同步")
        connection.commit()
    return get_track(track_id)


def ensure_output_clip_snapshot(task_id: str, output_clip_id: str) -> dict[str, Any]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT oc.*, cc.start_time, cc.end_time
            FROM output_clip oc
            LEFT JOIN clip_candidates cc ON cc.id = oc.clip_candidate_id
            WHERE oc.id = ? AND oc.task_id = ?
            """,
            (output_clip_id, task_id),
        ).fetchone()
    if not row:
        raise ValueError("切片记录不存在")
    output = dict(row)
    if output.get("source_start_ms") is not None and output.get("source_end_ms") is not None:
        return output
    if not output.get("start_time") or not output.get("end_time"):
        raise ValueError("旧切片缺少可推断的原片边界，请重新生成切片")
    from app.services.task_service import _parse_time_to_seconds

    start_ms = round(_parse_time_to_seconds(output["start_time"]) * 1000)
    end_ms = round(_parse_time_to_seconds(output["end_time"]) * 1000)
    if end_ms <= start_ms:
        raise ValueError("旧切片的原片边界无效，请重新生成切片")
    task = _get_task_row(task_id)
    source_path = get_source_video_path(task)
    source_fingerprint = fingerprint_file(source_path) if source_path and source_path.exists() else ""
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE output_clip
            SET source_start_ms = ?, source_end_ms = ?, source_duration_ms = ?,
                source_fingerprint = ?, snapshot_source = 'legacy_inferred', updated_at = ?
            WHERE id = ?
            """,
            (start_ms, end_ms, end_ms - start_ms, source_fingerprint, _now_iso(), output_clip_id),
        )
        connection.commit()
    return ensure_output_clip_snapshot(task_id, output_clip_id)


def inherit_cues_for_clip(
    source_cues: Iterable[dict[str, Any]],
    source_start_ms: int,
    source_end_ms: int,
) -> list[dict[str, Any]]:
    inherited = []
    for cue in source_cues:
        cue_start = int(cue["start_ms"])
        cue_end = int(cue["end_ms"])
        if cue_end <= source_start_ms or cue_start >= source_end_ms:
            continue
        local_start = max(0, cue_start - source_start_ms)
        local_end = min(source_end_ms, cue_end) - source_start_ms
        if local_end <= local_start:
            continue
        inherited.append(
            {
                "start_ms": local_start,
                "end_ms": local_end,
                "text": cue["text"],
                "confidence": cue.get("confidence"),
                "speaker": cue.get("speaker") or "",
                "source_cue_id": cue.get("id") or cue.get("source_cue_id"),
            }
        )
    return inherited


def create_manual_revision(
    track_id: str,
    *,
    base_revision_id: str | None,
    cues: Iterable[Any],
    note: str = "",
) -> dict[str, Any]:
    normalized = [_cue_input_to_dict(cue) for cue in cues]
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        track_row = connection.execute(
            "SELECT * FROM subtitle_tracks WHERE id = ? AND is_active = 1",
            (track_id,),
        ).fetchone()
        if not track_row:
            connection.rollback()
            raise ValueError("字幕轨不存在或已停用")
        track = dict(track_row)
        if (track.get("active_revision_id") or None) != (base_revision_id or None):
            connection.rollback()
            raise SubtitleRevisionConflict("字幕已产生新版本，请刷新后再保存，当前编辑没有覆盖新版本")
        revision = _insert_revision_with_connection(
            connection,
            track_id,
            normalized,
            origin="manual",
            parent_revision_id=base_revision_id,
            status="draft",
            note=note or "字幕编辑器自动保存",
            activate=False,
        )
        cursor = connection.execute(
            """
            UPDATE subtitle_tracks
            SET active_revision_id = ?, has_manual_edits = 1,
                sync_status = CASE WHEN track_type = 'clip' THEN 'manual' ELSE sync_status END,
                updated_at = ?
            WHERE id = ? AND is_active = 1
              AND ((active_revision_id = ?) OR (active_revision_id IS NULL AND ? IS NULL))
            """,
            (revision["id"], _now_iso(), track_id, base_revision_id, base_revision_id),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise SubtitleRevisionConflict("字幕已产生新版本，请刷新后再保存，当前编辑没有覆盖新版本")
        connection.commit()
    if track["track_type"] == "source":
        _sync_dependent_clip_tracks(track["task_id"])
    return get_revision(revision["id"], include_cues=True)


def apply_revision_operations(
    track_id: str,
    *,
    base_revision_id: str,
    operations: Iterable[Any],
    note: str = "",
) -> dict[str, Any]:
    cues = _fetch_all_revision_cues(base_revision_id)
    for operation_value in operations:
        operation = operation_value.model_dump() if hasattr(operation_value, "model_dump") else dict(operation_value)
        cues = _apply_operation(cues, operation)
    return create_manual_revision(
        track_id,
        base_revision_id=base_revision_id,
        cues=cues,
        note=note or "字幕批量编辑",
    )


def get_revision(revision_id: str, *, include_cues: bool = False) -> dict[str, Any]:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM subtitle_revisions WHERE id = ?",
            (revision_id,),
        ).fetchone()
    if not row:
        raise ValueError("字幕 revision 不存在")
    revision = dict(row)
    if include_cues:
        revision["cues"] = _fetch_all_revision_cues(revision_id)
        revision["quality"] = evaluate_subtitle_quality(revision["cues"])
    return revision


def list_revisions(track_id: str) -> list[dict[str, Any]]:
    get_track(track_id)
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM subtitle_revisions WHERE track_id = ? ORDER BY revision_number DESC",
            (track_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_revision_cues(
    track_id: str,
    *,
    revision_id: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    offset: int = 0,
    limit: int = DEFAULT_RANGE_LIMIT,
) -> dict[str, Any]:
    track = get_track(track_id)
    revision_id = revision_id or track.get("active_revision_id")
    if not revision_id:
        return {"track": track, "revision": None, "cues": [], "total": 0, "quality": {"issues": []}}
    revision = get_revision(revision_id)
    if revision["track_id"] != track_id:
        raise ValueError("revision 不属于当前字幕轨")
    clauses = ["revision_id = ?"]
    params: list[Any] = [revision_id]
    if start_ms is not None:
        clauses.append("end_ms > ?")
        params.append(max(0, start_ms))
    if end_ms is not None:
        clauses.append("start_ms < ?")
        params.append(max(0, end_ms))
    safe_limit = max(1, min(MAX_RANGE_LIMIT, int(limit)))
    safe_offset = max(0, int(offset))
    where = " AND ".join(clauses)
    with get_connection() as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM subtitle_cues WHERE {where}",
            params,
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT * FROM subtitle_cues WHERE {where}
            ORDER BY cue_index ASC LIMIT ? OFFSET ?
            """,
            [*params, safe_limit, safe_offset],
        ).fetchall()
    cues = [dict(row) for row in rows]
    return {
        "track": track,
        "revision": revision,
        "cues": cues,
        "total": total,
        "offset": safe_offset,
        "limit": safe_limit,
        "quality": evaluate_subtitle_quality(cues),
    }


def approve_revision(track_id: str, revision_id: str) -> dict[str, Any]:
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        approve_revisions_with_connection(
            connection,
            [(track_id, revision_id)],
            require_non_empty=False,
        )
        connection.commit()
    return get_revision(revision_id, include_cues=True)


def approve_revisions_with_connection(
    connection,
    approvals: Iterable[tuple[str, str]],
    *,
    require_non_empty: bool = True,
) -> list[dict[str, Any]]:
    """在调用方事务内重新校验并批准一组当前 active revision。"""
    now = _now_iso()
    approved: list[dict[str, Any]] = []
    for track_id, revision_id in approvals:
        track = connection.execute(
            "SELECT id, active_revision_id FROM subtitle_tracks WHERE id = ? AND is_active = 1",
            (track_id,),
        ).fetchone()
        if not track:
            raise ValueError("字幕轨不存在或已停用")
        if str(track["active_revision_id"] or "") != revision_id:
            raise SubtitleRevisionConflict("字幕已产生新版本，请刷新后重新审核")

        revision = connection.execute(
            "SELECT id, track_id, status, cue_count FROM subtitle_revisions WHERE id = ?",
            (revision_id,),
        ).fetchone()
        if not revision:
            raise ValueError("字幕 revision 不存在")
        if revision["track_id"] != track_id:
            raise ValueError("revision 不属于当前字幕轨")
        if require_non_empty and int(revision["cue_count"] or 0) <= 0:
            raise ValueError("当前字幕没有可审核内容")

        cues = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM subtitle_cues WHERE revision_id = ? ORDER BY cue_index ASC",
                (revision_id,),
            ).fetchall()
        ]
        if int(evaluate_subtitle_quality(cues).get("error_count") or 0) > 0:
            raise ValueError("当前字幕仍有时间重叠错误，请修正后再审核")

        cursor = connection.execute(
            """
            UPDATE subtitle_revisions
            SET status = 'approved', approved_at = COALESCE(approved_at, ?)
            WHERE id = ? AND track_id = ?
            """,
            (now, revision_id, track_id),
        )
        if cursor.rowcount != 1:
            raise SubtitleRevisionConflict("字幕 revision 已变化，请刷新后重新审核")
        cursor = connection.execute(
            """
            UPDATE subtitle_tracks SET active_revision_id = ?, updated_at = ?
            WHERE id = ? AND active_revision_id = ? AND is_active = 1
            """,
            (revision_id, now, track_id, revision_id),
        )
        if cursor.rowcount != 1:
            raise SubtitleRevisionConflict("字幕已产生新版本，请刷新后重新审核")
        approved.append(
            {
                "id": revision_id,
                "track_id": track_id,
                "status": "approved",
                "cue_count": int(revision["cue_count"] or 0),
            }
        )
    return approved


def create_suggestion_revision(
    track_id: str,
    *,
    base_revision_id: str,
    suggested_text_by_cue_id: dict[str, str],
    note: str = "AI 字幕纠错建议",
) -> dict[str, Any]:
    """保存非 active 建议版本；服务端只接受文字变化。"""
    track = get_track(track_id)
    if track.get("active_revision_id") != base_revision_id:
        raise SubtitleRevisionConflict("字幕已产生新版本，请刷新后重新生成 AI 建议")
    base_revision = get_revision(base_revision_id)
    if base_revision["track_id"] != track_id:
        raise ValueError("基础 revision 不属于当前字幕轨")
    base_cues = _fetch_all_revision_cues(base_revision_id)
    known_ids = {str(cue["id"]) for cue in base_cues}
    unknown = set(suggested_text_by_cue_id) - known_ids
    if unknown:
        raise ValueError("AI 建议包含不属于当前 revision 的 cue")
    suggestion_cues = []
    for cue in base_cues:
        cue_id = str(cue["id"])
        text = str(suggested_text_by_cue_id.get(cue_id, cue["text"]) or "").strip()
        if not text:
            raise ValueError("AI 建议不能把字幕文字清空")
        suggestion_cues.append(
            {
                "start_ms": int(cue["start_ms"]),
                "end_ms": int(cue["end_ms"]),
                "text": text,
                "confidence": cue.get("confidence"),
                "speaker": cue.get("speaker") or "",
                "source_cue_id": cue_id,
            }
        )
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        revision = _insert_revision_with_connection(
            connection,
            track_id,
            suggestion_cues,
            origin="ai_suggestion",
            parent_revision_id=base_revision_id,
            status="suggested",
            note=note,
            activate=False,
        )
        connection.commit()
    return get_revision(revision["id"], include_cues=True)


def get_suggestion_diff(track_id: str, suggestion_revision_id: str) -> list[dict[str, Any]]:
    suggestion = get_revision(suggestion_revision_id, include_cues=True)
    if suggestion["track_id"] != track_id or suggestion.get("origin") != "ai_suggestion":
        raise ValueError("AI 建议 revision 不属于当前字幕轨")
    base_revision_id = str(suggestion.get("parent_revision_id") or "")
    if not base_revision_id:
        raise ValueError("AI 建议缺少基础 revision")
    base_cues = {str(cue["id"]): cue for cue in _fetch_all_revision_cues(base_revision_id)}
    changes = []
    for cue in suggestion["cues"]:
        source_id = str(cue.get("source_cue_id") or "")
        base = base_cues.get(source_id)
        if not base or str(base.get("text") or "") == str(cue.get("text") or ""):
            continue
        changes.append(
            {
                "cue_id": source_id,
                "start_ms": int(base["start_ms"]),
                "end_ms": int(base["end_ms"]),
                "original_text": str(base.get("text") or ""),
                "suggested_text": str(cue.get("text") or ""),
            }
        )
    return changes


def accept_suggestion_revision(
    track_id: str,
    *,
    suggestion_revision_id: str,
    base_revision_id: str,
    cue_ids: Iterable[str],
) -> dict[str, Any]:
    """选择接受 AI 文字建议，并生成新的人工草稿 revision。"""
    track = get_track(track_id)
    if track.get("active_revision_id") != base_revision_id:
        raise SubtitleRevisionConflict("字幕已产生新版本，请刷新后再接受 AI 建议")
    suggestion = get_revision(suggestion_revision_id, include_cues=True)
    if (
        suggestion["track_id"] != track_id
        or suggestion.get("origin") != "ai_suggestion"
        or suggestion.get("parent_revision_id") != base_revision_id
    ):
        raise ValueError("AI 建议与当前字幕版本不匹配")
    selected = {str(value) for value in cue_ids if str(value)}
    if not selected:
        raise ValueError("请至少选择一条 AI 建议")
    suggested_text = {
        str(cue.get("source_cue_id") or ""): str(cue.get("text") or "")
        for cue in suggestion["cues"]
    }
    base_cues = _fetch_all_revision_cues(base_revision_id)
    known_ids = {str(cue["id"]) for cue in base_cues}
    if not selected <= known_ids:
        raise ValueError("选中的 AI 建议不属于当前字幕版本")
    merged = []
    for cue in base_cues:
        cue_id = str(cue["id"])
        merged.append(
            {
                **cue,
                "text": suggested_text.get(cue_id, cue["text"]) if cue_id in selected else cue["text"],
            }
        )
    return create_manual_revision(
        track_id,
        base_revision_id=base_revision_id,
        cues=merged,
        note=f"接受 {len(selected)} 条 AI 字幕建议",
    )


def import_subtitle_text(
    track_id: str,
    *,
    content: str,
    format_name: str,
    note: str = "",
) -> dict[str, Any]:
    format_name = _validate_format(format_name)
    try:
        document = pysubs2.SSAFile.from_string(content, format_=format_name)
    except Exception as exc:
        raise ValueError(f"字幕文件解析失败：{exc}") from exc
    cues = [
        {
            "start_ms": int(event.start),
            "end_ms": int(event.end),
            "text": event.plaintext.strip(),
            "speaker": event.name or "",
            "confidence": None,
            "source_cue_id": None,
        }
        for event in document.events
        if event.type == "Dialogue" and event.end > event.start and event.plaintext.strip()
    ]
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        track_row = connection.execute(
            "SELECT * FROM subtitle_tracks WHERE id = ? AND is_active = 1",
            (track_id,),
        ).fetchone()
        if not track_row:
            connection.rollback()
            raise ValueError("字幕轨不存在或已停用")
        track = dict(track_row)
        base_revision_id = str(track.get("active_revision_id") or "")
        revision = _insert_revision_with_connection(
            connection,
            track_id,
            cues,
            origin="import",
            parent_revision_id=base_revision_id or None,
            status="draft",
            note=note or f"导入 {format_name.upper()} 字幕",
            activate=False,
        )
        cursor = connection.execute(
            """
            UPDATE subtitle_tracks
            SET active_revision_id = ?, has_manual_edits = 1,
                sync_status = 'manual', updated_at = ?
            WHERE id = ? AND is_active = 1
              AND ((active_revision_id = ?) OR (active_revision_id IS NULL AND ? = ''))
            """,
            (revision["id"], _now_iso(), track_id, base_revision_id, base_revision_id),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise SubtitleRevisionConflict("字幕已产生新版本，请刷新后重新导入")
        connection.commit()
    if track["track_type"] == "source":
        _sync_dependent_clip_tracks(track["task_id"])
    return get_revision(revision["id"], include_cues=True)


def export_subtitle_text(
    track_id: str,
    *,
    revision_id: str | None = None,
    format_name: str,
) -> tuple[str, str, str]:
    format_name = _validate_format(format_name)
    track = get_track(track_id)
    revision_id = revision_id or track.get("active_revision_id")
    if not revision_id:
        raise ValueError("当前字幕轨没有可导出的 revision")
    revision = get_revision(revision_id)
    if revision["track_id"] != track_id:
        raise ValueError("revision 不属于当前字幕轨")
    document = _build_pysubs2_document(track, revision_id, dynamic_ass=format_name == "ass")
    content = document.to_string(format_name)
    media_type = {
        "srt": "application/x-subrip",
        "vtt": "text/vtt",
        "ass": "text/x-ssa",
    }[format_name]
    return content, media_type, f"{_safe_file_stem(track['name'])}.{format_name}"


def serialize_revision_to_ass(track_id: str, revision_id: str) -> str:
    track = get_track(track_id)
    revision = get_revision(revision_id)
    if revision["track_id"] != track_id:
        raise ValueError("revision 不属于当前字幕轨")
    return _build_pysubs2_document(track, revision_id, dynamic_ass=True).to_string("ass")


def evaluate_subtitle_quality(cues: Iterable[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted((dict(cue) for cue in cues), key=lambda cue: (int(cue["start_ms"]), int(cue["end_ms"])))
    issues: list[dict[str, Any]] = []
    previous = None
    for cue in ordered:
        cue_id = cue.get("id") or ""
        text = str(cue.get("text") or "")
        duration = int(cue["end_ms"]) - int(cue["start_ms"])
        lines = text.splitlines() or [text]
        if len(lines) > QUALITY_MAX_LINES:
            issues.append(_issue(cue_id, "too_many_lines", "warning", "建议最多 2 行"))
        if any(_chinese_char_count(line) > QUALITY_MAX_CHINESE_CHARS_PER_LINE for line in lines):
            issues.append(_issue(cue_id, "line_too_long", "warning", "单行建议不超过 18 个中文字符"))
        if duration < QUALITY_MIN_DURATION_MS:
            issues.append(_issue(cue_id, "too_short", "warning", "字幕显示时间短于 800ms"))
        if duration > QUALITY_MAX_DURATION_MS:
            issues.append(_issue(cue_id, "too_long", "warning", "字幕显示时间长于 7 秒"))
        chars_per_second = _chinese_char_count(text) / max(0.001, duration / 1000)
        if chars_per_second > QUALITY_MAX_CHINESE_CHARS_PER_SECOND:
            issues.append(_issue(cue_id, "reading_speed", "warning", "中文阅读速度过快"))
        if previous:
            gap = int(cue["start_ms"]) - int(previous["end_ms"])
            if gap < 0:
                issues.append(_issue(cue_id, "overlap", "error", "字幕时间与上一条重叠"))
            elif gap < QUALITY_MIN_GAP_MS:
                issues.append(_issue(cue_id, "small_gap", "warning", "与上一条间隔小于 80ms"))
        previous = cue
    return {
        "issues": issues,
        "error_count": sum(1 for item in issues if item["severity"] == "error"),
        "warning_count": sum(1 for item in issues if item["severity"] == "warning"),
    }


def get_waveform_peaks(track_id: str, *, max_points: int = 12_000) -> dict[str, Any]:
    track = get_track(track_id)
    max_points = max(1_000, min(50_000, int(max_points)))
    media_path = _track_media_path(track)
    if not media_path or not media_path.exists():
        raise ValueError("字幕轨对应的媒体文件不存在")
    fingerprint = fingerprint_file(media_path)
    cache_dir = get_artifact_paths(track["task_id"])["transcript_path"].parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"waveform_{track['track_type']}_{fingerprint[:12]}_{max_points}.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
        if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint:
            return {**cached, "cached": True}
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg 不可用，无法预计算波形 peaks")
    command = [
        "ffmpeg", "-v", "error", "-i", str(media_path), "-map", "0:a:0",
        "-ac", "1", "-ar", "100", "-f", "s16le", "pipe:1",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=max(900, settings.ffmpeg_audio_extract_timeout),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("波形预计算超时") from exc
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(error or "波形预计算失败")
    samples = array("h")
    samples.frombytes(result.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    peaks = _downsample_peaks(samples, max_points)
    duration_ms = round(len(samples) / 100 * 1000)
    payload = {
        "track_id": track_id,
        "fingerprint": fingerprint,
        "duration_ms": duration_ms,
        "sample_rate": 100,
        "point_count": len(peaks),
        "peaks": peaks,
        "cached": False,
    }
    temp_path = cache_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temp_path.replace(cache_path)
    return payload


def _insert_revision_with_connection(
    connection,
    track_id: str,
    cues: Iterable[dict[str, Any]],
    *,
    origin: str,
    parent_revision_id: str | None,
    status: str,
    note: str,
    activate: bool,
) -> dict[str, Any]:
    normalized = _normalize_cues(cues)
    if len(normalized) > MAX_CUES_PER_REVISION:
        raise ValueError(f"单个字幕 revision 最多 {MAX_CUES_PER_REVISION} 条")
    revision_number = connection.execute(
        "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM subtitle_revisions WHERE track_id = ?",
        (track_id,),
    ).fetchone()[0]
    revision_id = uuid4().hex
    now = _now_iso()
    checksum = _cue_checksum(normalized)
    connection.execute(
        """
        INSERT INTO subtitle_revisions (
            id, track_id, revision_number, origin, parent_revision_id,
            status, note, cue_count, checksum, created_at, approved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            revision_id,
            track_id,
            revision_number,
            origin,
            parent_revision_id,
            status,
            note,
            len(normalized),
            checksum,
            now,
        ),
    )
    for index, cue in enumerate(normalized):
        connection.execute(
            """
            INSERT INTO subtitle_cues (
                id, revision_id, cue_index, start_ms, end_ms, text,
                confidence, speaker, source_cue_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                revision_id,
                index,
                cue["start_ms"],
                cue["end_ms"],
                cue["text"],
                cue.get("confidence"),
                cue.get("speaker") or "",
                cue.get("source_cue_id"),
                now,
            ),
        )
    if activate:
        connection.execute(
            "UPDATE subtitle_tracks SET active_revision_id = ?, updated_at = ? WHERE id = ?",
            (revision_id, now, track_id),
        )
    return {
        "id": revision_id,
        "track_id": track_id,
        "revision_number": revision_number,
        "origin": origin,
        "status": status,
        "cue_count": len(normalized),
        "checksum": checksum,
    }


def _load_source_cues(task_id: str) -> tuple[list[dict[str, Any]], str, str]:
    with get_connection() as connection:
        run = connection.execute(
            """
            SELECT * FROM transcription_runs
            WHERE task_id = ? AND is_active = 1
            ORDER BY updated_at DESC LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        chunks = []
        if run:
            chunks = connection.execute(
                """
                SELECT * FROM transcription_chunks
                WHERE run_id = ? AND status = 'completed'
                ORDER BY chunk_index ASC
                """,
                (run["id"],),
            ).fetchall()
    if run and chunks:
        cues: list[dict[str, Any]] = []
        checksum_parts = []
        overlap_ms = int(run["overlap_seconds"] or 0) * 1000
        for chunk in chunks:
            raw = str(chunk["result_json"] or "")
            checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if not raw or checksum != str(chunk["result_checksum"] or ""):
                continue
            checksum_parts.append(checksum)
            try:
                segments = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for segment in segments if isinstance(segments, list) else []:
                if not isinstance(segment, dict):
                    continue
                start_ms = int(chunk["start_ms"]) + round(float(segment.get("start_seconds") or 0) * 1000)
                end_ms = int(chunk["start_ms"]) + round(float(segment.get("end_seconds") or 0) * 1000)
                if int(chunk["chunk_index"]) > 1 and start_ms < int(chunk["start_ms"]) + overlap_ms:
                    continue
                text = " ".join(str(segment.get("text") or "").split())
                if text and end_ms > start_ms:
                    cues.append(
                        {
                            "start_ms": start_ms,
                            "end_ms": end_ms,
                            "text": text,
                            "confidence": segment.get("confidence"),
                            "speaker": "",
                            "source_cue_id": None,
                        }
                    )
        if cues:
            fingerprint = hashlib.sha256(
                f"{run['source_fingerprint']}|{'|'.join(checksum_parts)}".encode("utf-8")
            ).hexdigest()
            return cues, fingerprint, "asr"

    transcript_path = get_artifact_paths(task_id)["transcript_path"]
    if not transcript_path.exists():
        return [], "", "markdown"
    transcript_text = _read_transcript(transcript_path)
    rows = _extract_transcript_rows(transcript_text)
    cues = [
        {
            "start_ms": row.start_seconds * 1000,
            "end_ms": row.end_seconds * 1000,
            "text": row.text,
            "confidence": None,
            "speaker": "",
            "source_cue_id": None,
        }
        for row in rows
        if row.end_seconds > row.start_seconds
    ]
    return cues, hashlib.sha256(transcript_text.encode("utf-8")).hexdigest(), "markdown"


def _apply_operation(cues: list[dict[str, Any]], operation: dict[str, Any]) -> list[dict[str, Any]]:
    operation_type = operation["type"]
    items = [dict(cue) for cue in cues]
    by_id = {str(cue.get("id") or ""): index for index, cue in enumerate(items)}
    cue_id = str(operation.get("cue_id") or "")
    if operation_type == "update":
        if cue_id not in by_id:
            raise ValueError("要更新的字幕行不存在")
        cue = items[by_id[cue_id]]
        for field in ("start_ms", "end_ms", "text", "speaker", "confidence"):
            if operation.get(field) is not None:
                cue[field] = operation[field]
    elif operation_type == "split":
        if cue_id not in by_id:
            raise ValueError("要拆分的字幕行不存在")
        index = by_id[cue_id]
        cue = items[index]
        split_ms = int(operation.get("split_ms") or 0)
        if not int(cue["start_ms"]) < split_ms < int(cue["end_ms"]):
            raise ValueError("拆分点必须位于字幕时间范围内")
        first_text = str(operation.get("text") or cue["text"]).strip()
        second_text = str(operation.get("second_text") or cue["text"]).strip()
        first = {**cue, "end_ms": split_ms, "text": first_text}
        second = {**cue, "id": None, "start_ms": split_ms, "text": second_text}
        items[index : index + 1] = [first, second]
    elif operation_type == "merge":
        selected_ids = set(operation.get("cue_ids") or [])
        selected = [cue for cue in items if cue.get("id") in selected_ids]
        if len(selected) < 2:
            raise ValueError("合并至少需要两条字幕")
        selected.sort(key=lambda cue: int(cue["start_ms"]))
        merged = {
            **selected[0],
            "start_ms": min(int(cue["start_ms"]) for cue in selected),
            "end_ms": max(int(cue["end_ms"]) for cue in selected),
            "text": str(operation.get("text") or " ".join(str(cue["text"]) for cue in selected)).strip(),
        }
        first_index = min(items.index(cue) for cue in selected)
        items = [cue for cue in items if cue.get("id") not in selected_ids]
        items.insert(first_index, merged)
    elif operation_type == "add":
        cue = operation.get("cue")
        if not cue:
            raise ValueError("新增字幕缺少 cue")
        items.append(_cue_input_to_dict(cue))
    elif operation_type == "delete":
        selected_ids = set(operation.get("cue_ids") or ([cue_id] if cue_id else []))
        items = [cue for cue in items if cue.get("id") not in selected_ids]
    elif operation_type == "shift":
        selected_ids = set(operation.get("cue_ids") or [])
        delta = int(operation.get("delta_ms") or 0)
        for cue in items:
            if not selected_ids or cue.get("id") in selected_ids:
                duration = int(cue["end_ms"]) - int(cue["start_ms"])
                cue["start_ms"] = max(0, int(cue["start_ms"]) + delta)
                cue["end_ms"] = cue["start_ms"] + duration
    elif operation_type == "replace":
        search = str(operation.get("search") or "")
        if not search:
            raise ValueError("搜索文字不能为空")
        replacement = str(operation.get("replacement") or "")
        for cue in items:
            cue["text"] = str(cue["text"]).replace(search, replacement)
    else:
        raise ValueError("不支持的字幕编辑操作")
    return _normalize_cues(items)


def _normalize_cues(cues: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for raw in cues:
        cue = _cue_input_to_dict(raw)
        start_ms = int(cue["start_ms"])
        end_ms = int(cue["end_ms"])
        text = str(cue.get("text") or "").strip()
        if start_ms < 0 or end_ms <= start_ms:
            raise ValueError("字幕时间范围无效")
        if not text:
            raise ValueError("字幕文字不能为空")
        normalized.append(
            {
                # 编辑操作在同一次请求内会连续执行，必须保留当前 revision 的 cue id。
                # 写入新 revision 时会生成新 id；checksum 也不会把旧 id 算进去。
                "id": cue.get("id"),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": text,
                "confidence": cue.get("confidence"),
                "speaker": str(cue.get("speaker") or "").strip()[:80],
                "source_cue_id": cue.get("source_cue_id"),
            }
        )
    return sorted(normalized, key=lambda cue: (cue["start_ms"], cue["end_ms"]))


def _cue_input_to_dict(cue: Any) -> dict[str, Any]:
    if hasattr(cue, "model_dump"):
        return cue.model_dump()
    return dict(cue)


def _cue_checksum(cues: list[dict[str, Any]]) -> str:
    canonical = [
        {
            "start_ms": int(cue["start_ms"]),
            "end_ms": int(cue["end_ms"]),
            "text": str(cue["text"]),
            "confidence": cue.get("confidence"),
            "speaker": str(cue.get("speaker") or ""),
            "source_cue_id": cue.get("source_cue_id"),
        }
        for cue in cues
    ]
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _fetch_all_revision_cues(revision_id: str) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM subtitle_cues WHERE revision_id = ? ORDER BY cue_index ASC",
            (revision_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _sync_dependent_clip_tracks(task_id: str) -> None:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id FROM subtitle_tracks WHERE task_id = ? AND track_type = 'clip' AND is_active = 1",
            (task_id,),
        ).fetchall()
    for row in rows:
        sync_clip_track(row["id"], force=False)


def _track_to_dict(track: dict[str, Any]) -> dict[str, Any]:
    track["has_manual_edits"] = bool(track.get("has_manual_edits"))
    track["is_active"] = bool(track.get("is_active"))
    if track.get("track_type") == "source":
        track["media_url"] = f"/media/tasks/{track['task_id']}/source-video"
    else:
        track["media_url"] = f"/media/tasks/{track['task_id']}/output-clips/{track['output_clip_id']}"
    track["peaks_url"] = f"/api/subtitles/tracks/{track['id']}/peaks"
    return track


def _get_task_row(task_id: str) -> dict[str, Any]:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise ValueError("任务不存在")
    return dict(row)


def _track_media_path(track: dict[str, Any]) -> Path | None:
    if track["track_type"] == "source":
        return get_source_video_path(_get_task_row(track["task_id"]))
    with get_connection() as connection:
        row = connection.execute(
            "SELECT output_file_path FROM output_clip WHERE id = ? AND task_id = ?",
            (track["output_clip_id"], track["task_id"]),
        ).fetchone()
    return resolve_video_file_path(row["output_file_path"]) if row and row["output_file_path"] else None


def _build_pysubs2_document(
    track: dict[str, Any],
    revision_id: str,
    *,
    dynamic_ass: bool,
) -> pysubs2.SSAFile:
    document = pysubs2.SSAFile()
    cues = _fetch_all_revision_cues(revision_id)
    style_names: dict[str, str] = {}
    if dynamic_ass:
        style = _get_default_style()
        width, height = _probe_media_dimensions(_track_media_path(track))
        document.info["PlayResX"] = str(width)
        document.info["PlayResY"] = str(height)
        document.info["ScaledBorderAndShadow"] = "yes"
        alignment = {
            "top_center": pysubs2.Alignment.TOP_CENTER,
            "middle_lower": pysubs2.Alignment.MIDDLE_CENTER,
        }.get(style.get("position"), pysubs2.Alignment.BOTTOM_CENTER)
        base_height = 1920 if height > width else 1080
        scale = height / base_height
        font_size = max(18, float(style.get("font_size") or 42) * scale)
        margin_v = round(height * float(style.get("safe_area_percent") or 5) / 100)
        default_style = pysubs2.SSAStyle(
            fontname=_resolve_font(str(style.get("font_family") or "Microsoft YaHei")),
            fontsize=font_size,
            primarycolor=_hex_color(style.get("font_color") or "#ffffff"),
            outlinecolor=_hex_color(style.get("stroke_color") or "#111827"),
            backcolor=pysubs2.Color(0, 0, 0, 127),
            bold=True,
            outline=float(style.get("outline_width") or 3) * scale,
            shadow=float(style.get("shadow_depth") or 1) * scale if style.get("shadow_enabled") else 0,
            alignment=alignment,
            marginl=round(width * 0.055),
            marginr=round(width * 0.055),
            marginv=margin_v,
        )
        document.styles["Default"] = default_style
        speaker_styles = style.get("speaker_styles") or {}
        for speaker, overrides in speaker_styles.items():
            style_name = f"Speaker_{len(style_names) + 1}"
            speaker_style = default_style.copy()
            if isinstance(overrides, dict) and overrides.get("font_color"):
                speaker_style.primarycolor = _hex_color(overrides["font_color"])
            document.styles[style_name] = speaker_style
            style_names[str(speaker)] = style_name
    for cue in cues:
        speaker = str(cue.get("speaker") or "")
        document.events.append(
            pysubs2.SSAEvent(
                start=int(cue["start_ms"]),
                end=int(cue["end_ms"]),
                text=str(cue["text"]).replace("\n", r"\N"),
                name=speaker,
                style=style_names.get(speaker, "Default"),
            )
        )
    return document


def _get_default_style() -> dict[str, Any]:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM subtitle_style_presets WHERE is_default = 1 ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    style = dict(row) if row else {}
    try:
        style["speaker_styles"] = json.loads(style.get("speaker_styles_json") or "{}")
    except json.JSONDecodeError:
        style["speaker_styles"] = {}
    if not style["speaker_styles"]:
        style["speaker_styles"] = {
            "主播": {"font_color": "#ffffff"},
            "嘉宾": {"font_color": "#ffd60a"},
        }
    style["shadow_enabled"] = bool(style.get("shadow_enabled", True))
    return style


def _probe_media_dimensions(media_path: Path | None) -> tuple[int, int]:
    if not media_path or not media_path.exists() or not shutil.which("ffprobe"):
        return 1080, 1920
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", str(media_path),
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
        payload = json.loads(result.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        if width > 0 and height > 0:
            return width, height
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, IndexError):
        pass
    return 1080, 1920


def _resolve_font(font_family: str) -> str:
    from app.services.subtitle_workflow_service import _resolve_subtitle_font_family

    return _resolve_subtitle_font_family(font_family)


def _hex_color(value: str) -> pysubs2.Color:
    match = re.fullmatch(r"#?([0-9A-Fa-f]{6})", str(value or ""))
    cleaned = match.group(1) if match else "ffffff"
    return pysubs2.Color(int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))


def _downsample_peaks(samples: array, max_points: int) -> list[float]:
    if not samples:
        return []
    bucket_size = max(1, math.ceil(len(samples) / max_points))
    peaks = []
    for index in range(0, len(samples), bucket_size):
        bucket = samples[index : index + bucket_size]
        peak = max(bucket, key=lambda value: abs(value))
        peaks.append(round(float(peak) / 32768, 5))
    return peaks


def _validate_format(format_name: str) -> str:
    normalized = str(format_name or "").lower().lstrip(".")
    if normalized not in {"srt", "vtt", "ass"}:
        raise ValueError("字幕格式只支持 SRT、VTT 或 ASS")
    return normalized


def _safe_file_stem(value: str) -> str:
    cleaned = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", value, flags=re.UNICODE).strip("_")
    return cleaned[:80] or "subtitles"


def _issue(cue_id: str, code: str, severity: str, message: str) -> dict[str, str]:
    return {"cue_id": cue_id, "code": code, "severity": severity, "message": message}


def _chinese_char_count(value: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", value or ""))


__all__ = [
    "SubtitleRevisionConflict",
    "apply_revision_operations",
    "approve_revision",
    "approve_revisions_with_connection",
    "create_manual_revision",
    "ensure_clip_track",
    "ensure_output_clip_snapshot",
    "ensure_source_track",
    "evaluate_subtitle_quality",
    "export_subtitle_text",
    "get_revision",
    "get_revision_cues",
    "get_track",
    "get_waveform_peaks",
    "import_subtitle_text",
    "inherit_cues_for_clip",
    "list_revisions",
    "list_task_tracks",
    "serialize_revision_to_ass",
    "sync_clip_track",
]
