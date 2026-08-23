from __future__ import annotations

import ast
from dataclasses import dataclass
import math
import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.models.task import AIClipAnalysisResult
from app.services.ai.base import AIProvider, AIProviderError, ProviderConfig
from app.services.ai.codex_cli_provider import CodexCliConfig, CodexCliProvider
from app.services.ai.local_model_provider import LocalModelProvider
from app.services.ai.remote_responses_provider import RemoteResponsesProvider


PROMPT_PATH = settings.project_root / "prompts" / "clip_analysis_prompt.txt"
ANALYSIS_CHUNK_SECONDS = 180
ANALYSIS_MAX_CONTEXT_CHARS = 4500
LOCAL_ANALYSIS_CHUNK_SECONDS = ANALYSIS_CHUNK_SECONDS
LOCAL_ANALYSIS_MAX_CONTEXT_CHARS = ANALYSIS_MAX_CONTEXT_CHARS
COVER_TIME_PROMPT_REQUIREMENT = """
【程序必填字段补充要求】
每个 clips 项必须额外包含 cover_time_seconds。
cover_time_seconds 表示相对于该条短视频开头的封面画面秒数，必须是数字，满足 0 <= cover_time_seconds < duration_seconds。
请选择最能代表核心观点、笑点、冲突或人物反应的时刻，避免使用明显的片头、片尾、寒暄或空白画面。
示例：片段从原视频 00:12:10 开始，适合的封面画面位于原视频 00:12:25，则 cover_time_seconds 应填写 15。
只返回严格 JSON，不要解释这个补充要求。
""".strip()


class AIAnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class AnalysisRequest:
    task_id: str
    transcript_path: Path
    max_clip_duration_minutes: int
    target_clip_count: int
    ai_preference: str
    provider_name: str
    prompt_template: str | None = None


@dataclass(frozen=True)
class TranscriptRow:
    start_time: str
    end_time: str
    start_seconds: int
    end_seconds: int
    text: str


@dataclass(frozen=True)
class TranscriptChunk:
    index: int
    total: int
    start_seconds: int
    end_seconds: int
    text: str
    prompt_chars: int


def analyze_task_transcript(request: AnalysisRequest) -> AIClipAnalysisResult:
    transcript_text = _read_transcript(request.transcript_path)
    transcript_bounds = _get_transcript_bounds(transcript_text)
    return _analyze_task_transcript_in_chunks(request, transcript_text, transcript_bounds)


def inspect_local_analysis_plan(request: AnalysisRequest) -> dict[str, Any]:
    transcript_text = _read_transcript(request.transcript_path)
    chunks = _build_local_analysis_chunks(request, transcript_text)
    prompt_chars = [chunk.prompt_chars for chunk in chunks]
    return {
        "transcript_chars": len(transcript_text),
        "chunk_count": len(chunks),
        "chunk_seconds": ANALYSIS_CHUNK_SECONDS,
        "max_prompt_chars": max(prompt_chars) if prompt_chars else 0,
        "min_prompt_chars": min(prompt_chars) if prompt_chars else 0,
        "needs_chunking": len(chunks) > 1,
    }


