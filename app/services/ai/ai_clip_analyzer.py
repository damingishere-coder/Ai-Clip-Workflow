from __future__ import annotations

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
from app.services.ai.local_model_provider import LocalModelProvider
from app.services.ai.remote_responses_provider import RemoteResponsesProvider


PROMPT_PATH = settings.project_root / "prompts" / "clip_analysis_prompt.txt"
LOCAL_ANALYSIS_CHUNK_SECONDS = 180
LOCAL_ANALYSIS_MAX_CONTEXT_CHARS = 4500


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
    if request.provider_name == "local":
        return _analyze_task_transcript_in_local_chunks(request, transcript_text, transcript_bounds)

    prompt = _render_prompt(
        max_clip_duration=request.max_clip_duration_minutes * 60,
        target_clip_count=request.target_clip_count,
        ai_preference=request.ai_preference,
        transcript_text=transcript_text,
        prompt_template=request.prompt_template,
    )
    provider = build_provider(request.provider_name)

    raw_text = provider.generate_json(prompt)
    try:
        result = _parse_and_validate(raw_text, task_id=request.task_id)
    except AIAnalysisError as first_error:
        retry_instruction = (
            "上一次输出无法被程序解析或校验。请重新输出严格 JSON，"
            "不要 Markdown，不要解释文字，字段必须完整，片段时长不能超限。"
        )
        raw_text = provider.generate_json(prompt, retry_instruction=retry_instruction)
        try:
            result = _parse_and_validate(raw_text, task_id=request.task_id)
        except AIAnalysisError as second_error:
            raise AIAnalysisError(f"AI 返回非法 JSON，安全重试后仍失败：{second_error}") from first_error

    _validate_clip_constraints(result, request, transcript_bounds)
    return result


def inspect_local_analysis_plan(request: AnalysisRequest) -> dict[str, Any]:
    transcript_text = _read_transcript(request.transcript_path)
    chunks = _build_local_analysis_chunks(request, transcript_text)
    prompt_chars = [chunk.prompt_chars for chunk in chunks]
    return {
        "transcript_chars": len(transcript_text),
        "chunk_count": len(chunks),
        "chunk_seconds": LOCAL_ANALYSIS_CHUNK_SECONDS,
        "max_prompt_chars": max(prompt_chars) if prompt_chars else 0,
        "min_prompt_chars": min(prompt_chars) if prompt_chars else 0,
        "needs_chunking": len(chunks) > 1,
    }


def _analyze_task_transcript_in_local_chunks(
    request: AnalysisRequest,
    transcript_text: str,
    transcript_bounds: tuple[int, int],
) -> AIClipAnalysisResult:
    provider = build_provider("local")
    chunks = _build_local_analysis_chunks(request, transcript_text)
    if not chunks:
        raise AIAnalysisError("本地 AI 分段分析失败：没有从转写文本中解析到可分析的时间戳正文")

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
                    "不要 Markdown，不要解释文字，字段必须完整，片段时长不能超限。"
                )
                raw_text = provider.generate_json(prompt, retry_instruction=retry_instruction)
                try:
                    chunk_result = _parse_and_validate(raw_text, task_id=request.task_id)
                except AIAnalysisError as second_error:
                    raise AIAnalysisError(f"AI 返回非法 JSON，安全重试后仍失败：{second_error}") from first_error
            _validate_clip_constraints(
                chunk_result,
                request,
                (chunk.start_seconds, chunk.end_seconds),
            )
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
        raise AIAnalysisError(f"本地 AI 分段分析没有生成可用候选片段：{failure_text}")

    for index, clip in enumerate(merged_clips, start=1):
        clip.clip_id = f"clip_{index:03d}"

    summary = f"本地 AI 已按 {len(chunks)} 个小段完成分段分析，并合并为 {len(merged_clips)} 条候选片段。"
    if failures:
        summary += f" 有 {len(failures)} 个小段失败，已跳过失败小段。"
    result = AIClipAnalysisResult(task_id=request.task_id, analysis_summary=summary, clips=merged_clips)
    _validate_clip_constraints(result, request, transcript_bounds)
    return result


def build_provider(provider_name: str | None = None) -> AIProvider:
    resolved = (provider_name or settings.ai_default_provider).lower()
    if resolved == "remote":
        remote_model = settings.ai_remote_review_model or settings.ai_remote_model
        if settings.ai_remote_model.startswith("deepseek") and not remote_model.startswith("deepseek"):
            remote_model = settings.ai_remote_model
        return RemoteResponsesProvider(
            ProviderConfig(
                base_url=settings.ai_remote_base_url,
                api_key=settings.ai_remote_api_key,
                model=remote_model,
                protocol=settings.ai_remote_protocol,
                timeout_seconds=settings.ai_request_timeout_seconds,
                responses_path=settings.ai_remote_responses_path,
                reasoning_effort=settings.ai_remote_reasoning_effort,
                disable_response_storage=settings.ai_remote_disable_response_storage.lower() == "true",
            )
        )
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
    raise AIAnalysisError("AI provider 只能是 remote 或 local")


def _read_transcript(transcript_path: Path) -> str:
    if not transcript_path.exists():
        raise AIAnalysisError("未找到转写文本，请先生成转写 Markdown")
    transcript_text = transcript_path.read_text(encoding="utf-8", errors="replace").strip()
    if not transcript_text:
        raise AIAnalysisError("转写文本为空，无法进行 AI 分析")
    if not _TIME_PATTERN.search(transcript_text):
        raise AIAnalysisError("转写文本里没有可识别时间戳，请先生成带时间戳的转写文本")
    return transcript_text


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
    return prompt


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
        exceeds_time = row.end_seconds - current_start > LOCAL_ANALYSIS_CHUNK_SECONDS
        exceeds_size = len(candidate_prompt) > LOCAL_ANALYSIS_MAX_CONTEXT_CHARS
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
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AIAnalysisError(f"JSON 解析失败：{exc}") from exc

    if task_id and isinstance(payload, dict) and not payload.get("task_id"):
        payload["task_id"] = task_id

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
    "inspect_local_analysis_plan",
    "result_to_jsonable",
]
