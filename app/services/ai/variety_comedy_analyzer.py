"""《康熙来了》类综艺的质量优先三阶段选片。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from app.models.task import AIClipAnalysisResult
from app.services.ai.base import AIProvider, generate_json_with_safe_retry
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
from app.services.audio_reaction_service import analyze_audio_reaction
from app.services.clip_feedback_service import list_recent_feedback_context
from app.services.ai.unit_checkpoint import (
    build_unit_fingerprint,
    execute_checkpointed_ai_unit,
    provider_fingerprint_fields,
)


REMOTE_WINDOW_SECONDS = 300
REMOTE_WINDOW_OVERLAP_SECONDS = 60
REMOTE_TRANSCRIPT_CHAR_BUDGET = 8_000
LOCAL_WINDOW_SECONDS = 180
LOCAL_WINDOW_OVERLAP_SECONDS = 45
LOCAL_TRANSCRIPT_CHAR_BUDGET = 4_000
RECALL_LIMIT_PER_WINDOW = 3
EXPANSION_BATCH_SIZE_REMOTE = 3
MAX_PRELIMINARY_MOMENTS = 18
PREFERRED_MIN_CLIP_SECONDS = 60
MIN_ACCEPTED_CLIP_SECONDS = 45
MAX_COMEDY_CLIP_SECONDS = 150
QUALITY_A_THRESHOLD = 78
QUALITY_B_THRESHOLD = 65
HUMOR_HARD_GATE = 75
COMPLETENESS_HARD_GATE = 70


@dataclass(frozen=True)
class ComedyAnalysisRequest:
    task_id: str
    transcript_path: Path
    audio_path: Path
    candidate_pool_limit: int
    final_clip_target: int
    ai_preference: str
    provider_name: str
    prompt_template: str | None = None


@dataclass(frozen=True)
class ComedyTranscriptWindow:
    index: int
    total: int
    start_seconds: int
    end_seconds: int
    rows: tuple[TranscriptRow, ...]
    text: str


def analyze_variety_comedy(request: ComedyAnalysisRequest) -> AIClipAnalysisResult:
    transcript_text = _read_transcript(request.transcript_path)
    rows = _extract_transcript_rows(transcript_text)
    if not rows:
        raise AIAnalysisError("综艺笑点分析失败：转写中没有可识别的逐句时间戳")

    provider = build_provider(request.provider_name)
    windows = build_comedy_windows(rows, provider_name=request.provider_name)
    preference = _preference_summary(request.prompt_template or "", request.ai_preference)
    unit_fingerprint = build_unit_fingerprint(
        {
            "profile": "variety_comedy",
            "transcript_sha256": hashlib.sha256(transcript_text.encode("utf-8")).hexdigest(),
            "provider": request.provider_name,
            "provider_identity": provider_fingerprint_fields(provider),
            "prompt_template": request.prompt_template or "",
            "ai_preference": request.ai_preference,
            "candidate_pool_limit": request.candidate_pool_limit,
            "final_clip_target": request.final_clip_target,
        }
    )
    moments, recall_failures, recall_stats = _recall_moments(
        provider,
        windows,
        preference,
        task_id=request.task_id,
        input_fingerprint=unit_fingerprint,
    )
    moments = dedupe_recall_moments(moments)[:MAX_PRELIMINARY_MOMENTS]
    if not moments:
        detail = "；".join(recall_failures[:3]) or "没有召回达到条件的笑点时刻"
        raise AIAnalysisError(f"综艺笑点分析没有召回可用内容：{detail}")

    expanded, expansion_failures, expansion_stats = _expand_moments(
        provider,
        rows,
        moments,
        preference,
        provider_name=request.provider_name,
        task_id=request.task_id,
        input_fingerprint=unit_fingerprint,
    )
    expanded = dedupe_expanded_candidates(expanded)[:MAX_PRELIMINARY_MOMENTS]
    if not expanded:
        detail = "；".join(expansion_failures[:3]) or "没有形成完整的 60–150 秒内容闭环"
        raise AIAnalysisError(f"综艺笑点分析没有形成完整候选：{detail}")

    for candidate in expanded:
        start_seconds = _time_to_seconds(candidate["start_time"])
        end_seconds = _time_to_seconds(candidate["end_time"])
        key_seconds = _time_to_seconds(candidate["key_moment_time"])
        candidate_rows = _rows_in_range(rows, start_seconds, end_seconds)
        candidate["audio_evidence"] = analyze_audio_reaction(
            request.audio_path,
            start_seconds,
            end_seconds,
            key_seconds,
            candidate_rows,
        )

    feedback = list_recent_feedback_context("variety_comedy", limit=20)
    judge_payload, judge_warning = _global_judge(
        provider,
        expanded,
        preference,
        feedback,
        task_id=request.task_id,
        input_fingerprint=unit_fingerprint,
    )
    scored = [
        score_comedy_candidate(candidate, judge_payload.get(candidate["source_id"]) or {})
        for candidate in expanded
    ]
    scored = dedupe_scored_candidates(scored)
    candidate_pool_limit = max(1, min(12, int(request.candidate_pool_limit or 12)))
    kept = [item for item in scored if item["quality_tier"] in {"A", "B"}]
    kept = sorted(kept, key=lambda item: item["quality_score"], reverse=True)[:candidate_pool_limit]

    a_ranked = [item for item in kept if item["quality_tier"] == "A"]
    selected_ids = {
        item["source_id"]
        for item in a_ranked[: max(1, min(12, int(request.final_clip_target or 5)))]
    }
    for item in kept:
        item["selected_by_default"] = item["source_id"] in selected_ids

    kept = sorted(kept, key=lambda item: _time_to_seconds(item["start_time"]))
    clips = []
    for index, item in enumerate(kept, start=1):
        clips.append(_to_clip_payload(item, index))

    selected_count = sum(1 for clip in clips if clip["selected_by_default"])
    summary = (
        f"综艺笑点优先 V2 已按 {len(windows)} 个重叠窗口召回，"
        f"扩展并全局复评 {len(expanded)} 条，保留 {len(clips)} 条候选，"
        f"其中 {selected_count} 条达到 A 级并默认启用。"
    )
    warnings = [*recall_failures, *expansion_failures]
    if judge_warning:
        warnings.append(judge_warning)
    if warnings:
        summary += f" 有 {len(warnings)} 个局部步骤已降级或跳过。"
    expected_units = int(recall_stats["expected_units"]) + int(expansion_stats["expected_units"]) + 1
    completed_units = (
        int(recall_stats["completed_units"])
        + int(expansion_stats["completed_units"])
        + (0 if judge_warning else 1)
    )
    failed_units = (
        int(recall_stats["failed_units"])
        + int(expansion_stats["failed_units"])
        + (1 if judge_warning else 0)
    )
    invalid_item_count = int(recall_stats["invalid_item_count"]) + int(
        expansion_stats["invalid_item_count"]
    )
    coverage_ratio = completed_units / expected_units if expected_units else 0.0
    failed_stages = [
        {"stage": "recall", "message": " ".join(message.split())[:500]}
        for message in recall_failures
    ] + [
        {"stage": "expansion", "message": " ".join(message.split())[:500]}
        for message in expansion_failures
    ]
    if judge_warning:
        failed_stages.append({"stage": "global_judge", "message": " ".join(judge_warning.split())[:500]})
    return AIClipAnalysisResult(
        task_id=request.task_id,
        analysis_summary=summary,
        clips=clips,
        analysis_meta={
            "schema_version": 2,
            "coverage_basis": "recall_and_expansion_units",
            "expected_units": expected_units,
            "completed_units": completed_units,
            "failed_units": failed_units,
            "empty_unit_count": int(recall_stats["empty_unit_count"])
            + int(expansion_stats["empty_unit_count"]),
            "invalid_item_count": invalid_item_count,
            "coverage_ratio": round(coverage_ratio, 6),
            "coverage_percent": round(coverage_ratio * 100, 2),
            "analysis_incomplete": bool(failed_units or invalid_item_count or judge_warning),
            "quality_degraded": bool(judge_warning),
            "failed_stages": failed_stages,
        },
    )


def build_comedy_windows(
    rows: list[TranscriptRow],
    *,
    provider_name: str,
) -> list[ComedyTranscriptWindow]:
    if provider_name == "local":
        duration_limit = LOCAL_WINDOW_SECONDS
        overlap_seconds = LOCAL_WINDOW_OVERLAP_SECONDS
        char_budget = LOCAL_TRANSCRIPT_CHAR_BUDGET
    else:
        duration_limit = REMOTE_WINDOW_SECONDS
        overlap_seconds = REMOTE_WINDOW_OVERLAP_SECONDS
        char_budget = REMOTE_TRANSCRIPT_CHAR_BUDGET

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
            exceeds_time = bool(current) and row.end_seconds - start_seconds > duration_limit
            exceeds_chars = bool(current) and current_chars + len(line) + 1 > char_budget
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
        next_time = max(current[0].start_seconds + 1, current[-1].end_seconds - overlap_seconds)
        next_index = start_index + 1
        while next_index < end_index and rows[next_index].end_seconds < next_time:
            next_index += 1
        start_index = max(start_index + 1, next_index)

    total = len(raw_windows)
    return [
        ComedyTranscriptWindow(
            index=index,
            total=total,
            start_seconds=window_rows[0].start_seconds,
            end_seconds=window_rows[-1].end_seconds,
            rows=window_rows,
            text="\n".join(_format_row(row) for row in window_rows),
        )
        for index, window_rows in enumerate(raw_windows, start=1)
    ]


def dedupe_recall_moments(moments: list[dict]) -> list[dict]:
    selected: list[dict] = []
    for moment in sorted(moments, key=lambda item: float(item.get("recall_score") or 0), reverse=True):
        key_seconds = int(moment["key_seconds"])
        topic = _normalize_topic(moment.get("topic_key") or moment.get("title") or "")
        duplicate = False
        for existing in selected:
            existing_topic = _normalize_topic(existing.get("topic_key") or existing.get("title") or "")
            if abs(key_seconds - int(existing["key_seconds"])) <= 30:
                duplicate = True
                break
            if topic and topic == existing_topic and abs(key_seconds - int(existing["key_seconds"])) <= 120:
                duplicate = True
                break
        if not duplicate:
            selected.append(moment)
    return sorted(selected, key=lambda item: int(item["key_seconds"]))


def normalize_clip_bounds(
    start_seconds: int,
    end_seconds: int,
    key_seconds: int,
    context_rows: list[TranscriptRow],
) -> tuple[int, int] | None:
    if not context_rows:
        return None
    lower = context_rows[0].start_seconds
    upper = context_rows[-1].end_seconds
    start_seconds = max(lower, min(start_seconds, key_seconds))
    end_seconds = min(upper, max(end_seconds, key_seconds + 1))

    if end_seconds - start_seconds < PREFERRED_MIN_CLIP_SECONDS:
        missing = PREFERRED_MIN_CLIP_SECONDS - (end_seconds - start_seconds)
        start_seconds = max(lower, start_seconds - (missing // 2 + missing % 2))
        end_seconds = min(upper, end_seconds + missing // 2)
        if end_seconds - start_seconds < PREFERRED_MIN_CLIP_SECONDS:
            if start_seconds == lower:
                end_seconds = min(upper, start_seconds + PREFERRED_MIN_CLIP_SECONDS)
            else:
                start_seconds = max(lower, end_seconds - PREFERRED_MIN_CLIP_SECONDS)

    if end_seconds - start_seconds > MAX_COMEDY_CLIP_SECONDS:
        start_seconds = max(lower, key_seconds - 60)
        end_seconds = min(upper, start_seconds + MAX_COMEDY_CLIP_SECONDS)
        if end_seconds <= key_seconds:
            end_seconds = min(upper, key_seconds + 90)
            start_seconds = max(lower, end_seconds - MAX_COMEDY_CLIP_SECONDS)

    start_seconds = _nearest_boundary(start_seconds, context_rows, use_start=True)
    end_seconds = _nearest_boundary(end_seconds, context_rows, use_start=False)
    start_seconds, end_seconds = _expand_bounds_to_min_duration(
        start_seconds,
        end_seconds,
        context_rows,
        PREFERRED_MIN_CLIP_SECONDS,
    )
    duration = end_seconds - start_seconds
    if duration < MIN_ACCEPTED_CLIP_SECONDS or duration > MAX_COMEDY_CLIP_SECONDS:
        return None
    if not start_seconds <= key_seconds < end_seconds:
        return None
    return start_seconds, end_seconds


def dedupe_expanded_candidates(candidates: list[dict]) -> list[dict]:
    ranked = sorted(
        candidates,
        key=lambda item: float(item.get("humor_score") or 0) + float(item.get("completeness_score") or 0),
        reverse=True,
    )
    selected: list[dict] = []
    for candidate in ranked:
        if any(_is_duplicate_candidate(candidate, existing, overlap_threshold=0.4) for existing in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: _time_to_seconds(item["start_time"]))


def score_comedy_candidate(candidate: dict, judge: dict) -> dict:
    humor = _score_value(judge.get("humor_score"), candidate.get("humor_score"), default=50)
    interaction = _score_value(
        judge.get("interaction_reaction_score"),
        candidate.get("interaction_reaction_score"),
        default=50,
    )
    completeness = _score_value(
        judge.get("completeness_score"),
        candidate.get("completeness_score"),
        default=50,
    )
    hook = _score_value(judge.get("hook_score"), candidate.get("hook_score"), default=50)
    novelty = _score_value(judge.get("novelty_score"), candidate.get("novelty_score"), default=50)
    title = _score_value(judge.get("title_score"), candidate.get("title_score"), default=50)
    text_score = round(
        humor * 0.30
        + interaction * 0.20
        + completeness * 0.20
        + hook * 0.10
        + novelty * 0.10
        + title * 0.10,
        1,
    )
    audio = candidate.get("audio_evidence") or {}
    audio_available = bool(audio.get("available"))
    audio_score = _score_value(audio.get("score"), default=0)
    weighted_score = text_score * 0.75 + audio_score * 0.25
    # 音频是辅助加分项：反应信号弱或缺失时，不反向扣减已经成立的文字质量分。
    quality_score = round(max(text_score, weighted_score), 1) if audio_available else text_score

    hard_gate_passed = humor >= HUMOR_HARD_GATE and completeness >= COMPLETENESS_HARD_GATE
    if quality_score >= QUALITY_A_THRESHOLD and hard_gate_passed:
        tier = "A"
        rejection_reason = ""
    elif quality_score >= QUALITY_B_THRESHOLD:
        tier = "B"
        rejection_reason = str(judge.get("rejection_reason") or "未同时达到笑点闭环、完整度和 A 级总分门槛")
    else:
        tier = "C"
        rejection_reason = str(judge.get("rejection_reason") or "综合质量分低于候选门槛")

    evidence = {
        "why_selected": str(judge.get("why_selected") or candidate.get("highlight_reason") or ""),
        "arc_structure": str(judge.get("arc_structure") or candidate.get("arc_structure") or ""),
        "score_breakdown": {
            "humor": humor,
            "interaction_reaction": interaction,
            "completeness": completeness,
            "hook": hook,
            "novelty": novelty,
            "title": title,
            "text_quality": text_score,
            "audio_reaction": audio_score,
            "final": quality_score,
        },
        "audio": audio,
    }
    return {
        **candidate,
        "title": str(judge.get("title") or candidate.get("title") or "综艺笑点候选")[:160],
        "topic_key": str(judge.get("topic_key") or candidate.get("topic_key") or "")[:120],
        "humor_score": humor,
        "interaction_reaction_score": interaction,
        "completeness_score": completeness,
        "hook_score": hook,
        "novelty_score": novelty,
        "title_score": title,
        "text_quality_score": text_score,
        "audio_reaction_score": audio_score,
        "quality_score": quality_score,
        "quality_tier": tier,
        "quality_evidence": evidence,
        "rejection_reason": rejection_reason,
        "selected_by_default": False,
    }


def dedupe_scored_candidates(candidates: list[dict]) -> list[dict]:
    selected: list[dict] = []
    for candidate in sorted(candidates, key=lambda item: item["quality_score"], reverse=True):
        if any(_is_duplicate_candidate(candidate, existing, overlap_threshold=0.3) for existing in selected):
            continue
        selected.append(candidate)
    return selected


def _recall_moments(
    provider: AIProvider,
    windows: list[ComedyTranscriptWindow],
    preference: str,
    *,
    task_id: str,
    input_fingerprint: str,
) -> tuple[list[dict], list[str], dict[str, int]]:
    moments: list[dict] = []
    failures = []
    completed_units = 0
    failed_units = 0
    empty_units = 0
    invalid_item_count = 0
    for window in windows:
        prompt = _recall_prompt(window, preference)
        execution = execute_checkpointed_ai_unit(
            task_id=task_id,
            namespace="variety_recall",
            input_fingerprint=input_fingerprint,
            unit_id=f"window_{window.index:03d}",
            operation=lambda prompt=prompt: _generate_payload(provider, prompt, expected_key="moments"),
        )
        if execution.status != "completed" or not isinstance(execution.payload, dict):
            failed_units += 1
            failures.append(
                f"召回窗口 {window.index}/{window.total} 跳过："
                f"{execution.error or execution.status}"
            )
            continue
        try:
            payload = execution.payload
            raw_moments = payload.get("moments")
            if not isinstance(raw_moments, list):
                raise AIAnalysisError("moments 不是数组")
            completed_units += 1
            if not raw_moments:
                empty_units += 1
            for index, item in enumerate(raw_moments[:RECALL_LIMIT_PER_WINDOW], start=1):
                if not isinstance(item, dict):
                    invalid_item_count += 1
                    failures.append(f"召回窗口 {window.index}/{window.total} 第 {index} 条不是对象")
                    continue
                key_text = _first_time(item, ("key_time", "key_moment_time", "moment_time", "start_time"))
                if not key_text:
                    invalid_item_count += 1
                    failures.append(f"召回窗口 {window.index}/{window.total} 第 {index} 条缺少关键时间")
                    continue
                key_seconds = _time_to_seconds(key_text)
                if key_seconds < window.start_seconds or key_seconds > window.end_seconds:
                    invalid_item_count += 1
                    failures.append(f"召回窗口 {window.index}/{window.total} 第 {index} 条关键时间越界")
                    continue
                moments.append(
                    {
                        "source_id": f"w{window.index:03d}_m{index:02d}",
                        "key_time": _seconds_to_time(key_seconds),
                        "key_seconds": key_seconds,
                        "title": str(item.get("title") or item.get("hook") or "综艺笑点")[:160],
                        "topic_key": str(item.get("topic_key") or item.get("title") or "")[:120],
                        "humor_reason": str(item.get("humor_reason") or item.get("reason") or "")[:1000],
                        "recall_score": _score_value(item.get("recall_score"), default=60),
                    }
                )
        except Exception as exc:
            failed_units += 1
            failures.append(f"召回窗口 {window.index}/{window.total} 跳过：{exc}")
    return moments, failures, {
        "expected_units": len(windows),
        "completed_units": completed_units,
        "failed_units": failed_units,
        "empty_unit_count": empty_units,
        "invalid_item_count": invalid_item_count,
    }


def _expand_moments(
    provider: AIProvider,
    rows: list[TranscriptRow],
    moments: list[dict],
    preference: str,
    *,
    provider_name: str,
    task_id: str,
    input_fingerprint: str,
) -> tuple[list[dict], list[str], dict[str, int]]:
    expanded = []
    failures = []
    batch_size = 1 if provider_name == "local" else EXPANSION_BATCH_SIZE_REMOTE
    expected_units = math.ceil(len(moments) / batch_size) if moments else 0
    completed_units = 0
    failed_units = 0
    empty_units = 0
    invalid_item_count = 0
    for offset in range(0, len(moments), batch_size):
        batch = moments[offset : offset + batch_size]
        contexts = []
        context_rows_by_id: dict[str, list[TranscriptRow]] = {}
        for moment in batch:
            context_rows = _rows_in_range(
                rows,
                max(rows[0].start_seconds, int(moment["key_seconds"]) - 120),
                min(rows[-1].end_seconds, int(moment["key_seconds"]) + 150),
            )
            context_rows_by_id[moment["source_id"]] = context_rows
            contexts.append(
                {
                    "source_id": moment["source_id"],
                    "key_time": moment["key_time"],
                    "title": moment["title"],
                    "reason": moment["humor_reason"],
                    "transcript": "\n".join(_format_row(row) for row in context_rows),
                }
            )
        prompt = _expansion_prompt(contexts, preference)
        batch_number = offset // batch_size + 1
        execution = execute_checkpointed_ai_unit(
            task_id=task_id,
            namespace="variety_expansion",
            input_fingerprint=input_fingerprint,
            unit_id=f"batch_{batch_number:03d}",
            operation=lambda prompt=prompt: _generate_payload(provider, prompt, expected_key="clips"),
        )
        if execution.status != "completed" or not isinstance(execution.payload, dict):
            failed_units += 1
            failures.append(
                f"上下文扩展批次 {batch_number} 跳过：{execution.error or execution.status}"
            )
            continue
        try:
            payload = execution.payload
            raw_clips = payload.get("clips")
            if not isinstance(raw_clips, list):
                raise AIAnalysisError("clips 不是数组")
            completed_units += 1
            if not raw_clips:
                empty_units += 1
            for item_index, item in enumerate(raw_clips, start=1):
                if not isinstance(item, dict):
                    invalid_item_count += 1
                    failures.append(f"上下文扩展批次 {offset // batch_size + 1} 第 {item_index} 条不是对象")
                    continue
                source_id = str(item.get("source_id") or "")
                moment = next((value for value in batch if value["source_id"] == source_id), None)
                context_rows = context_rows_by_id.get(source_id) or []
                if not moment or not context_rows:
                    invalid_item_count += 1
                    failures.append(f"上下文扩展批次 {offset // batch_size + 1} 第 {item_index} 条 source_id 无效")
                    continue
                start_text = _first_time(item, ("start_time",))
                end_text = _first_time(item, ("end_time",))
                key_text = _first_time(item, ("key_moment_time", "key_time")) or moment["key_time"]
                if not start_text or not end_text:
                    invalid_item_count += 1
                    failures.append(f"上下文扩展批次 {offset // batch_size + 1} 第 {item_index} 条缺少起止时间")
                    continue
                bounds = normalize_clip_bounds(
                    _time_to_seconds(start_text),
                    _time_to_seconds(end_text),
                    _time_to_seconds(key_text),
                    context_rows,
                )
                if not bounds:
                    invalid_item_count += 1
                    failures.append(f"上下文扩展批次 {offset // batch_size + 1} 第 {item_index} 条时间范围无效")
                    continue
                start_seconds, end_seconds = bounds
                key_seconds = min(end_seconds - 1, max(start_seconds, _time_to_seconds(key_text)))
                expanded.append(
                    {
                        "source_id": source_id,
                        "title": str(item.get("title") or moment["title"])[:160],
                        "start_time": _seconds_to_time(start_seconds),
                        "end_time": _seconds_to_time(end_seconds),
                        "duration_seconds": end_seconds - start_seconds,
                        "key_moment_time": _seconds_to_time(key_seconds),
                        "topic_key": str(item.get("topic_key") or moment["topic_key"])[:120],
                        "summary": str(item.get("summary") or moment["humor_reason"] or moment["title"])[:1000],
                        "highlight_reason": str(item.get("highlight_reason") or moment["humor_reason"] or "")[:1000],
                        "arc_structure": str(item.get("arc_structure") or "")[:1000],
                        "suggested_editing": str(item.get("suggested_editing") or "保留铺垫、笑点和笑点后的反应，压缩无关停顿。")[:1000],
                        "humor_score": _score_value(item.get("humor_score"), default=60),
                        "interaction_reaction_score": _score_value(item.get("interaction_reaction_score"), default=60),
                        "completeness_score": _score_value(item.get("completeness_score"), default=60),
                        "hook_score": _score_value(item.get("hook_score"), default=55),
                        "novelty_score": _score_value(item.get("novelty_score"), default=55),
                        "title_score": _score_value(item.get("title_score"), default=55),
                    }
                )
        except Exception as exc:
            failed_units += 1
            failures.append(f"上下文扩展批次 {offset // batch_size + 1} 跳过：{exc}")
    return expanded, failures, {
        "expected_units": expected_units,
        "completed_units": completed_units,
        "failed_units": failed_units,
        "empty_unit_count": empty_units,
        "invalid_item_count": invalid_item_count,
    }


def _global_judge(
    provider: AIProvider,
    candidates: list[dict],
    preference: str,
    feedback: list[dict],
    *,
    task_id: str,
    input_fingerprint: str,
) -> tuple[dict[str, dict], str]:
    prompt_candidates = []
    for item in candidates:
        prompt_candidates.append(
            {
                "source_id": item["source_id"],
                "title": item["title"],
                "time_range": f"{item['start_time']}-{item['end_time']}",
                "duration_seconds": item["duration_seconds"],
                "topic_key": item["topic_key"],
                "summary": item["summary"],
                "highlight_reason": item["highlight_reason"],
                "arc_structure": item["arc_structure"],
                "audio_reaction": item.get("audio_evidence") or {},
            }
        )
    prompt = _judge_prompt(prompt_candidates, preference, feedback)
    execution = execute_checkpointed_ai_unit(
        task_id=task_id,
        namespace="variety_global_judge",
        input_fingerprint=input_fingerprint,
        unit_id="judge_001",
        operation=lambda: _generate_payload(provider, prompt, expected_key="ranked_clips"),
    )
    if execution.status != "completed" or not isinstance(execution.payload, dict):
        return {}, f"全局评审结果不确定，已锁定自动切片：{execution.error or execution.status}"
    try:
        payload = execution.payload
        raw_items = payload.get("ranked_clips")
        if not isinstance(raw_items, list):
            raise AIAnalysisError("ranked_clips 不是数组")
        known_ids = {str(item["source_id"]) for item in candidates}
        valid_items = [
            item
            for item in raw_items
            if isinstance(item, dict) and str(item.get("source_id") or "") in known_ids
        ]
        judged = {str(item["source_id"]): item for item in valid_items}
        invalid_count = len(raw_items) - len(valid_items)
        duplicate_count = len(valid_items) - len(judged)
        missing_ids = sorted(known_ids - set(judged))
        if not judged:
            raise AIAnalysisError("全局评审没有返回任何可对应的 source_id")
        issues = []
        if invalid_count:
            issues.append(f"{invalid_count} 条无效条目")
        if duplicate_count:
            issues.append(f"{duplicate_count} 条重复 source_id")
        if missing_ids:
            issues.append(f"缺少 {len(missing_ids)} 个候选")
        warning = f"全局评审{'、'.join(issues)}，已锁定自动切片" if issues else ""
        return judged, warning
    except Exception as exc:
        return {}, f"全局评审调用失败，已使用扩展阶段评分降级：{exc}"


def _to_clip_payload(item: dict, index: int) -> dict:
    start_seconds = _time_to_seconds(item["start_time"])
    key_seconds = _time_to_seconds(item["key_moment_time"])
    duration = int(item["duration_seconds"])
    cover_time = max(0.0, min(duration - 0.001, float(key_seconds - start_seconds)))
    return {
        "clip_id": f"clip_{index:03d}",
        "title": item["title"],
        "start_time": item["start_time"],
        "end_time": item["end_time"],
        "duration_seconds": duration,
        "cover_time_seconds": round(cover_time, 3),
        "summary": item["summary"],
        "highlight_reason": item["highlight_reason"],
        "spread_value": "高" if item["quality_tier"] == "A" else "中",
        "suggested_editing": item["suggested_editing"],
        "confidence_score": round(float(item["quality_score"]) / 100, 4),
        "selected_by_default": bool(item["selected_by_default"]),
        "quality_tier": item["quality_tier"],
        "quality_score": item["quality_score"],
        "text_quality_score": item["text_quality_score"],
        "humor_score": item["humor_score"],
        "completeness_score": item["completeness_score"],
        "audio_reaction_score": item["audio_reaction_score"],
        "topic_key": item["topic_key"],
        "key_moment_time": item["key_moment_time"],
        "quality_evidence": item["quality_evidence"],
        "rejection_reason": item["rejection_reason"],
    }


def _generate_payload(provider: AIProvider, prompt: str, *, expected_key: str) -> dict:
    raw = generate_json_with_safe_retry(provider, prompt)
    payload = _loads_ai_json(raw)
    if not isinstance(payload, dict):
        raise AIAnalysisError("AI 输出必须是 JSON 对象")
    if not isinstance(payload.get(expected_key), list):
        raise AIAnalysisError(f"AI 输出缺少 {expected_key} 数组")
    return payload


def _recall_prompt(window: ComedyTranscriptWindow, preference: str) -> str:
    return f"""你是《康熙来了》笑点召回编辑。现在只做宽召回，不做凑数，不输出完整切片。
