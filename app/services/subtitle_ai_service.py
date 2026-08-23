"""字幕 AI 纠错建议：只生成非 active 文字建议 revision。"""

from __future__ import annotations

import json
from typing import Any

from app.services.ai.ai_clip_analyzer import build_provider, loads_ai_json
from app.services.subtitle_data_service import (
    create_suggestion_revision,
    get_revision,
    get_suggestion_diff,
    get_track,
)


AI_BATCH_SIZE = 50
ALLOWED_SUGGESTION_KEYS = {"cue_id", "suggested_text", "reason"}


def generate_subtitle_suggestions(
    track_id: str,
    *,
    revision_id: str,
    cue_ids: list[str],
    instructions: str = "",
    provider: Any | None = None,
) -> dict[str, Any]:
    track = get_track(track_id)
    if track.get("active_revision_id") != revision_id:
        raise ValueError("当前字幕版本已经变化，请刷新后重新生成 AI 建议")
    revision = get_revision(revision_id, include_cues=True)
    if revision["track_id"] != track_id:
        raise ValueError("revision 不属于当前字幕轨")
    selected_ids = list(dict.fromkeys(str(value) for value in cue_ids if str(value)))
    if not selected_ids:
        raise ValueError("请先勾选需要 AI 纠错的字幕")
    if len(selected_ids) > 500:
        raise ValueError("单次最多选择 500 条字幕")
    cue_map = {str(cue["id"]): cue for cue in revision["cues"]}
    if not set(selected_ids) <= cue_map.keys():
        raise ValueError("选中的 cue 不属于当前 revision")

    resolved_provider = provider or build_provider()
    suggestions: dict[str, str] = {}
    for offset in range(0, len(selected_ids), AI_BATCH_SIZE):
        batch_ids = selected_ids[offset : offset + AI_BATCH_SIZE]
        prompt = _build_prompt([cue_map[cue_id] for cue_id in batch_ids], instructions)
        payload = loads_ai_json(resolved_provider.generate_json(prompt))
        suggestions.update(_validate_suggestion_payload(payload, allowed_ids=set(batch_ids)))

    suggestion_revision = create_suggestion_revision(
        track_id,
        base_revision_id=revision_id,
        suggested_text_by_cue_id=suggestions,
        note=f"AI 字幕纠错建议 · {getattr(resolved_provider, 'name', 'AI')}",
    )
    return {
        "revision": suggestion_revision,
        "base_revision_id": revision_id,
        "provider": getattr(resolved_provider, "name", "AI"),
        "diff": get_suggestion_diff(track_id, suggestion_revision["id"]),
    }


def _build_prompt(cues: list[dict[str, Any]], instructions: str) -> str:
    compact = [
        {
            "cue_id": str(cue["id"]),
            "text": str(cue.get("text") or ""),
        }
        for cue in cues
    ]
    extra = instructions.strip() or "修正明显错别字、同音误识别、标点和便于阅读的 cue 内换行"
    return (
        "你是中文视频字幕校对员。只输出严格 JSON，不要解释。\n"
        "你只能修改 suggested_text 的文字、标点和 cue 内换行；禁止增删 cue，禁止修改时间、说话人或 cue_id。\n"
        "没有必要修改的 cue 也必须原样返回。换行请直接使用 JSON 字符串中的 \\n。\n"
        f"额外要求：{extra}\n"
        "输出结构：{\"suggestions\":[{\"cue_id\":\"...\",\"suggested_text\":\"...\",\"reason\":\"简短原因\"}]}\n"
        f"字幕：{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}"
    )


def _validate_suggestion_payload(payload: Any, *, allowed_ids: set[str]) -> dict[str, str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("suggestions"), list):
        raise ValueError("AI 字幕建议缺少 suggestions 数组")
    resolved: dict[str, str] = {}
    for item in payload["suggestions"]:
        if not isinstance(item, dict):
            raise ValueError("AI 字幕建议条目格式无效")
        forbidden = set(item) - ALLOWED_SUGGESTION_KEYS
        if forbidden:
            raise ValueError(f"AI 字幕建议包含禁止字段：{', '.join(sorted(forbidden))}")
        cue_id = str(item.get("cue_id") or "")
        if cue_id not in allowed_ids or cue_id in resolved:
            raise ValueError("AI 字幕建议包含未知或重复 cue_id")
        text = str(item.get("suggested_text") or "").strip()
        if not text or len(text) > 4000:
            raise ValueError("AI 字幕建议文字为空或过长")
        resolved[cue_id] = text
    if set(resolved) != allowed_ids:
        raise ValueError("AI 字幕建议没有完整返回所选 cue")
    return resolved