def _analyze_task_transcript_in_chunks(
    request: AnalysisRequest,
    transcript_text: str,
    transcript_bounds: tuple[int, int],
) -> AIClipAnalysisResult:
    provider = build_provider(request.provider_name)
    chunks = _build_local_analysis_chunks(request, transcript_text)
    if not chunks:
        raise AIAnalysisError("AI 分段分析失败：没有从转写文本中解析到可分析的时间戳正文")

    clips = []
    failures: list[str] = []
    per_chunk_target = max(1, min(3, math.ceil(request.target_clip_count / len(chunks)) + 1))
    for chunk in chunks:
        prompt = _render_prompt(
            max_clip_duration=request.max_clip_duration_minutes * 60,
            target_clip_count=per_chunk_target,
            ai_preference=request.ai_preference,
            transcript_text=chunk.text,
            prompt_template=request.prompt_template,
        )
        try:
            raw_text = provider.generate_json(prompt)
            try:
                chunk_result = _parse_and_validate(raw_text, task_id=request.task_id)
            except AIAnalysisError as first_error:
                retry_instruction = (
                    "上一次输出无法被程序解析或校验。请重新输出严格 JSON，"
                    "不要 Markdown，不要解释文字。每个 clips 项必须包含："
                    "clip_id、title、start_time、end_time、duration_seconds、cover_time_seconds、summary、"
                    "highlight_reason、spread_value、suggested_editing、confidence_score、selected_by_default。"
                    "cover_time_seconds 是相对于短视频开头的秒数，必须大于或等于 0 且小于 duration_seconds。"
                    "spread_value 只能是“高”“中”“低”。片段时长不能超限。"
                )
                raw_text = provider.generate_json(prompt, retry_instruction=retry_instruction)
                try:
                    chunk_result = _parse_and_validate(raw_text, task_id=request.task_id)
                except AIAnalysisError as second_error:
                    raise AIAnalysisError(f"AI 返回非法 JSON，安全重试后仍失败：{second_error}") from first_error
            _validate_clip_constraints(chunk_result, request, transcript_bounds)
            clips.extend(chunk_result.clips)
        except Exception as exc:
            failures.append(
                f"第 {chunk.index}/{chunk.total} 段失败，"
                f"时间范围 {_seconds_to_time(chunk.start_seconds)}-{_seconds_to_time(chunk.end_seconds)}，"
                f"prompt 约 {len(prompt)} 字：{exc}"
            )

    merged_clips = _dedupe_and_rank_clips(clips, request.target_clip_count)
    if not merged_clips:
        failure_text = "；".join(failures[:5]) if failures else "没有候选片段"
        raise AIAnalysisError(f"AI 分段分析没有生成可用候选片段：{failure_text}")

    for index, clip in enumerate(merged_clips, start=1):
        clip.clip_id = f"clip_{index:03d}"

    provider_label = (
        "Codex CLI"
        if request.provider_name == "codex"
        else "本地 AI"
        if request.provider_name == "local"
        else "远程 AI"
    )
    summary = f"{provider_label} 已按 {len(chunks)} 个小段完成分段分析，并合并为 {len(merged_clips)} 条候选片段。"
    if failures:
        summary += f" 有 {len(failures)} 个小段失败，已跳过失败小段。"
    result = AIClipAnalysisResult(task_id=request.task_id, analysis_summary=summary, clips=merged_clips)
    _validate_clip_constraints(result, request, transcript_bounds)
    return result


def build_provider(provider_name: str | None = None, purpose: str = "analysis") -> AIProvider:
    default_provider = settings.ai_publish_provider if purpose == "publish" else settings.ai_default_provider
    resolved = (provider_name or default_provider).lower()
    if resolved == "codex":
        return CodexCliProvider(
            CodexCliConfig(
                executable=settings.ai_codex_path,
                model=settings.ai_codex_model,
                timeout_seconds=settings.ai_codex_timeout_seconds,
                codex_home=settings.ai_codex_home,
            )
        )
    if resolved == "remote":
        model = settings.ai_publish_remote_model if purpose == "publish" else settings.ai_analysis_remote_model
        return build_remote_provider(model, purpose=purpose)
    if resolved == "local":
        return LocalModelProvider(
            ProviderConfig(
                base_url=settings.ai_local_base_url,
                api_key=settings.ai_local_api_key,
                model=settings.ai_local_model,
                protocol=settings.ai_local_protocol,
                timeout_seconds=settings.ai_request_timeout_seconds,
                fallback_protocol=settings.ai_local_fallback_protocol,
            )
        )
    raise AIAnalysisError("AI provider 只能是 codex、remote 或 local")


