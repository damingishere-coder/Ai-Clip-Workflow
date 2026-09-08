"""按内容选片：容量只决定批次，AI 决定取舍，程序只核验证据和格式。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Lock

from app.models.task import AIClipAnalysisResult
from app.services.ai.ai_clip_analyzer import (
    AIAnalysisError,
    _extract_transcript_rows,
    _loads_ai_json,
    _read_transcript,
    _time_to_seconds,
    build_provider,
)
from app.services.ai.base import generate_json_with_safe_retry
from app.services.ai.unit_checkpoint import (
    build_unit_fingerprint,
    execute_checkpointed_ai_unit,
    provider_fingerprint_fields,
)
from app.services.ai.variety_comedy_analyzer import (
    RECALL_OUTPUT_SCHEMA,
    EXPANSION_OUTPUT_SCHEMA,
    SCORE_FIELDS,
    _preference_summary,
    _format_row,
    _validate_clip_output,
    build_comedy_windows,
)

CONTRACT_VERSION = "kangxi-content-decisions-1"
ROLES = ("opening", "topic", "highlight", "response", "ending")
DECISIONS = ("publish", "review", "reject")
EVIDENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        key: {"type": "string"} for key in ("role", "start_time", "end_time", "quote")
    },
    "required": ["role", "start_time", "end_time", "quote"],
}
_properties = dict(
    EXPANSION_OUTPUT_SCHEMA["properties"]["clips"]["items"]["properties"]
)
_properties.update(
    {
        "decision": {"type": "string", "enum": list(DECISIONS)},
        "decision_reason": {"type": "string"},
        "review_issues": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
        "duplicate_of": {"type": "string"},
    }
)
DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clips"],
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": _properties,
                "required": list(_properties),
            },
        }
    },
}


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2,}:[0-5]\d:[0-5]\d", value):
        raise AIAnalysisError("格式错误：时间必须是 HH:MM:SS")
    return _time_to_seconds(value)


def validate_decisions(payload, contexts, *, final, catalog_ids=()):
    """新响应和复用缓存使用同一校验，绝不修补分数、时间或推断 AI 决定。"""
    if (
        not isinstance(payload, dict)
        or set(payload) != {"clips"}
        or not isinstance(payload["clips"], list)
    ):
        raise AIAnalysisError("格式错误：缺少 clips 数组")
    _validate_clip_output(
        payload, "clips", EXPANSION_OUTPUT_SCHEMA, validate_times=not final
    )
    ids = [item["source_id"] for item in payload["clips"]]
    if len(ids) != len(set(ids)) or set(ids) != set(contexts):
        raise AIAnalysisError("候选编号错误、重复或遗漏，未记为成功")
    for item in payload["clips"]:
        expected = (
            set(_properties)
            if final
            else set(
                EXPANSION_OUTPUT_SCHEMA["properties"]["clips"]["items"]["properties"]
            )
        )
        if set(item) != expected:
            raise AIAnalysisError("格式错误：候选必填字段缺失或包含额外字段")
        for key, limit in {
            "title": 160,
            "topic_key": 120,
            "summary": 1000,
            "highlight_reason": 1000,
            "suggested_editing": 1000,
        }.items():
            if not item[key].strip() or len(item[key]) > limit:
                raise AIAnalysisError(f"格式错误：{key} 为空或超过 {limit} 字符")
        rows = contexts[item["source_id"]]
        start, end = _timestamp(item["start_time"]), _timestamp(item["end_time"])
        moment = _timestamp(item["key_moment_time"])
        if end <= start or not start <= moment <= end:
            raise AIAnalysisError("时间范围无效")
        if not rows or start < rows[0].start_seconds or end > rows[-1].end_seconds:
            raise AIAnalysisError("时间范围越出已提供原文；禁止机械修改边界")
        if not final:
            continue
        decision = item["decision"]
        issues = item["review_issues"]
        if (
            decision not in DECISIONS
            or not isinstance(item["decision_reason"], str)
            or not item["decision_reason"].strip()
        ):
            raise AIAnalysisError("格式错误：需要明确决定和理由")
        if not isinstance(issues, list) or any(
            not isinstance(s, str) or not s.strip() for s in issues
        ):
            raise AIAnalysisError("格式错误：review_issues 必须是问题文本数组")
        duplicate = item["duplicate_of"]
        if not isinstance(duplicate, str) or (
            duplicate
            and (
                duplicate not in catalog_ids
                or duplicate == item["source_id"]
                or decision != "reject"
            )
        ):
            raise AIAnalysisError("重复候选引用无效")
        evidence = item["evidence"]
        if not isinstance(evidence, list):
            raise AIAnalysisError("格式错误：缺少原文证据数组")
        roles = set()
        for point in evidence:
            if not isinstance(point, dict) or set(point) != {
                "role",
                "start_time",
                "end_time",
                "quote",
            }:
                raise AIAnalysisError("格式错误：原文证据字段不完整")
            if (
                point["role"] not in ROLES
                or not isinstance(point["quote"], str)
                or not point["quote"].strip()
            ):
                raise AIAnalysisError("格式错误：证据角色或引用为空")
            matches = _evidence_spans(point, rows)
            if not matches:
                raise AIAnalysisError(
                    f"证据与已提供逐句原文不一致：{point['role']} {point['start_time']}–{point['end_time']}"
                )
            if decision == "publish" and not any(
                start <= first.start_seconds
                and last.end_seconds <= end
                and last.end_seconds > first.start_seconds
                for first, last in matches
            ):
                raise AIAnalysisError("可出片证据跨出最终边界，需重新评审")
            roles.add(point["role"])
        if decision == "review" and not issues:
            raise AIAnalysisError("待审核必须列出待确认问题")
        if decision == "publish":
            if issues or end - start > 150 or roles != set(ROLES) or moment >= end:
                raise AIAnalysisError(
                    "可出片仍有待审核问题、超过 150 秒或缺少五项内容证据"
                )
            if start not in {r.start_seconds for r in rows} or end not in {
                r.end_seconds for r in rows
            }:
                raise AIAnalysisError(
                    "可出片边界必须来自提供的逐句原文，无法对齐时应待审核"
                )


def _evidence_spans(point, rows):
    """一句话可能被字幕拆成连续多行；只归一空白/标点，不补字或修改原始引用。"""

    def normalized(text):
        return re.sub(r"[\s，。！？、；：“”‘’,.!?;:\"'…]+", "", text)

    quote = normalized(point["quote"])
    if not quote:
        return []
    matches = []
    for start_index, first in enumerate(rows):
        if first.start_time != point["start_time"]:
            continue
        parts = []
        for end_index in range(start_index, len(rows)):
            last = rows[end_index]
            parts.append(last.text)
            if last.end_time != point["end_time"]:
                continue
            source = normalized("".join(parts))
            if quote == source or (end_index == start_index and quote in source):
                matches.append((first, last))
    return matches


def _validate_recall(payload, window):
    if (
        not isinstance(payload, dict)
        or set(payload) != {"moments"}
        or not isinstance(payload["moments"], list)
    ):
        raise AIAnalysisError("格式错误：缺少 moments 数组")
    for item in payload["moments"]:
        if not isinstance(item, dict) or set(item) != {
            "key_time",
            "title",
            "topic_key",
            "humor_reason",
            "recall_score",
        }:
            raise AIAnalysisError("格式错误：召回字段缺失")
        moment = _timestamp(item["key_time"])
        score = item["recall_score"]
        if (
            type(score) not in (int, float)
            or not math.isfinite(score)
            or not 0 <= score <= 100
        ):
            raise AIAnalysisError("格式错误：评分必须为 0–100 数字")
        if not window.start_seconds <= moment <= window.end_seconds:
            raise AIAnalysisError("召回时间越出窗口")
        if any(
            not isinstance(item[k], str) or not item[k].strip()
            for k in ("title", "topic_key", "humor_reason")
        ):
            raise AIAnalysisError("格式错误：召回文本不能为空")


def _payload(item, index):
    start, end = _timestamp(item["start_time"]), _timestamp(item["end_time"])
    score = round(sum(item[k] for k in SCORE_FIELDS) / len(SCORE_FIELDS), 1)
    return {
        **{
            k: item[k]
            for k in (
                "title",
                "start_time",
                "end_time",
                "summary",
                "highlight_reason",
                "suggested_editing",
                "topic_key",
                "key_moment_time",
                "humor_score",
                "completeness_score",
                "decision",
                "decision_reason",
                "review_issues",
            )
        },
        "clip_id": f"clip_{index:03d}",
        "duration_seconds": end - start,
        "cover_time_seconds": _timestamp(item["key_moment_time"]) - start,
        "spread_value": "由内容决定",
        "confidence_score": score / 100,
        "quality_score": score,
        "text_quality_score": score,
        "quality_tier": "",
        "selected_by_default": item["decision"] == "publish",
        "rejection_reason": "；".join(item["review_issues"])
        if item["decision"] != "reject"
        else item["decision_reason"],
        "quality_evidence": {
            "source_id": item["source_id"],
            "ai_original_decision": item,
            "content_evidence": item["evidence"],
        },
    }


def analyze_content_decisions(request):
    text = _read_transcript(request.transcript_path)
    rows = _extract_transcript_rows(text)
    if not rows:
        raise AIAnalysisError("转写中没有逐句时间戳")
    provider = build_provider(request.provider_name)
    preference = _preference_summary(
        request.prompt_template or "", request.ai_preference
    )
    fingerprint = build_unit_fingerprint(
        {
            "contract": CONTRACT_VERSION,
            "transcript": hashlib.sha256(text.encode()).hexdigest(),
            "provider": provider_fingerprint_fields(provider),
            "preference": preference,
        }
    )
    stats = {
        "expected_units": 0,
        "completed_units": 0,
        "failed_units": 0,
        "empty_unit_count": 0,
    }
    failures = []
    stats_lock = Lock()

    def unit(stage, unit_id, prompt, schema, validator):
        with stats_lock:
            stats["expected_units"] += 1

        def operation():
            payload = _loads_ai_json(
                generate_json_with_safe_retry(provider, prompt, output_schema=schema)
            )
            try:
                validator(payload)
            except AIAnalysisError as exc:
                exc.response_payload = payload
                from app.services.storage_service import get_artifact_paths

                path = (
                    get_artifact_paths(request.task_id)["analysis_path"].parent
                    / "invalid_decisions"
                )
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    (path / f"{stage}-{unit_id}-{uuid4().hex[:12]}.json").write_text(
                        json.dumps(
                            {
                                "error": str(exc),
                                "input_fingerprint": fingerprint,
                                "request_fingerprint": build_unit_fingerprint(
                                    {"prompt": prompt, "schema": schema}
                                ),
                                "response": payload,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                raise
            return payload

        execution = execute_checkpointed_ai_unit(
            task_id=request.task_id,
            namespace=f"content_{stage}",
            input_fingerprint=fingerprint,
            unit_id=unit_id,
            request_fingerprint=build_unit_fingerprint(
                {"prompt": prompt, "schema": schema}
            ),
            operation=operation,
            validate_payload=validator,
        )
        if execution.status != "completed":
            with stats_lock:
                stats["failed_units"] += 1
                failures.append(
                    {
                        "stage": stage,
                        "message": f"{unit_id}: {execution.error or execution.status}",
                    }
                )
            return None
        with stats_lock:
            stats["completed_units"] += 1
            if not next(iter(execution.payload.values())):
                stats["empty_unit_count"] += 1
        return execution.payload

    def recall(window):
        prompt = f"""阶段：从提供的原文召回值得进一步核对的内容。选片偏好只依据下面的方案正文。
{preference}
逐条召回所有符合正文偏好的内容，没有固定数量、最低数量或每窗上限。可以返回空数组。
此阶段只定位，不确定边界留给扩展和评审。时间来自原文，评分是 0–100 数字。
按给定 JSON schema 输出。
窗口 {window.index}/{window.total}：\n{window.text}"""
        result = unit(
            "recall",
            f"window_{window.index:03d}",
            prompt,
            RECALL_OUTPUT_SCHEMA,
            lambda p, w=window: _validate_recall(p, w),
        )
        return (
            [
                {**m, "source_id": f"w{window.index:03d}_m{i:03d}"}
                for i, m in enumerate(result["moments"], 1)
            ]
            if result
            else []
        )

    windows = build_comedy_windows(rows, provider_name=request.provider_name)
    moments = [m for batch in _parallel_in_order(recall, windows) for m in batch]

    expanded = []
    contexts = {}

    # 一条一批控制上下文，不删除相邻或低分候选；重叠窗口的重复交给 AI 明确淘汰。
    def expand(moment):
        source_id = moment["source_id"]
        key = _timestamp(moment["key_time"])
        context = [
            r
            for r in rows
            if r.end_seconds >= key - 150 and r.start_seconds <= key + 150
        ]
        prompt = f"""阶段：根据提供的原文为指定候选提出连续起止时间，供最终评审核对。
{preference}
不补足时长，不围绕关键点机械截短。所有候选均须返回；是否可出片由最终评审决定。
最多 150 秒是正文要求；没有 60 秒下限。不能确认时在 arc_structure 写明限制。
按给定 schema 返回候选 {json.dumps(moment, ensure_ascii=False)}。
原文：\n{chr(10).join(_format_row(r) for r in context)}"""
        result = unit(
            "expansion",
            source_id,
            prompt,
            EXPANSION_OUTPUT_SCHEMA,
            lambda p, sid=source_id, ctx=context: validate_decisions(
                p, {sid: ctx}, final=False
            ),
        )
        return source_id, context, result

    for source_id, context, result in _parallel_in_order(expand, moments):
        contexts[source_id] = context
        if result:
            expanded.extend(result["clips"])

    judged = []
    for candidate in expanded:
        source_id = candidate["source_id"]
        context = contexts[source_id]
        lower, upper = context[0].start_seconds, context[-1].end_seconds
        neighbors = [
            c
            for c in expanded
            if _timestamp(c["end_time"]) >= lower
            and _timestamp(c["start_time"]) <= upper
        ]
        prior = [
            {
                "source_id": c["source_id"],
                "decision": c["decision"],
                "decision_reason": c["decision_reason"],
                "start_time": c["start_time"],
                "end_time": c["end_time"],
            }
            for c in judged
            if c["source_id"] in {n["source_id"] for n in neighbors}
        ]
        prompt = f"""阶段：对指定候选作最终决定，核验实际开头、主题、笑点、回应、收尾。
{preference}
按内容决定 publish（可出片）、review（待审核）或 reject（淘汰），评分仅供解释。合格多少采用多少，可以为零。
必须保留不确定性和全部限制意见；高分不能消除待审核问题。存在未解决问题必须 review，写入 review_issues。
可以依据所提供原文调整最终边界，禁止为了 60 秒补长。publish 必须连续且不超过 150 秒，并对齐原文句子的起止时间。
evidence 逐项引用原文的一句，role 为 opening/topic/highlight/response/ending。publish 五项齐全，引用必须完整处于最终边界内。
原文没有注明的表情、笑声不可编造。正文允许追问、解释、补刀等言语回应形成闭环；已有这些原文证据时，不得仅因缺少笑声或表情标记就要求待审核。
只有判定正文必要条件确实依赖缺失证据时才因此 review，不添加正文之外的内容门槛。decision_reason 解释决定，review_issues 记录影响采用的待核实项。
同一内容的重复版本由你比较处理，reject 时 duplicate_of 引用已采用的 source_id，否则为空。不要因相邻时间就视为重复。
全部候选只分批核验，不设置保留数量。只返回指定 source_id，其他候选仅供比较。所有必填字段按 schema 输出。
指定候选：{json.dumps(candidate, ensure_ascii=False)}
附近候选：{json.dumps(neighbors, ensure_ascii=False)}
此前已评审：{json.dumps(prior, ensure_ascii=False)}
原文：\n{chr(10).join(_format_row(r) for r in context)}"""
        result = unit(
            "global_judge",
            source_id,
            prompt,
            DECISION_SCHEMA,
            lambda p, sid=source_id, ctx=context: validate_decisions(
                p,
                {sid: ctx},
                final=True,
                catalog_ids={
                    c["source_id"]
                    for c in judged
                    if c["decision"] in {"publish", "review"}
                },
            ),
        )
        if result:
            judged.extend(result["clips"])
    complete = not failures
    clips = [_payload(item, i) for i, item in enumerate(judged, 1)]
    counts = {d: sum(c["decision"] == d for c in clips) for d in DECISIONS}
    ratio = (
        stats["completed_units"] / stats["expected_units"]
        if stats["expected_units"]
        else 1
    )
    return AIClipAnalysisResult(
        task_id=request.task_id,
        clips=clips,
        analysis_summary=f"按内容分析：可出片 {counts['publish']}，待审核 {counts['review']}，淘汰 {counts['reject']}。"
        + (
            "分析完成，暂无可用片段。"
            if complete and not counts["publish"] and not counts["review"]
            else ""
        ),
        analysis_meta={
            **stats,
            "schema_version": 2,
            "decision_contract": CONTRACT_VERSION,
            "selection_count_mode": "content",
            "selection_profile": "variety_comedy",
            "coverage_basis": "recall_and_expansion_units",
            "invalid_item_count": 0,
            "coverage_ratio": round(ratio, 6),
            "coverage_percent": round(ratio * 100, 2),
            "analysis_incomplete": not complete,
            "quality_degraded": not complete,
            "failed_stages": failures,
            "decision_counts": counts,
        },
    )


def _parallel_in_order(operation, items):
    """最多两个独立请求；传播租约 context，按原顺序收集以保持候选 ID 稳定。"""
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(copy_context().run, operation, item) for item in items
        ]
        return [future.result() for future in futures]
