"""Lightweight initial AI observations, independent of mutable production choices.

This module never calls a model or reads the current candidate table to infer an
old recommendation. Keys are meaningful only within their source analysis Run.
"""

import hashlib
import json
import math
import re


VERSION = "review-observations-v1"


def existing_source_identity(meta):
    """Reuse this Run's already verified visual source, never another task's file."""
    visual = meta.get("visual_signal") or {}
    candidates = (visual.get("candidates") or {}).values()
    hashes = {r.get("source_sha256") for r in candidates if r.get("status") in {"completed", "partial"}}
    if len(hashes) == 1:
        sha = next(iter(hashes))
        if isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha):
            return {"kind": "original_video_sha256", "sha256": sha, "basis": "same_run_visual_sampling"}
    return {"kind": "unknown", "reason": "本批次没有完整原片哈希；音频哈希和任务数不能替代"}


def evidence_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _time(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        seconds = int(value)
        return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    return str(value or "")


def _observation(item, *, key, clip_key, recommended, in_pool):
    evidence = item.get("quality_evidence") or {}
    tier = item.get("quality_tier", item.get("tier"))
    tier = tier if tier in {"A", "B", "C"} else None
    score = item.get("quality_score", item.get("score"))
    # Historical model defaults (tier='', score=0) are not measured zero scores.
    score = score if tier and isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score) and 0 <= score <= 100 else None
    value = {
        "key": key, "clip_key": clip_key, "in_review_pool": in_pool,
        "initial_recommended": recommended, "quality_tier": tier, "quality_score": score,
        "title": str(item.get("title") or "")[:160], "summary": str(item.get("summary") or "")[:1000],
        "start_time": _time(item.get("start_time", item.get("start_seconds"))),
        "end_time": _time(item.get("end_time", item.get("end_seconds"))),
        "topic": str(item.get("topic_key", item.get("topic")) or "")[:120],
        "hook_type": str(evidence.get("hook_type", item.get("hook_type")) or "")[:120] or None,
        "score_dimensions": evidence.get("score_breakdown", item.get("scores")) or {},
        "rejection_reason": str(item.get("rejection_reason") or "")[:1000],
        "disposition": "review_pool" if in_pool else "diagnostic_only",
    }
    return {**value, "sha256": evidence_hash(value)}


def build_observations(clips, *, scored=(), source_to_clip=None, profile_id="unknown"):
    source_to_clip = source_to_clip or {}
    observations = []
    for clip in clips:
        clip_key = str(clip.get("clip_id") or "")
        if not clip_key:
            continue
        # The general adapter's model supplies selected_by_default=True even
        # when its provider did not emit a recommendation. Keep that unknown.
        recommended = clip.get("selected_by_default") if profile_id in {
            "variety_comedy", "interview_story", "knowledge_opinion", "long_live_talk"
        } else None
        observations.append(_observation(clip, key=f"clip:{clip_key}", clip_key=clip_key,
                                        recommended=recommended if isinstance(recommended, bool) else None, in_pool=True))
    seen = set()
    for item in scored:
        source = str(item.get("source_id") or "")
        if not source or source in seen or source in source_to_clip:
            continue
        seen.add(source)
        observations.append(_observation(item, key=f"source:{source}", clip_key=None,
                                        recommended=False, in_pool=False))
    return {"version": VERSION, "items": observations, "diagnostics_recorded": bool(scored)}


def read_observations(payload):
    meta = payload.get("analysis_meta") or {}
    frozen = meta.get("review_observations")
    if isinstance(frozen, dict) and frozen.get("version") == VERSION:
        rows = frozen.get("items") or []
        valid = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = {k: v for k, v in row.items() if k != "sha256"}
            try:
                if row.get("sha256") == evidence_hash(value):
                    valid.append(row)
            except (ValueError, TypeError):
                continue
        keys = [r.get("key") for r in valid]
        unique = [r for r in valid if keys.count(r.get("key")) == 1]
        return unique, "frozen_v1" if len(unique) == len(rows) else "invalid_observations"
    # Read-only interpretation of actual historical JSON; never backfill a Run.
    clips = payload.get("clips") or []
    scored, mapping = [], {}
    # The pre-v2.5.5 shared analyzer already persisted full scored observations.
    # Its expansion rejections have no validated clip range and remain excluded.
    if meta.get("schema_version") == 2 and meta.get("selection_profile") in {"interview_story", "knowledge_opinion"}:
        for clip in clips:
            source = (clip.get("quality_evidence") or {}).get("source_id")
            if source:
                mapping[source] = str(clip.get("clip_id") or "")
        for item in meta.get("observations") or []:
            if not isinstance(item, dict) or not item.get("source_id") or item.get("tier") not in {"A", "B", "C"}:
                continue
            start, end = item.get("start_seconds"), item.get("end_seconds")
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end:
                continue
            # Without complete source mapping, only C is provably absent from
            # the old production pool. Do not double-count an unmapped A/B.
            if item["tier"] == "C" or len(mapping) == len(clips):
                scored.append(item)
    rows = build_observations(clips, scored=scored, source_to_clip=mapping, profile_id=meta.get("selection_profile"))["items"]
    keys = [r["key"] for r in rows]
    unique = [r for r in rows if keys.count(r["key"]) == 1]
    return unique, "legacy_payload" if len(unique) == len(rows) else "invalid_observations"