def build_remote_provider(model: str | None = None, purpose: str = "analysis") -> AIProvider:
    if purpose == "publish":
        return RemoteResponsesProvider(
            ProviderConfig(
                base_url=settings.ai_publish_remote_base_url,
                api_key=settings.ai_publish_remote_api_key,
                model=(model or settings.ai_publish_remote_model or "deepseek-v4-flash"),
                protocol=settings.ai_publish_remote_protocol,
                timeout_seconds=settings.ai_publish_request_timeout_seconds,
                responses_path=settings.ai_publish_remote_responses_path,
                reasoning_effort=settings.ai_publish_remote_reasoning_effort,
                disable_response_storage=settings.ai_publish_remote_disable_response_storage.lower() == "true",
                api_key_name="AI_PUBLISH_REMOTE_API_KEY",
            )
        )
    return RemoteResponsesProvider(
        ProviderConfig(
            base_url=settings.ai_analysis_remote_base_url,
            api_key=settings.ai_analysis_remote_api_key,
            model=(model or settings.ai_analysis_remote_model or "deepseek-v4-flash"),
            protocol=settings.ai_analysis_remote_protocol,
            timeout_seconds=settings.ai_analysis_request_timeout_seconds,
            responses_path=settings.ai_analysis_remote_responses_path,
            reasoning_effort=settings.ai_analysis_remote_reasoning_effort,
            disable_response_storage=settings.ai_analysis_remote_disable_response_storage.lower() == "true",
            api_key_name="AI_ANALYSIS_REMOTE_API_KEY",
        )
    )


def _read_transcript(transcript_path: Path) -> str:
    if not transcript_path.exists():
        raise AIAnalysisError("未找到转写文本，请先生成转写 Markdown")
    transcript_text = transcript_path.read_text(encoding="utf-8", errors="replace").strip()
    if not transcript_text:
        raise AIAnalysisError("转写文本为空，无法进行 AI 分析")
    sentence_text = _extract_sentence_transcript_section(transcript_text)
    if sentence_text:
        transcript_text = sentence_text
    if not _TIME_PATTERN.search(transcript_text):
        raise AIAnalysisError("转写文本里没有可识别时间戳，请先生成带时间戳的转写文本")
    return transcript_text


def _extract_sentence_transcript_section(transcript_text: str) -> str:
    lines = transcript_text.splitlines()
    section_lines: list[str] = []
    in_sentence_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_sentence_section:
                break
            in_sentence_section = "逐句时间戳原文" in stripped
            if in_sentence_section:
                section_lines.append(stripped)
            continue
        if in_sentence_section:
            section_lines.append(line)
    return "\n".join(section_lines).strip()


def _render_prompt(
    max_clip_duration: int,
    target_clip_count: int,
    ai_preference: str,
    transcript_text: str,
    prompt_template: str | None = None,
) -> str:
    template = prompt_template or PROMPT_PATH.read_text(encoding="utf-8")
    replacements = {
        "{{MAX_CLIP_DURATION}}": str(max_clip_duration),
        "{{TARGET_CLIP_COUNT}}": str(target_clip_count),
        "{{AI_PREFERENCE}}": ai_preference or "观点集中、逻辑完整、适合短视频传播",
        "{{TRANSCRIPT_TEXT}}": transcript_text,
    }
    prompt = template
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)
    return f"{prompt.rstrip()}\n\n{COVER_TIME_PROMPT_REQUIREMENT}"


def _build_local_analysis_chunks(request: AnalysisRequest, transcript_text: str) -> list[TranscriptChunk]:
    rows = _extract_transcript_rows(transcript_text)
    if not rows:
        return []

    chunks_text: list[tuple[int, int, str]] = []
    current_lines: list[str] = []
    current_start = rows[0].start_seconds
    current_end = rows[0].end_seconds

    for row in rows:
        row_line = f"{row.start_time} - {row.end_time} {row.text}"
        candidate_lines = [*current_lines, row_line]
        candidate_text = "\n".join(candidate_lines)
        candidate_prompt = _render_prompt(
            max_clip_duration=request.max_clip_duration_minutes * 60,
            target_clip_count=1,
            ai_preference=request.ai_preference,
            transcript_text=candidate_text,
            prompt_template=request.prompt_template,
        )
        exceeds_time = row.end_seconds - current_start > ANALYSIS_CHUNK_SECONDS
        exceeds_size = len(candidate_prompt) > ANALYSIS_MAX_CONTEXT_CHARS
        if current_lines and (exceeds_time or exceeds_size):
            chunks_text.append((current_start, current_end, "\n".join(current_lines)))
            current_lines = [row_line]
            current_start = row.start_seconds
        else:
            current_lines = candidate_lines
        current_end = row.end_seconds

    if current_lines:
        chunks_text.append((current_start, current_end, "\n".join(current_lines)))

    total = len(chunks_text)
    chunks = []
    for index, (start_seconds, end_seconds, text) in enumerate(chunks_text, start=1):
        prompt = _render_prompt(
            max_clip_duration=request.max_clip_duration_minutes * 60,
            target_clip_count=1,
            ai_preference=request.ai_preference,
            transcript_text=text,
            prompt_template=request.prompt_template,
        )
        chunks.append(
            TranscriptChunk(
                index=index,
                total=total,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                text=text,
                prompt_chars=len(prompt),
            )
        )
    return chunks