从这一个约 5 分钟且与相邻窗口重叠的逐句转写中，找出 0-{RECALL_LIMIT_PER_WINDOW} 个真正可能成立的笑点时刻。
必须有反转、尴尬、意外回答、主持人补刀或明显现场反应；纯八卦、纯身体话题、平铺直叙不算好笑。
{preference}
只输出：{{"moments":[{{"key_time":"HH:MM:SS","title":"短标题","topic_key":"同一故事的稳定短标识","humor_reason":"为什么可能好笑","recall_score":0}}]}}
时间必须来自转写；没有合适内容就返回空数组。

窗口 {window.index}/{window.total}，范围 {_seconds_to_time(window.start_seconds)}-{_seconds_to_time(window.end_seconds)}：
{window.text}"""


def _expansion_prompt(contexts: list[dict], preference: str) -> str:
    return f"""你是综艺短视频剪辑导演。请围绕每个已召回笑点，从各自前后文中形成一条完整片段。
默认 60-150 秒，必须包含必要铺垫、核心笑点/反转、笑点后的追问/补刀/解释/笑声和自然收尾。
不要把同一笑点拆成多条，不要输出只有一句包袱或只有背景信息的片段。
{preference}
只输出严格 JSON：{{"clips":[{{"source_id":"原值","title":"标题","start_time":"HH:MM:SS","end_time":"HH:MM:SS","key_moment_time":"HH:MM:SS","topic_key":"话题标识","summary":"情境与看点","highlight_reason":"具体笑点","arc_structure":"铺垫→笑点→反应→收尾","suggested_editing":"剪辑建议","humor_score":0,"interaction_reaction_score":0,"completeness_score":0,"hook_score":0,"novelty_score":0,"title_score":0}}]}}
所有分数为 0-100，不要虚高；时间必须来自对应转写。

