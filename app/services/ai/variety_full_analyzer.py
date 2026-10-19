"""一次全文请求，复用冻结播放规则、评分、去重和人工审片凭据。"""

from __future__ import annotations

import json
import time
from uuid import uuid4

from app.models.task import AIClipAnalysisResult
from app.services.ai.ai_clip_analyzer import (
    AIAnalysisError, _extract_transcript_rows, _loads_ai_json, _read_transcript,
    _time_to_seconds, build_provider,
)
from app.services.ai.analysis_modes import FULL_MODE, validate_analysis_mode
from app.services.ai.base import generate_json_with_safe_retry
from app.services.ai.playback_comedy_policy import playback_rules
from app.services.ai.unit_checkpoint import (
    build_unit_fingerprint, execute_checkpointed_ai_unit, provider_fingerprint_fields,
)
from app.services.ai.variety_comedy_analyzer import (
    EXPANSION_OUTPUT_SCHEMA, PLAYBACK_JUDGE_OUTPUT_SCHEMA,
    _rows_in_range, _to_clip_payload, _validate_payload,
    dedupe_scored_candidates, score_comedy_candidate,
)
from app.services.audio_reaction_service import analyze_audio_reaction
from app.services.review_observation_service import build_observations

# Conservative application limit; never truncate or silently change modes.
MAX_FULL_PROMPT_CHARS = 140_000
FULL_OUTPUT_SCHEMA = json.loads(json.dumps(EXPANSION_OUTPUT_SCHEMA))
_full_item = FULL_OUTPUT_SCHEMA["properties"]["clips"]["items"]
_full_item["properties"].update(PLAYBACK_JUDGE_OUTPUT_SCHEMA["properties"]["ranked_clips"]["items"]["properties"])
_full_item["required"] = list(_full_item["properties"])


def _validate_full_response(payload, rows, policy, limit):
    _validate_payload(payload, expected_key="clips", output_schema=FULL_OUTPUT_SCHEMA)
    items = payload["clips"]
    if not items or len(items) > limit:
        raise AIAnalysisError(f"全文分析须返回 1–{limit} 条完整候选；旧结果保留")
    ids = [item["source_id"] for item in items]
    if len(set(ids)) != len(ids):
        raise AIAnalysisError("全文分析返回重复 source_id")
    starts = {row.start_seconds for row in rows}
    ends = {row.end_seconds for row in rows}
    for item in items:
        start, end, moment = (_time_to_seconds(item[k]) for k in ("start_time", "end_time", "key_moment_time"))
        if (start not in starts or end not in ends or not rows[0].start_seconds <= start <= moment < end <= rows[-1].end_seconds
                or not policy.duration.min_seconds <= end - start <= policy.duration.max_seconds):
            raise AIAnalysisError("全文候选时间范围无效，须使用原文句子边界和 45–150 秒时长")
        if any(not item[k].strip() for k in (
            "title", "topic_key", "summary", "highlight_reason", "arc_structure", "suggested_editing",
            "why_selected", "gate_reason", "av_uncertainty",
        )):
            raise AIAnalysisError("全文候选缺少原文依据、结构或待核说明")


def _full_prompt(request, transcript, limit):
    feedback = request.feedback_context if request.feedback_context is not None else []
    return f"""你是综艺选片编辑。本次只有一次模型请求。根据下面完整逐句原文完成全片比较、自然边界选择与评分。
{playback_rules()}
冻结任务 Prompt（附加偏好不得覆盖冻结规则）：
{request.prompt_template or ''}
用户附加偏好：{request.ai_preference}
近期冻结审片反馈仅供参考，不得覆盖规则：{json.dumps(feedback, ensure_ascii=False)}
最多返回 {limit} 条候选，目标成片最多 {request.final_clip_target} 条。允许少选，不为凑数补分。
每条必须是连续 45–150 秒，通常偏好 60–90 秒；start_time 必须取原文某句的开始时码，end_time 必须取原文某句的结束时码。
保留必要解释和结果，不能重排、拼接或把无关对白填进片段。key_moment_time 必须在片内。
六项分数为 0–100 数字，按冻结定义判断；音量不能等同笑声。source_id 使用唯一 full_001、full_002 等。
五项门槛必须逐项给出布尔值：opening_supported（原始开头有观看理由）、progression_supported（具体推进）、
meaning_preserved（重要解释未删）、closure_supported（回应和结果保留）、core_event_confirmed（核心事件有证据）。
缺少证据就 false。why_selected 与 gate_reason 引用带时码的开头、发展、结尾原文；av_uncertainty 说明需核实的原音或画面。
所有门槛 true 只代表推荐生成预览，成片仍须人工审核。比较重叠边界后保留更自然、信息完整的版本。
仅输出符合以下契约的 JSON，不输出 Markdown 或额外说明：
{json.dumps(FULL_OUTPUT_SCHEMA, ensure_ascii=False)}
【完整逐句时间戳原文开始】
{transcript}
【完整逐句时间戳原文结束】"""