def _extract_transcript_rows(transcript_text: str) -> list[TranscriptRow]:
    rows: list[TranscriptRow] = []
    for raw_line in transcript_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        start_time, end_time, text = cells[0], cells[1], cells[2]
        if not _TIME_PATTERN.fullmatch(start_time) or not _TIME_PATTERN.fullmatch(end_time):
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        rows.append(
            TranscriptRow(
                start_time=start_time,
                end_time=end_time,
                start_seconds=_time_to_seconds(start_time),
                end_seconds=_time_to_seconds(end_time),
                text=text,
            )
        )

    if rows:
        return rows

    for match in re.finditer(
        r"(?P<start>(?:(?:\d{2}:)?\d{2}:\d{2}))\s*[-–]\s*(?P<end>(?:(?:\d{2}:)?\d{2}:\d{2}))\s+(?P<text>.+)",
        transcript_text,
    ):
        text = re.sub(r"\s+", " ", match.group("text")).strip()
        if not text:
            continue
        start_time = match.group("start")
        end_time = match.group("end")
        rows.append(
            TranscriptRow(
                start_time=start_time,
                end_time=end_time,
                start_seconds=_time_to_seconds(start_time),
                end_seconds=_time_to_seconds(end_time),
                text=text,
            )
        )
    return rows


def _dedupe_and_rank_clips(clips: list[Any], target_clip_count: int) -> list[Any]:
    sorted_clips = sorted(clips, key=lambda clip: clip.confidence_score, reverse=True)
    selected = []
    for clip in sorted_clips:
        start_seconds = _time_to_seconds(clip.start_time)
        end_seconds = _time_to_seconds(clip.end_time)
        overlaps = False
        for existing in selected:
            existing_start = _time_to_seconds(existing.start_time)
            existing_end = _time_to_seconds(existing.end_time)
            overlap_seconds = max(0, min(end_seconds, existing_end) - max(start_seconds, existing_start))
            shorter = max(1, min(end_seconds - start_seconds, existing_end - existing_start))
            if overlap_seconds / shorter >= 0.5:
                overlaps = True
                break
        if overlaps:
            continue
        selected.append(clip)
        if len(selected) >= target_clip_count:
            break
    return sorted(selected, key=lambda clip: _time_to_seconds(clip.start_time))


def _parse_and_validate(raw_text: str, task_id: str | None = None) -> AIClipAnalysisResult:
    try:
        payload = _loads_ai_json(raw_text)
    except AIAnalysisError:
        raise

    payload = _normalize_ai_payload(payload, task_id=task_id)

    try:
        if hasattr(AIClipAnalysisResult, "model_validate"):
            result = AIClipAnalysisResult.model_validate(payload)
        else:
            result = AIClipAnalysisResult.parse_obj(payload)
    except ValidationError as exc:
        raise AIAnalysisError(f"JSON 字段校验失败：{exc}") from exc


    if task_id and not result.task_id:
        result.task_id = task_id
    return result


def _normalize_ai_payload(payload: Any, task_id: str | None = None) -> Any:
    if isinstance(payload, list):
        payload = {"task_id": task_id or "", "analysis_summary": "", "clips": payload}
    if not isinstance(payload, dict):
        return payload

    if task_id and not payload.get("task_id"):
        payload["task_id"] = task_id
    if not _has_text(payload.get("analysis_summary")):
        payload["analysis_summary"] = "AI 已完成候选片段分析。"

    clips = payload.get("clips")
    if clips is None:
        for alias in ("clip_candidates", "candidates", "items", "results"):
            if isinstance(payload.get(alias), list):
                clips = payload[alias]
                payload["clips"] = clips
                break
    if not isinstance(clips, list):
        return payload

    payload["clips"] = [
        _normalize_ai_clip_item(clip, index)
        if isinstance(clip, dict)
        else clip
        for index, clip in enumerate(clips, start=1)
    ]
    return payload


