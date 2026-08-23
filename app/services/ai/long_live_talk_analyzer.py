"""语言类长直播的可恢复分层高光选片。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Callable
from uuid import uuid4

from app.db.database import get_connection
from app.models.task import AIClipAnalysisResult
from app.services.ai.ai_clip_analyzer import (
    AIAnalysisError,
    TranscriptRow,
    _extract_transcript_rows,
    _loads_ai_json,
    _read_transcript,
    _seconds_to_time,
    _time_to_seconds,
    build_provider,
)
from app.services.ai.base import AIProvider


WINDOW_SECONDS = 300
WINDOW_OVERLAP_SECONDS = 60
WINDOW_CHAR_BUDGET = 12_000
WINDOW_RECALL_LIMIT = 5
MIN_COMPLETE_COVERAGE = 0.90
ALLOWED_CATEGORIES = (
    "quote_opinion",
    "story_experience",
    "emotional_peak",
    "conflict_reversal",
    "practical_knowledge",
    "interactive_humor",
)
CATEGORY_LABELS = {
    "quote_opinion": "金句观点",
    "story_experience": "故事经历",
    "emotional_peak": "情绪峰值",
    "conflict_reversal": "冲突反转",
    "practical_knowledge": "实用知识",
    "interactive_humor": "互动幽默",
}


@dataclass(frozen=True)
class LongLiveAnalysisRequest:
    task_id: str
    transcript_path: Path
    provider_name: str
    model_name: str
    density_per_hour: int = 4
    total_limit: int = 30
    ai_preference: str = ""
    prompt_template: str | None = None


@dataclass(frozen=True)
class LongLiveWindow:
    index: int
    total: int
    start_seconds: int
    end_seconds: int
    rows: tuple[TranscriptRow, ...]
    text: str


@dataclass(frozen=True)
class LongLiveAnalysisOutcome:
    result: AIClipAnalysisResult
    meta: dict[str, Any]


def analyze_long_live_talk(
    request: LongLiveAnalysisRequest,
    *,
    provider: AIProvider | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> LongLiveAnalysisOutcome:
    transcript_text = _read_transcript(request.transcript_path)
    rows = _extract_transcript_rows(transcript_text)
    if not rows:
        raise AIAnalysisError("长直播分析失败：转写中没有可识别的逐句时间戳")

    windows = build_long_live_windows(rows)
    if not windows:
        raise AIAnalysisError("长直播分析失败：没有生成可分析窗口")

    transcript_fingerprint = hashlib.sha256(transcript_text.encode("utf-8")).hexdigest()
    provider = provider or build_provider(request.provider_name)
    preference = _preference_summary(request.prompt_template or "", request.ai_preference)
    successful_payloads: list[dict[str, Any]] = []
    completed_windows: list[LongLiveWindow] = []
    failed_windows: list[dict[str, Any]] = []
    reused_count = 0

    for window in windows:
        checkpoint = _get_or_create_checkpoint(request, transcript_fingerprint, window)
        if checkpoint.get("status") == "completed" and checkpoint.get("result_json"):
            payload = _load_verified_checkpoint_payload(checkpoint)
            if isinstance(payload, dict):
                successful_payloads.append(payload)
                completed_windows.append(window)
                reused_count += 1
                _report_progress(progress_callback, window, "reused", len(completed_windows))
                continue

        max_attempts = 3 if request.provider_name == "remote" else 1
        last_error = ""
        payload = None
        for attempt in range(1, max_attempts + 1):
            _mark_checkpoint_running(checkpoint["id"])
            try:
                raw = provider.generate_json(_window_prompt(window, preference))
                payload = _parse_window_payload(raw, window)
                if not payload.get("moments"):
                    payload = {"moments": []}
                _mark_checkpoint_completed(checkpoint["id"], payload)
                successful_payloads.append(payload)
                completed_windows.append(window)
                _report_progress(progress_callback, window, "completed", len(completed_windows))
                break
            except Exception as exc:  # 每个窗口必须独立记录，不能丢失前面成功结果
                last_error = " ".join(str(exc).split())[:1000] or "未知错误"
                should_retry = attempt < max_attempts
                delay_seconds = 2 ** (attempt - 1) if should_retry else 0
                _mark_checkpoint_failed(checkpoint["id"], last_error, delay_seconds)
                if should_retry:
                    sleep_fn(delay_seconds)
        if payload is None:
            failed_windows.append(
                {
                    "window_index": window.index,
                    "start_seconds": window.start_seconds,
                    "end_seconds": window.end_seconds,
                    "error": last_error,
                }
            )
            _report_progress(progress_callback, window, "failed", len(completed_windows))

    if not completed_windows:
        detail = "；".join(item["error"] for item in failed_windows[:3]) or "全部窗口均失败"
        raise AIAnalysisError(f"长直播分析没有完成任何窗口：{detail}")

    moments: list[dict[str, Any]] = []
    for payload in successful_payloads:
        moments.extend(payload.get("moments") or [])
    deduplicated = deduplicate_long_live_moments(moments)
    density = max(1, min(10, int(request.density_per_hour or 4)))
    total_limit = max(1, min(50, int(request.total_limit or 30)))
    selected = select_temporally_balanced_highlights(deduplicated, density, total_limit)
    clips = [_moment_to_clip(moment, index) for index, moment in enumerate(selected, start=1)]

    transcript_start = rows[0].start_seconds
    transcript_end = rows[-1].end_seconds
    coverage_ratio = calculate_window_coverage(
        [(window.start_seconds, window.end_seconds) for window in completed_windows],
        transcript_start,
        transcript_end,
    )
    incomplete = coverage_ratio < MIN_COMPLETE_COVERAGE
    coverage_percent = round(coverage_ratio * 100, 2)
    summary = (
        f"长直播高光已完成 {len(completed_windows)}/{len(windows)} 个重叠窗口，"
        f"时间轴覆盖 {coverage_percent:.2f}%，去重后保留 {len(clips)} 条候选。"
    )
    if incomplete:
        summary += " 当前分析不完整，必须补齐失败窗口后才能进入自动切片。"

    meta = {
        "transcript_fingerprint": transcript_fingerprint,
        "window_seconds": WINDOW_SECONDS,
        "window_overlap_seconds": WINDOW_OVERLAP_SECONDS,
        "window_count": len(windows),
        "completed_window_count": len(completed_windows),
        "failed_window_count": len(failed_windows),
        "failed_windows": failed_windows,
        "reused_window_count": reused_count,
        "coverage_ratio": round(coverage_ratio, 6),
        "coverage_percent": coverage_percent,
        "analysis_incomplete": incomplete,
        "minimum_complete_coverage": MIN_COMPLETE_COVERAGE,
        "highlight_density_per_hour": density,
        "highlight_total_limit": total_limit,
        "deduplicated_moment_count": len(deduplicated),
        "selected_highlight_count": len(clips),
    }
    return LongLiveAnalysisOutcome(
        result=AIClipAnalysisResult(task_id=request.task_id, analysis_summary=summary, clips=clips),
        meta=meta,
    )


def build_long_live_windows(rows: list[TranscriptRow]) -> list[LongLiveWindow]:
    """按约 5 分钟、60 秒重叠构造窗口，并兼顾 prompt 字符预算。"""
    if not rows:
        return []
    raw_windows: list[tuple[TranscriptRow, ...]] = []
    start_index = 0
    while start_index < len(rows):
        start_seconds = rows[start_index].start_seconds
        current: list[TranscriptRow] = []
        current_chars = 0
        end_index = start_index
        while end_index < len(rows):
            row = rows[end_index]
            line = _format_row(row)
            exceeds_time = bool(current) and row.end_seconds - start_seconds > WINDOW_SECONDS
            exceeds_chars = bool(current) and current_chars + len(line) + 1 > WINDOW_CHAR_BUDGET
            if exceeds_time or exceeds_chars:
                break
            current.append(row)
            current_chars += len(line) + 1
            end_index += 1
        if not current:
            current = [rows[start_index]]
            end_index = start_index + 1
        raw_windows.append(tuple(current))
        if end_index >= len(rows):
            break
        next_time = max(current[0].start_seconds + 1, current[-1].end_seconds - WINDOW_OVERLAP_SECONDS)
        next_index = start_index + 1
        while next_index < end_index and rows[next_index].start_seconds < next_time:
            next_index += 1
        start_index = max(start_index + 1, next_index)

    total = len(raw_windows)
    return [
        LongLiveWindow(
            index=index,
            total=total,
            start_seconds=window_rows[0].start_seconds,
            end_seconds=window_rows[-1].end_seconds,
            rows=window_rows,
            text="\n".join(_format_row(row) for row in window_rows),
        )
        for index, window_rows in enumerate(raw_windows, start=1)
    ]


def calculate_window_coverage(
    intervals: list[tuple[int, int]],
    timeline_start: int,
    timeline_end: int,
) -> float:
    if timeline_end <= timeline_start:
        return 1.0 if intervals else 0.0
    clipped = sorted(
        (max(timeline_start, start), min(timeline_end, end))
        for start, end in intervals
        if end > timeline_start and start < timeline_end and end > start
    )
    if not clipped:
        return 0.0
    merged: list[list[int]] = []
    for start, end in clipped:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    covered = sum(end - start for start, end in merged)
    return min(1.0, covered / (timeline_end - timeline_start))


def deduplicate_long_live_moments(moments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(moments, key=lambda item: float(item.get("score") or 0), reverse=True)
    selected: list[dict[str, Any]] = []
    for raw in ranked:
        moment = _normalize_moment(raw)
        if not moment:
            continue
        duplicate_index = None
        for index, existing in enumerate(selected):
            if _moments_are_duplicate(moment, existing):
                duplicate_index = index
                break
        if duplicate_index is None:
            selected.append(moment)
        else:
            selected[duplicate_index] = _merge_moments(selected[duplicate_index], moment)
    return sorted(selected, key=lambda item: int(item["start_seconds"]))


def select_temporally_balanced_highlights(
    moments: list[dict[str, Any]],
    density_per_hour: int,
    total_limit: int,
) -> list[dict[str, Any]]:
    density = max(1, min(10, int(density_per_hour or 4)))
    limit = max(1, min(50, int(total_limit or 30)))
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for moment in moments:
        midpoint = (int(moment["start_seconds"]) + int(moment["end_seconds"])) / 2
        buckets[int(midpoint // 3600)].append(moment)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
        del bucket[density:]

    hours = sorted(buckets)
    if len(hours) > limit:
        indexes = _evenly_spaced_indexes(len(hours), limit)
        hours = [hours[index] for index in indexes]

    selected: list[dict[str, Any]] = []
    for rank in range(density):
        for hour in hours:
            if rank < len(buckets[hour]):
                selected.append(buckets[hour][rank])
                if len(selected) >= limit:
                    return sorted(selected, key=lambda item: int(item["start_seconds"]))
    return sorted(selected, key=lambda item: int(item["start_seconds"]))


def list_long_live_window_checkpoints(task_id: str) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM ai_analysis_windows
            WHERE task_id = ?
            ORDER BY updated_at DESC, window_index ASC
            """,
            (task_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_latest_long_live_window_status(task_id: str) -> dict[str, Any]:
    """返回最新一组窗口的轻量统计，不把历史转写指纹混入当前进度。"""
    with get_connection() as connection:
        latest = connection.execute(
            """
            SELECT transcript_fingerprint, provider, model
            FROM ai_analysis_windows
            WHERE task_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if not latest:
            return {}
        rows = connection.execute(
            """
            SELECT window_index, start_seconds, end_seconds, status,
                   attempt_count, error_message, updated_at
            FROM ai_analysis_windows
            WHERE task_id = ? AND transcript_fingerprint = ? AND provider = ? AND model = ?
            ORDER BY window_index ASC
            """,
            (task_id, latest["transcript_fingerprint"], latest["provider"], latest["model"]),
        ).fetchall()
    items = [dict(row) for row in rows]
    completed = sum(1 for item in items if item["status"] == "completed")
    failed = [item for item in items if item["status"] == "failed"]
    return {
        "provider": latest["provider"],
        "model": latest["model"],
        "window_count": len(items),
        "completed_window_count": completed,
        "failed_window_count": len(failed),
        "failed_windows": failed,
        "percent": round(completed / len(items) * 100) if items else 0,
    }


def _get_or_create_checkpoint(
    request: LongLiveAnalysisRequest,
    fingerprint: str,
    window: LongLiveWindow,
) -> dict[str, Any]:
    now = _now_iso()
    checkpoint_id = uuid4().hex
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO ai_analysis_windows (
                id, task_id, transcript_fingerprint, provider, model,
                window_index, start_seconds, end_seconds, status,
                attempt_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?)
            """,
            (
                checkpoint_id,
                request.task_id,
                fingerprint,
                request.provider_name,
                request.model_name,
                window.index,
                window.start_seconds,
                window.end_seconds,
                now,
                now,
            ),
        )
        row = connection.execute(
            """
            SELECT * FROM ai_analysis_windows
            WHERE task_id = ? AND transcript_fingerprint = ? AND provider = ? AND model = ?
              AND window_index = ? AND start_seconds = ? AND end_seconds = ?
            """,
            (
                request.task_id,
                fingerprint,
                request.provider_name,
                request.model_name,
                window.index,
                window.start_seconds,
                window.end_seconds,
            ),
        ).fetchone()
        connection.commit()
    if not row:
        raise AIAnalysisError(f"无法创建长直播窗口 checkpoint：{window.index}")
    return dict(row)


def _mark_checkpoint_running(checkpoint_id: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE ai_analysis_windows
            SET status = 'running', attempt_count = attempt_count + 1,
                error_message = NULL, next_retry_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (_now_iso(), checkpoint_id),
        )
        connection.commit()


def _mark_checkpoint_completed(checkpoint_id: str, payload: dict[str, Any]) -> None:
    now = _now_iso()
    result_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    checksum = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE ai_analysis_windows
            SET status = 'completed', result_json = ?, result_checksum = ?,
                error_message = NULL, next_retry_at = NULL,
                updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (result_json, checksum, now, now, checkpoint_id),
        )
        connection.commit()