待扩展内容：
{json.dumps(contexts, ensure_ascii=False)}"""


def _judge_prompt(candidates: list[dict], preference: str, feedback: list[dict]) -> str:
    feedback_summary = [
        {
            "decision": item.get("decision"),
            "reason": item.get("reason_code"),
            "title": item.get("title_snapshot"),
            "note": item.get("note"),
        }
        for item in feedback
    ]
    return f"""你是《康熙来了》短视频总编。请把所有候选放在一起横向比较，重点淘汰“不够好笑但话题看似刺激”的内容。
同一故事、相邻时间或同一笑点只能保留最完整的一条。音频信号只是辅助证据，不能弥补笑点闭环和完整度不足。
{preference}
参考用户近期审片反馈：{json.dumps(feedback_summary, ensure_ascii=False)}

只输出严格 JSON：{{"ranked_clips":[{{"source_id":"原值","title":"可优化标题","topic_key":"统一后的话题标识","humor_score":0,"interaction_reaction_score":0,"completeness_score":0,"hook_score":0,"novelty_score":0,"title_score":0,"arc_structure":"铺垫→笑点→反应→收尾","why_selected":"为什么值得发","rejection_reason":"若不足则说明原因"}}]}}
所有候选都要返回，所有分数为 0-100，不要虚高。