def _normalize_ai_clip_item(clip: dict[str, Any], index: int) -> dict[str, Any]:
    item = dict(clip)

    _copy_first_text(item, "clip_id", ("clip_key", "id", "key"))
    _copy_first_text(item, "title", ("clip_title", "name"))
    _copy_first_text(item, "summary", ("clip_summary", "description", "desc"))
    _copy_first_text(item, "highlight_reason", ("reason", "recommend_reason", "highlight", "why"))
    _copy_first_text(item, "spread_value", ("viral_value", "share_value", "virality", "shareability"))
    _copy_first_text(item, "suggested_editing", ("editing_suggestion", "edit_suggestion", "suggestion"))
    _copy_first_text(item, "confidence_score", ("confidence", "score"))
    _copy_first_text(
        item,
        "cover_time_seconds",
        ("cover_second", "cover_seconds", "cover_time", "cover_timestamp_seconds", "thumbnail_time_seconds"),
    )

    if not _has_text(item.get("clip_id")):
        item["clip_id"] = f"clip_{index:03d}"
    if not _has_text(item.get("title")):
        item["title"] = f"候选片段 {index:03d}"
    if not _has_text(item.get("summary")):
        item["summary"] = _first_text(item, ("title", "highlight_reason")) or "AI 未返回摘要，已使用兼容默认摘要。"
    if not _has_text(item.get("highlight_reason")):
        item["highlight_reason"] = _first_text(item, ("summary", "title")) or "AI 未返回推荐理由，已使用兼容默认理由。"
    if not _has_text(item.get("spread_value")):
        item["spread_value"] = "中"
    item["spread_value"] = _normalize_spread_value(item.get("spread_value"))
    if not _has_text(item.get("suggested_editing")):
        item["suggested_editing"] = "保留片段核心内容，剪掉明显停顿和无关转场。"
    if not _has_text(item.get("confidence_score")):
        item["confidence_score"] = 0.7
    item["confidence_score"] = _normalize_confidence_score(item.get("confidence_score"))

    duration_seconds = _duration_seconds_from_clip(item)
    if duration_seconds is not None:
        item["duration_seconds"] = duration_seconds
    item["cover_time_seconds"] = _normalize_cover_time_seconds(
        item.get("cover_time_seconds"),
        duration_seconds,
    )

    item["clip_id"] = _limit_text(item["clip_id"], 80)
    item["title"] = _limit_text(item["title"], 160)
    item["summary"] = _limit_text(item["summary"], 1000)
    item["highlight_reason"] = _limit_text(item["highlight_reason"], 1000)
    item["spread_value"] = _limit_text(item["spread_value"], 40)
    item["suggested_editing"] = _limit_text(item["suggested_editing"], 1000)
    return item


def _copy_first_text(item: dict[str, Any], target: str, aliases: tuple[str, ...]) -> None:
    if _has_text(item.get(target)):
        return
    value = _first_text(item, aliases)
    if value is not None:
        item[target] = value


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = item.get(key)
        if _has_text(value):
            return str(value).strip()
    return None


def _has_text(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, (int, float, bool))


def _normalize_confidence_score(value: Any) -> float:
    try:
        score = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return 0.7

    if math.isnan(score) or math.isinf(score):
        return 0.7
    if 1 < score <= 10:
        score = score / 10
    elif 10 < score <= 100:
        score = score / 100
    return min(1, max(0, score))


def _midpoint_cover_time_seconds(duration_seconds: int | float | None) -> float:
    try:
        duration = float(duration_seconds or 0)
    except (TypeError, ValueError):
        duration = 0
    if not math.isfinite(duration) or duration <= 0:
        return 0.0
    return round(max(0.0, min(duration - 0.001, duration / 2)), 3)