def _mark_checkpoint_failed(checkpoint_id: str, error: str, delay_seconds: int) -> None:
    now = datetime.now().astimezone()
    next_retry_at = (now + timedelta(seconds=delay_seconds)).isoformat(timespec="seconds") if delay_seconds else None
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE ai_analysis_windows
            SET status = 'failed', error_message = ?, next_retry_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (error, next_retry_at, now.isoformat(timespec="seconds"), checkpoint_id),
        )
        connection.commit()


def _load_verified_checkpoint_payload(checkpoint: dict[str, Any]) -> dict[str, Any] | None:
    result_json = str(checkpoint.get("result_json") or "")
    expected_checksum = str(checkpoint.get("result_checksum") or "")
    if not result_json or not expected_checksum:
        return None
    actual_checksum = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
    if actual_checksum != expected_checksum:
        return None
    try:
        payload = json.loads(result_json)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_window_payload(raw: str, window: LongLiveWindow) -> dict[str, Any]:
    payload = _loads_ai_json(raw)
    if isinstance(payload, list):
        payload = {"moments": payload}
    if not isinstance(payload, dict):
        raise AIAnalysisError("长直播窗口输出必须是 JSON 对象")
    moments = payload.get("moments")
    if moments is None:
        moments = payload.get("clips") or payload.get("candidates") or []
    if not isinstance(moments, list):
        raise AIAnalysisError("长直播窗口输出缺少 moments 数组")
    normalized: list[dict[str, Any]] = []
    for item in moments[:WINDOW_RECALL_LIMIT]:
        if not isinstance(item, dict):
            continue
        moment = _normalize_moment(item, window=window)
        if moment:
            normalized.append(moment)
    return {"moments": normalized}