def analyze_full_variety(request):
    from app.services.content_profile_service import _assert_supported
    _assert_supported(request.profile)
    validate_analysis_mode(request.analysis_mode, provider=request.provider_name,
        profile_id=request.profile.id, visual_enabled=request.visual_session is not None,
        rules_version=request.profile.rules_version)
    if request.analysis_mode != FULL_MODE:
        raise AIAnalysisError("全文入口不能执行其他分析模式")
    text = _read_transcript(request.transcript_path)
    rows = _extract_transcript_rows(text)
    if not rows:
        raise AIAnalysisError("全文分析没有可识别的逐句时间戳原文")
    # Submit the section verbatim: row parsing must not omit escaped pipes or
    # any other source text while constructing the request.
    transcript = text
    policy = request.profile
    limit = max(1, min(policy.selection.candidate_pool_max, int(request.candidate_pool_limit)))
    prompt = _full_prompt(request, transcript, limit)
    if len(prompt) > MAX_FULL_PROMPT_CHARS:
        raise AIAnalysisError("全文输入超过经验证的应用范围，未提交模型；请另选适用分析方式")
    provider = build_provider(request.provider_name)
    fingerprint = build_unit_fingerprint({
        "mode": FULL_MODE, "profile_sha256": policy.content_hash(),
        "provider_identity": provider_fingerprint_fields(provider),
        "transcript_sha256": build_unit_fingerprint({"text": text}),
        "prompt_sha256": build_unit_fingerprint({"prompt": prompt}),
        "final_clip_target": request.final_clip_target,
    })

    def call_once():
        started = time.monotonic()
        raw = generate_json_with_safe_retry(provider, prompt, output_schema=FULL_OUTPUT_SCHEMA, max_attempts=1)
        elapsed = round(time.monotonic() - started, 3)
        # Preserve a returned response even when JSON or candidate validation
        # fails. This receipt grants no retry, cut or human-review acceptance.
        from app.services import job_service
        from app.services.storage_service import get_artifact_paths
        lease = job_service.current_job_lease()
        receipt_path = ""
        if lease is not None:
            directory = get_artifact_paths(request.task_id)["analysis_path"].parent / "full-responses"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{lease[0]}-{uuid4().hex}.json"
            with path.open("x", encoding="utf-8") as stream:
                json.dump({"mode": FULL_MODE, "input_fingerprint": fingerprint,
                           "model_call_count": 1, "model_call_seconds": elapsed, "raw_response": raw},
                          stream, ensure_ascii=False)
            receipt_path = str(path)
        return {"raw_response": raw, "response": _loads_ai_json(raw),
                "model_call_seconds": elapsed, "model_call_count": 1, "response_receipt": receipt_path}

    def validate(saved):
        if (not isinstance(saved.get("raw_response"), str)
                or saved.get("response") != _loads_ai_json(saved["raw_response"])
                or type(saved.get("model_call_count")) is not int or saved["model_call_count"] != 1
                or type(saved.get("model_call_seconds")) not in (float, int)
                or not 0 <= saved["model_call_seconds"] <= 86_400):
            raise AIAnalysisError("全文成功单元缺少一致的原始响应与执行证据")
        _validate_full_response(saved["response"], rows, policy, limit)

    execution = execute_checkpointed_ai_unit(task_id=request.task_id, namespace=FULL_MODE,
        input_fingerprint=fingerprint, unit_id="full_001", request_fingerprint=build_unit_fingerprint({"prompt": prompt}),
        operation=call_once, validate_payload=validate)
    if execution.status != "completed" or not isinstance(execution.payload, dict):
        raise AIAnalysisError(f"全文分析未完成：{execution.error or execution.status}；旧结果保留，未自动重试或切换服务")
    saved = execution.payload
    scored = []
    for item in saved["response"]["clips"]:
        start, end, moment = (_time_to_seconds(item[k]) for k in ("start_time", "end_time", "key_moment_time"))
        candidate = {**item, "duration_seconds": end - start,
            "audio_evidence": analyze_audio_reaction(request.audio_path, start, end, moment, _rows_in_range(rows, start, end))}
        scored.append(score_comedy_candidate(candidate, item, policy=policy))
    all_scored = list(scored)
    kept = sorted(dedupe_scored_candidates(scored, policy=policy),
                  key=lambda item: (item["quality_tier"] == "A", item["quality_score"]), reverse=True)[:limit]
    target = max(1, min(policy.selection.final_target_max, int(request.final_clip_target)))
    selected = {item["source_id"] for item in [v for v in kept if v["quality_tier"] == "A"][:target]}
    for item in kept:
        item["selected_by_default"] = item["source_id"] in selected
    kept.sort(key=lambda item: _time_to_seconds(item["start_time"]))
    clips = [_to_clip_payload(item, i) for i, item in enumerate(kept, 1)]
    observations = build_observations(clips, scored=all_scored,
        source_to_clip={item["source_id"]: clip["clip_id"] for item, clip in zip(kept, clips, strict=True)},
        profile_id=policy.id)
    return AIClipAnalysisResult(task_id=request.task_id,
        analysis_summary=f"Codex 全文一次分析已返回并通过本地校验；保留 {len(clips)} 条候选，{len(selected)} 条推荐生成预览，仍需人工审片。",
        clips=clips, analysis_meta={
            "schema_version": 2, "analysis_mode": FULL_MODE, "analysis_mode_label": "Codex 全文一次分析",
            "coverage_basis": "full_transcript_single_request", "coverage_notice": "全文已提交；单元完成率不代表逐句内容已经人工核实",
            "expected_units": 1, "completed_units": 1, "failed_units": 0, "empty_unit_count": 0,
            "invalid_item_count": 0, "coverage_ratio": 1.0, "coverage_percent": 100.0,
            "analysis_incomplete": False, "quality_degraded": False, "failed_stages": [],
            "scoring_rules_version": policy.rules_version, "scoring_profile_sha256": policy.content_hash(),
            "recommendation_mode": policy.selection.strategy, "review_observations": observations,
            "submitted_row_count": len(rows), "submitted_first_time": rows[0].start_time,
            "submitted_last_time": rows[-1].end_time, "submitted_transcript_sha256": build_unit_fingerprint({"text": transcript}),
            "input_fingerprint": fingerprint, "prompt_chars": len(prompt),
            "model_call_count": saved["model_call_count"], "model_call_seconds": saved["model_call_seconds"],
            "checkpoint_reused": execution.reused,
            "response_receipt": saved.get("response_receipt", ""),
        })