def _normalize_cover_time_seconds(value: Any, duration_seconds: int | float | None) -> float:
    fallback = _midpoint_cover_time_seconds(duration_seconds)
    try:
        seconds = float(value)
        duration = float(duration_seconds or 0)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(seconds) or not math.isfinite(duration):
        return fallback
    if seconds < 0 or duration <= 0 or seconds >= duration:
        return fallback
    return round(seconds, 3)


def _normalize_spread_value(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"高", "中", "低"}:
        return str(value).strip()
    if any(marker in text for marker in ("高", "爆", "强", "viral", "hot")):
        return "高"
    if any(marker in text for marker in ("低", "弱", "普通")):
        return "低"
    return "中"


def _limit_text(value: Any, max_length: int) -> str:
    text = str(value).strip()
    if len(text) <= max_length:
        return text
    return text[:max_length]


def _duration_seconds_from_clip(item: dict[str, Any]) -> int | None:
    raw_duration = item.get("duration_seconds")
    try:
        duration = int(float(raw_duration))
    except (TypeError, ValueError):
        duration = 0
    if duration > 0:
        return duration

    start_time = item.get("start_time")
    end_time = item.get("end_time")
    if not (_has_text(start_time) and _has_text(end_time)):
        return None
    try:
        calculated = _time_to_seconds(str(end_time)) - _time_to_seconds(str(start_time))
    except (TypeError, ValueError):
        return None
    if calculated <= 0:
        return None
    return calculated


def _loads_ai_json(raw_text: str) -> Any:
    last_error: Exception | None = None
    for candidate in _iter_json_candidates(raw_text):
        for repaired in _iter_repaired_json_candidates(candidate):
            try:
                return json.loads(repaired, strict=False)
            except json.JSONDecodeError as exc:
                last_error = exc
            try:
                literal_payload = ast.literal_eval(repaired)
            except (SyntaxError, ValueError) as exc:
                last_error = exc
            else:
                if isinstance(literal_payload, (dict, list)):
                    return literal_payload

    if last_error:
        raise AIAnalysisError(f"JSON 解析失败：{last_error}") from last_error
    raise AIAnalysisError("JSON 解析失败：AI 没有返回可识别的 JSON 内容")


def _iter_json_candidates(raw_text: str) -> list[str]:
    cleaned = (raw_text or "").strip().lstrip("\ufeff")
    candidates: list[str] = []

    def add(value: str | None) -> None:
        if value:
            normalized = value.strip().lstrip("\ufeff")
            if normalized and normalized not in candidates:
                candidates.append(normalized)

    add(cleaned)
    add(_strip_markdown_code_fence(cleaned))
    add(_extract_first_json_value(cleaned))
    return candidates


def _strip_markdown_code_fence(text: str) -> str | None:
    match = re.fullmatch(r"```(?:json|JSON)?\s*(.*?)\s*```", text.strip(), flags=re.DOTALL)
    if not match:
        return None
    return match.group(1)


def _extract_first_json_value(text: str) -> str | None:
    start = -1
    for index, char in enumerate(text):
        if char in "{[":
            start = index
            break
    if start < 0:
        return None

    stack: list[str] = []
    in_string = False
    escape = False
    quote_char = ""
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote_char:
                in_string = False
            continue

        if char in {'"', "'"}:
            in_string = True
            quote_char = char
            continue
        if char == "{":
            stack.append("}")
            continue
        if char == "[":
            stack.append("]")
            continue
        if stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return text[start : index + 1]

    return text[start:].strip()


def _iter_repaired_json_candidates(text: str) -> list[str]:
    candidates: list[str] = []

    def add(value: str) -> None:
        if value and value not in candidates:
            candidates.append(value)

    add(text.strip())
    without_trailing_commas = _remove_trailing_commas(text)
    add(without_trailing_commas)
    with_quoted_keys = _quote_unquoted_object_keys(without_trailing_commas)
    add(with_quoted_keys)
    add(_replace_python_literals(with_quoted_keys))
    return candidates


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text.strip())