def _normalize_moment(
    item: dict[str, Any],
    *,
    window: LongLiveWindow | None = None,
) -> dict[str, Any] | None:
    try:
        start_seconds = _coerce_seconds(item.get("start_seconds"), item.get("start_time"))
        end_seconds = _coerce_seconds(item.get("end_seconds"), item.get("end_time"))
    except (TypeError, ValueError):
        return None
    if end_seconds <= start_seconds:
        return None
    if window and (start_seconds < window.start_seconds - 5 or end_seconds > window.end_seconds + 5):
        return None
    duration = end_seconds - start_seconds
    if duration < 15 or duration > 300:
        return None
    category = str(item.get("category") or "quote_opinion").strip().lower()
    if category not in ALLOWED_CATEGORIES:
        category = "quote_opinion"
    score = _bounded_float(item.get("score") or item.get("confidence_score") or 70, 0, 100)
    title = _clean_text(item.get("title"), "长直播高光", 160)
    summary = _clean_text(item.get("summary"), title, 1000)
    reason = _clean_text(item.get("highlight_reason") or item.get("reason"), CATEGORY_LABELS[category], 1000)
    topic_key = _clean_text(item.get("topic_key"), title, 120)
    key_seconds = _coerce_seconds(item.get("key_seconds"), item.get("key_time"), fallback=(start_seconds + end_seconds) // 2)
    key_seconds = max(start_seconds, min(end_seconds, key_seconds))
    return {
        "title": title,
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "key_seconds": key_seconds,
        "summary": summary,
        "highlight_reason": reason,
        "suggested_editing": _clean_text(
            item.get("suggested_editing"),
            "保留观点或故事闭环，剪掉明显停顿和重复表达。",
            1000,
        ),
        "category": category,
        "topic_key": topic_key,
        "score": score,
        "source_window_indexes": [window.index] if window else list(item.get("source_window_indexes") or []),
    }


def _moments_are_duplicate(first: dict[str, Any], second: dict[str, Any]) -> bool:
    overlap = max(
        0,
        min(int(first["end_seconds"]), int(second["end_seconds"]))
        - max(int(first["start_seconds"]), int(second["start_seconds"])),
    )
    shorter = max(
        1,
        min(
            int(first["end_seconds"]) - int(first["start_seconds"]),
            int(second["end_seconds"]) - int(second["start_seconds"]),
        ),
    )
    if overlap / shorter >= 0.35:
        return True
    key_distance = abs(int(first["key_seconds"]) - int(second["key_seconds"]))
    if key_distance <= 30:
        return True
    semantic = _semantic_similarity(
        f"{first.get('topic_key', '')}{first.get('title', '')}{first.get('summary', '')}",
        f"{second.get('topic_key', '')}{second.get('title', '')}{second.get('summary', '')}",
    )
    return key_distance <= 120 and semantic >= 0.34


def _merge_moments(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    stronger, other = (first, second) if float(first["score"]) >= float(second["score"]) else (second, first)
    merged = dict(stronger)
    merged["start_seconds"] = min(int(first["start_seconds"]), int(second["start_seconds"]))
    merged["end_seconds"] = max(int(first["end_seconds"]), int(second["end_seconds"]))
    merged["source_window_indexes"] = sorted(
        set(first.get("source_window_indexes") or []) | set(second.get("source_window_indexes") or [])
    )
    if len(str(other.get("summary") or "")) > len(str(merged.get("summary") or "")):
        merged["summary"] = other["summary"]
    return merged


def _moment_to_clip(moment: dict[str, Any], index: int) -> dict[str, Any]:
    start = int(moment["start_seconds"])
    end = int(moment["end_seconds"])
    duration = max(1, end - start)
    score = float(moment.get("score") or 0)
    quality_tier = "A" if score >= 80 else "B" if score >= 65 else "C"
    return {
        "clip_id": f"long_live_{index:03d}",
        "title": moment["title"],
        "start_time": _seconds_to_time(start),
        "end_time": _seconds_to_time(end),
        "duration_seconds": duration,
        "cover_time_seconds": max(0.0, min(duration - 0.001, float(moment["key_seconds"] - start))),
        "summary": moment["summary"],
        "highlight_reason": f"{CATEGORY_LABELS[moment['category']]}：{moment['highlight_reason']}",
        "spread_value": "高" if score >= 80 else "中",
        "suggested_editing": moment["suggested_editing"],
        "confidence_score": round(score / 100, 4),
        "selected_by_default": True,
        "quality_tier": quality_tier,
        "quality_score": score,
        "text_quality_score": score,
        "humor_score": score if moment["category"] == "interactive_humor" else 0,
        "completeness_score": score,
        "audio_reaction_score": 0,
        "topic_key": moment["topic_key"],
        "key_moment_time": _seconds_to_time(int(moment["key_seconds"])),
        "quality_evidence": {
            "highlight_category": moment["category"],
            "highlight_category_label": CATEGORY_LABELS[moment["category"]],
            "source_window_indexes": moment.get("source_window_indexes") or [],
        },
        "rejection_reason": "",
    }


def _window_prompt(window: LongLiveWindow, preference: str) -> str:
    return f"""你是语言内容型长直播的高光召回编辑。只分析当前约 5 分钟窗口，不评价整场直播。
从下列六类中找出 0-{WINDOW_RECALL_LIMIT} 个可独立理解的高光：
1. quote_opinion 金句观点；2. story_experience 故事经历；3. emotional_peak 情绪峰值；
4. conflict_reversal 冲突反转；5. practical_knowledge 实用知识；6. interactive_humor 互动幽默。
不要为凑数输出寒暄、重复表达、纯过场。片段建议 45-180 秒，必要时可为 15-300 秒。
{preference}
只返回严格 JSON：{{"moments":[{{"title":"标题","category":"六类英文值之一","start_time":"HH:MM:SS","end_time":"HH:MM:SS","key_time":"HH:MM:SS","topic_key":"稳定话题标识","summary":"完整内容闭环","highlight_reason":"具体价值","suggested_editing":"剪辑建议","score":0}}]}}
score 为 0-100；时间必须来自窗口转写。没有高光就返回空数组。

窗口 {window.index}/{window.total}，范围 {_seconds_to_time(window.start_seconds)}-{_seconds_to_time(window.end_seconds)}：
{window.text}"""


def _preference_summary(prompt_template: str, ai_preference: str) -> str:
    prompt = (prompt_template or "").replace("{{AI_PREFERENCE}}", ai_preference or "")
    for marker in ("# Output Format", "【输出格式】", "输出 JSON", "转写文本：", "# Transcript", "{{TRANSCRIPT_TEXT}}"):
        if marker in prompt:
            prompt = prompt.split(marker, 1)[0]
    prompt = " ".join(prompt.split())[:2000]
    extra = " ".join((ai_preference or "").split())[:500]
    parts = []
    if prompt:
        parts.append(f"沿用本任务内容偏好：{prompt}")
    if extra and extra not in prompt:
        parts.append(f"用户补充偏好：{extra}")
    return "\n".join(parts)


def _semantic_similarity(first: str, second: str) -> float:
    first_tokens = _semantic_tokens(first)
    second_tokens = _semantic_tokens(second)
    if not first_tokens or not second_tokens:
        return 0.0
    return len(first_tokens & second_tokens) / len(first_tokens | second_tokens)


def _semantic_tokens(value: str) -> set[str]:
    normalized = re.sub(r"\s+", "", str(value or "").lower())
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    tokens = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    tokens.update(re.findall(r"[a-z0-9]{2,}", normalized))
    return tokens


def _coerce_seconds(value: Any, time_text: Any, fallback: int | None = None) -> int:
    if value not in (None, ""):
        return int(float(value))
    if time_text not in (None, ""):
        return _time_to_seconds(str(time_text))
    if fallback is not None:
        return fallback
    raise ValueError("缺少时间")


def _bounded_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return round(max(minimum, min(maximum, number)), 2)


def _clean_text(value: Any, fallback: str, limit: int) -> str:
    text = " ".join(str(value or "").split()) or fallback
    return text[:limit]


def _format_row(row: TranscriptRow) -> str:
    return f"{row.start_time} - {row.end_time} {row.text}"


def _evenly_spaced_indexes(length: int, count: int) -> list[int]:
    if count >= length:
        return list(range(length))
    if count <= 1:
        return [length // 2]
    return sorted({round(index * (length - 1) / (count - 1)) for index in range(count)})


def _report_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    window: LongLiveWindow,
    status: str,
    completed_count: int,
) -> None:
    if callback:
        callback(
            {
                "window_index": window.index,
                "window_count": window.total,
                "status": status,
                "completed_count": completed_count,
                "percent": min(99, math.floor(window.index / max(1, window.total) * 100)),
            }
        )


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = [
    "ALLOWED_CATEGORIES",
    "LongLiveAnalysisOutcome",
    "LongLiveAnalysisRequest",
    "LongLiveWindow",
    "MIN_COMPLETE_COVERAGE",
    "analyze_long_live_talk",
    "build_long_live_windows",
    "calculate_window_coverage",
    "deduplicate_long_live_moments",
    "get_latest_long_live_window_status",
    "list_long_live_window_checkpoints",
    "select_temporally_balanced_highlights",
]
