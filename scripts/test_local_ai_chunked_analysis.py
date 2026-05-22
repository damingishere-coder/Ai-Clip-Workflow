from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.ai import ai_clip_analyzer  # noqa: E402
from app.services.ai.ai_clip_analyzer import AnalysisRequest, analyze_task_transcript, inspect_local_analysis_plan  # noqa: E402


class FakeLocalProvider:
    name = "local"

    def __init__(self) -> None:
        self.calls = 0

    def generate_json(self, prompt: str, retry_instruction: str | None = None) -> str:
        self.calls += 1
        rows = re.findall(r"(\d{2}:\d{2}:\d{2})\s+-\s+(\d{2}:\d{2}:\d{2})", prompt)
        if not rows:
            rows = re.findall(r"\|\s*(\d{2}:\d{2}:\d{2})\s*\|\s*(\d{2}:\d{2}:\d{2})\s*\|", prompt)
        start, end = rows[0]
        duration_seconds = _time_to_seconds(end) - _time_to_seconds(start)
        return json.dumps(
            {
                "analysis_summary": "fake local chunk result",
                "clips": [
                    {
                        "clip_id": f"clip_fake_{self.calls:03d}",
                        "title": f"Candidate clip {self.calls}",
                        "start_time": start,
                        "end_time": end,
                        "duration_seconds": duration_seconds,
                        "summary": "This segment is suitable as a short-video candidate.",
                        "highlight_reason": "The topic is focused and suitable for sharing.",
                        "spread_value": "中",
                        "suggested_editing": "Keep the core sentence and add keyword subtitles.",
                        "confidence_score": 0.8,
                        "selected_by_default": True,
                    }
                ],
            },
            ensure_ascii=False,
        )


def _time_to_seconds(value: str) -> int:
    hours, minutes, seconds = [int(part) for part in value.split(":")]
    return hours * 3600 + minutes * 60 + seconds


def main() -> None:
    rows = []
    for minute in range(12):
        rows.append(
            f"| 00:{minute:02d}:00 | 00:{minute + 1:02d}:00 | "
            f"Minute {minute + 1} test transcript content for local AI chunking. |"
        )
    transcript = "\n".join(
        [
            "# Test transcript",
            "",
            "## Timed transcript",
            "",
            "| Start | End | Text |",
            "| --- | --- | --- |",
            *rows,
        ]
    )

    provider = FakeLocalProvider()
    original_build_provider = ai_clip_analyzer.build_provider
    ai_clip_analyzer.build_provider = lambda provider_name=None: provider
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "transcript.md"
            transcript_path.write_text(transcript, encoding="utf-8")
            request = AnalysisRequest(
                task_id="chunk-test",
                transcript_path=transcript_path,
                max_clip_duration_minutes=5,
                target_clip_count=5,
                ai_preference="shareable",
                provider_name="local",
            )
            plan = inspect_local_analysis_plan(request)
            result = analyze_task_transcript(request)
    finally:
        ai_clip_analyzer.build_provider = original_build_provider

    assert plan["chunk_count"] > 1, plan
    assert provider.calls == plan["chunk_count"], (provider.calls, plan)
    assert result.clips, "expected merged clips"
    assert len(result.clips) <= 5, len(result.clips)
    print("Local AI chunked analysis test passed:", json.dumps(plan, ensure_ascii=False))


if __name__ == "__main__":
    main()