def _quote_unquoted_object_keys(text: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escape = False
    quote_char = ""
    expecting_key = False

    while index < len(text):
        char = text[index]
        if in_string:
            result.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote_char:
                in_string = False
            index += 1
            continue

        if char in {'"', "'"}:
            in_string = True
            quote_char = char
            result.append(char)
            index += 1
            continue

        if char in "{,":
            expecting_key = True
            result.append(char)
            index += 1
            continue

        if expecting_key and char.isspace():
            result.append(char)
            index += 1
            continue

        if expecting_key and (char.isalpha() or char == "_"):
            key_start = index
            index += 1
            while index < len(text) and (text[index].isalnum() or text[index] == "_"):
                index += 1
            key = text[key_start:index]
            lookahead = index
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] == ":":
                result.append(f'"{key}"')
                expecting_key = False
                continue
            result.append(key)
            expecting_key = False
            continue

        if not char.isspace():
            expecting_key = False
        result.append(char)
        index += 1

    return "".join(result)


def _replace_python_literals(text: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escape = False
    quote_char = ""
    replacements = {"True": "true", "False": "false", "None": "null"}

    while index < len(text):
        char = text[index]
        if in_string:
            result.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote_char:
                in_string = False
            index += 1
            continue

        if char in {'"', "'"}:
            in_string = True
            quote_char = char
            result.append(char)
            index += 1
            continue

        replaced = False
        for source, target in replacements.items():
            end = index + len(source)
            before_ok = index == 0 or not (text[index - 1].isalnum() or text[index - 1] == "_")
            after_ok = end >= len(text) or not (text[end].isalnum() or text[end] == "_")
            if text.startswith(source, index) and before_ok and after_ok:
                result.append(target)
                index = end
                replaced = True
                break
        if replaced:
            continue

        result.append(char)
        index += 1

    return "".join(result)


_TIME_PATTERN = re.compile(r"\b(?:(\d{2}):)?(\d{2}):(\d{2})\b")


def _get_transcript_bounds(transcript_text: str) -> tuple[int, int]:
    seconds_values = []
    for match in _TIME_PATTERN.finditer(transcript_text):
        seconds_values.append(_time_to_seconds(match.group(0)))
    if not seconds_values:
        raise AIAnalysisError("转写文本里没有可识别时间戳")
    return min(seconds_values), max(seconds_values)


def _validate_clip_constraints(
    result: AIClipAnalysisResult,
    request: AnalysisRequest,
    transcript_bounds: tuple[int, int],
) -> None:
    max_duration_seconds = request.max_clip_duration_minutes * 60
    transcript_start, transcript_end = transcript_bounds

    for clip in result.clips:
        start_seconds = _time_to_seconds(clip.start_time)
        end_seconds = _time_to_seconds(clip.end_time)
        if end_seconds <= start_seconds:
            raise AIAnalysisError(f"{clip.clip_id} 的结束时间必须晚于开始时间")
        real_duration = end_seconds - start_seconds
        if real_duration > max_duration_seconds or clip.duration_seconds > max_duration_seconds:
            raise AIAnalysisError(f"{clip.clip_id} 超过用户设置的单条最长时长")
        if abs(real_duration - clip.duration_seconds) > 3:
            raise AIAnalysisError(f"{clip.clip_id} 的 duration_seconds 与起止时间不一致")
        if clip.cover_time_seconds < 0 or clip.cover_time_seconds >= real_duration:
            raise AIAnalysisError(f"{clip.clip_id} 的 cover_time_seconds 必须位于片段时长范围内")
        if start_seconds < transcript_start or end_seconds > transcript_end:
            raise AIAnalysisError(f"{clip.clip_id} 的起止时间超出转写文本时间范围")


def _time_to_seconds(value: str) -> int:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def _seconds_to_time(value: int) -> str:
    hours = value // 3600
    minutes = (value % 3600) // 60
    seconds = value % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def result_to_jsonable(result: AIClipAnalysisResult) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return result.dict()


__all__ = [
    "AIAnalysisError",
    "AIProviderError",
    "AnalysisRequest",
    "analyze_task_transcript",
    "build_provider",
    "build_remote_provider",
    "inspect_local_analysis_plan",
    "result_to_jsonable",
]
