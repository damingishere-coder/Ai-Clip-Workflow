from __future__ import annotations

from dataclasses import dataclass
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


def analyze_task_transcript(request: AnalysisRequest) -> AIClipAnalysisResult:
    transcript_text = _read_transcript(request.transcript_path)
    transcript_bounds = _get_transcript_bounds(transcript_text)
    prompt = _render_prompt(
        max_clip_duration=request.max_clip_duration_minutes * 60,
        target_clip_count=request.target_clip_count,
        ai_preference=request.ai_preference,
        transcript_text=transcript_text,
    )
    provider = build_provider(request.provider_name)

    raw_text = provider.generate_json(prompt)
    try:
        result = _parse_and_validate(raw_text)
    except AIAnalysisError as first_error:
        retry_instruction = (
            "上一次输出无法被程序解析或校验。请重新输出严格 JSON，"
            "不要 Markdown，不要解释文字，字段必须完整，片段时长不能超限。"
        )
        raw_text = provider.generate_json(prompt, retry_instruction=retry_instruction)
        try:
            result = _parse_and_validate(raw_text)
        except AIAnalysisError as second_error:
            raise AIAnalysisError(f"AI 返回非法 JSON，安全重试后仍失败：{second_error}") from first_error

    _validate_clip_constraints(result, request, transcript_bounds)
    return result


def build_provider(provider_name: str | None = None) -> AIProvider:
    resolved = (provider_name or settings.ai_default_provider).lower()
    if resolved == "remote":
        return RemoteResponsesProvider(
            ProviderConfig(
                base_url=settings.ai_remote_base_url,
                api_key=settings.ai_remote_api_key,
                model=settings.ai_remote_model,
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
) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
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


def _parse_and_validate(raw_text: str) -> AIClipAnalysisResult:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AIAnalysisError(f"JSON 解析失败：{exc}") from exc

    try:
        if hasattr(AIClipAnalysisResult, "model_validate"):
            return AIClipAnalysisResult.model_validate(payload)
        return AIClipAnalysisResult.parse_obj(payload)
    except ValidationError as exc:
        raise AIAnalysisError(f"JSON 字段校验失败：{exc}") from exc


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
    "result_to_jsonable",
]