候选：{json.dumps(candidates, ensure_ascii=False)}"""


def _preference_summary(prompt_template: str, ai_preference: str) -> str:
    prompt = (prompt_template or "").replace("{{AI_PREFERENCE}}", ai_preference or "")
    for marker in ("# Output Format", "【输出格式】", "输出 JSON", "转写文本：", "# Transcript", "{{TRANSCRIPT_TEXT}}"):
        if marker in prompt:
            prompt = prompt.split(marker, 1)[0]
    prompt = " ".join(prompt.split())[:2500]
    extra = " ".join((ai_preference or "").split())[:500]
    parts = []
    if prompt:
        parts.append(f"本任务既有选片偏好：{prompt}")
    if extra and extra not in prompt:
        parts.append(f"用户补充偏好：{extra}")
    return "\n".join(parts)


def _rows_in_range(rows: list[TranscriptRow], start_seconds: int, end_seconds: int) -> list[TranscriptRow]:
    return [row for row in rows if row.end_seconds >= start_seconds and row.start_seconds <= end_seconds]


def _format_row(row: TranscriptRow) -> str:
    return f"{row.start_time} - {row.end_time} {row.text}"


def _first_time(item: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(item.get(key) or "").strip()
        if re.fullmatch(r"(?:\d{2}:)?\d{2}:\d{2}", value):
            return value
    return ""


def _nearest_boundary(seconds: int, rows: list[TranscriptRow], *, use_start: bool) -> int:
    values = [row.start_seconds if use_start else row.end_seconds for row in rows]
    return min(values, key=lambda value: abs(value - seconds))


def _expand_bounds_to_min_duration(
    start_seconds: int,
    end_seconds: int,
    rows: list[TranscriptRow],
    minimum_seconds: int,
) -> tuple[int, int]:
    if end_seconds - start_seconds >= minimum_seconds:
        return start_seconds, end_seconds

    lower = rows[0].start_seconds
    upper = rows[-1].end_seconds
    missing = minimum_seconds - (end_seconds - start_seconds)
    desired_start = max(lower, start_seconds - (missing // 2 + missing % 2))
    desired_end = min(upper, end_seconds + missing // 2)
    start_boundaries = sorted({row.start_seconds for row in rows})
    end_boundaries = sorted({row.end_seconds for row in rows})
    start_seconds = max((value for value in start_boundaries if value <= desired_start), default=lower)
    end_seconds = min((value for value in end_boundaries if value >= desired_end), default=upper)

    if end_seconds - start_seconds < minimum_seconds:
        desired_end = min(upper, start_seconds + minimum_seconds)
        end_seconds = min((value for value in end_boundaries if value >= desired_end), default=upper)
    if end_seconds - start_seconds < minimum_seconds:
        desired_start = max(lower, end_seconds - minimum_seconds)
        start_seconds = max((value for value in start_boundaries if value <= desired_start), default=lower)
    return start_seconds, end_seconds


def _score_value(*values: Any, default: float) -> float:
    for value in values:
        if value is None or value == "":
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if 0 <= number <= 1:
            number *= 100
        return round(max(0.0, min(100.0, number)), 1)
    return float(default)


def _normalize_topic(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "").lower())[:80]


def _is_duplicate_candidate(first: dict, second: dict, *, overlap_threshold: float) -> bool:
    first_start = _time_to_seconds(first["start_time"])
    first_end = _time_to_seconds(first["end_time"])
    second_start = _time_to_seconds(second["start_time"])
    second_end = _time_to_seconds(second["end_time"])
    overlap = max(0, min(first_end, second_end) - max(first_start, second_start))
    shorter = max(1, min(first_end - first_start, second_end - second_start))
    if overlap / shorter >= overlap_threshold:
        return True
    first_topic = _normalize_topic(first.get("topic_key") or first.get("title") or "")
    second_topic = _normalize_topic(second.get("topic_key") or second.get("title") or "")
    gap = max(0, max(first_start, second_start) - min(first_end, second_end))
    return bool(first_topic and first_topic == second_topic and gap <= 90)


__all__ = [
    "ComedyAnalysisRequest",
    "analyze_variety_comedy",
    "build_comedy_windows",
    "dedupe_expanded_candidates",
    "dedupe_recall_moments",
    "dedupe_scored_candidates",
    "normalize_clip_bounds",
    "score_comedy_candidate",
]
