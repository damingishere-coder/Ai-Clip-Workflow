from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.ai import ai_clip_analyzer  # noqa: E402
from app.services.ai.ai_clip_analyzer import AnalysisRequest, analyze_task_transcript, inspect_local_analysis_plan  # noqa: E402


class ResilientFakeProvider:
    name = "fake"

    def __init__(self, fail_second_chunk: bool = False) -> None:
        self.calls = 0
        self.prompts: list[str] = []
        self.fail_second_chunk = fail_second_chunk

    def generate_json(self, prompt: str, retry_instruction: str | None = None) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        transcript_text = prompt.split("转写文本：")[-1].split("# Transcript")[-1]
        start = _first_time(transcript_text, "00:00:00")
        end = _last_time(transcript_text, "00:01:00")
        if self.fail_second_chunk and self.calls == 2:
            return "{ broken json"
        return json.dumps(
            {
                "analysis_summary": "fake chunk result",
                "clips": [
                    {
                        "clip_key": f"legacy_{self.calls:03d}",
                        "title": f"Legacy candidate {self.calls}",
                        "start_time": start,
                        "end_time": end,
                        "duration_seconds": _time_to_seconds(end) - _time_to_seconds(start),
                        "summary": "A focused and shareable segment.",
                        "highlight_reason": "It has a clear hook and enough context.",
                        "viral_value": "笑点密度高，适合传播",
                        "suggested_editing": "Keep the opening hook and add subtitles.",
                        "confidence_score": 0.8,
                        "selected_by_default": True,
                    }
                ],
            },
            ensure_ascii=False,
        )


def _first_time(text: str, fallback: str) -> str:
    import re

    match = re.search(r"\b\d{2}:\d{2}:\d{2}\b", text)
    return match.group(0) if match else fallback


def _last_time(text: str, fallback: str) -> str:
    import re

    matches = re.findall(r"\b\d{2}:\d{2}:\d{2}\b", text)
    return matches[-1] if matches else fallback


def _time_to_seconds(value: str) -> int:
    hours, minutes, seconds = [int(part) for part in value.split(":")]
    return hours * 3600 + minutes * 60 + seconds


def _build_transcript(minutes: int = 12) -> str:
    rows = []
    for minute in range(minutes):
        rows.append(
            f"| 00:{minute:02d}:00 | 00:{minute + 1:02d}:00 | "
            f"Minute {minute + 1} has enough dialogue for long video analysis testing. |"
        )
    return "\n".join(
        [
            "# Long transcript",
            "",
            "| Start | End | Text |",
            "| --- | --- | --- |",
            *rows,
        ]
    )


def _run_with_provider(provider: ResilientFakeProvider) -> tuple[dict, object]:
    original_build_provider = ai_clip_analyzer.build_provider
    ai_clip_analyzer.build_provider = lambda provider_name=None: provider
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "transcript.md"
            transcript_path.write_text(_build_transcript(), encoding="utf-8")
            request = AnalysisRequest(
                task_id="long-analysis-test",
                transcript_path=transcript_path,
                max_clip_duration_minutes=5,
                target_clip_count=5,
                ai_preference="shareable",
                provider_name="remote",
            )
            plan = inspect_local_analysis_plan(request)
            result = analyze_task_transcript(request)
    finally:
        ai_clip_analyzer.build_provider = original_build_provider
    return plan, result


def main() -> None:
    provider = ResilientFakeProvider()
    plan, result = _run_with_provider(provider)
    assert plan["chunk_count"] > 1, plan
    assert provider.calls == plan["chunk_count"], (provider.calls, plan)
    assert all("Minute 12 has" not in prompt or "Minute 1 has" not in prompt for prompt in provider.prompts), "expected chunked prompts"
    assert result.clips[0].clip_id == "clip_001"
    assert result.clips[0].spread_value == "高"

    partial_provider = ResilientFakeProvider(fail_second_chunk=True)
    partial_plan, partial_result = _run_with_provider(partial_provider)
    assert partial_provider.calls > partial_plan["chunk_count"], "expected retry on failed chunk"
    assert partial_result.clips, "partial failure should still keep valid chunks"

    print("AI long analysis resilience test passed")


if __name__ == "__main__":
    main()
