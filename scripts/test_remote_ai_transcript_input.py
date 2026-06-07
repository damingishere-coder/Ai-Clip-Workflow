from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.ai import ai_clip_analyzer  # noqa: E402
from app.services.ai.ai_clip_analyzer import AnalysisRequest, analyze_task_transcript  # noqa: E402


class FakeRemoteProvider:
    name = "remote"

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    def generate_json(self, prompt: str, retry_instruction: str | None = None) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        assert "MINUTE_DUPLICATE" not in prompt
        assert "SENTENCE_CANONICAL" in prompt
        return json.dumps(
            {
                "analysis_summary": "remote whole-episode analysis",
                "clips": [
                    {
                        "clip_id": "clip_001",
                        "title": "Canonical sentence clip",
                        "start_time": "00:00:10",
                        "end_time": "00:00:50",
                        "duration_seconds": 40,
                        "summary": "The canonical sentence section is used for analysis.",
                        "highlight_reason": "It verifies duplicate minute rows are ignored.",
                        "spread_value": "medium",
                        "suggested_editing": "Keep the canonical sentence lines.",
                        "confidence_score": 0.8,
                        "selected_by_default": True,
                    }
                ],
            },
            ensure_ascii=False,
        )


def main() -> None:
    transcript = """# Test transcript

## 任务信息

- 任务 ID：`remote-input-test`

## 分钟级转写

| 开始 | 结束 | 文本 |
| --- | --- | --- |
| 00:00:00 | 00:01:00 | MINUTE_DUPLICATE should not be sent to remote AI. |

## 逐句时间戳原文

| 开始 | 结束 | 文本 |
| --- | --- | --- |
| 00:00:10 | 00:00:50 | SENTENCE_CANONICAL should be sent to remote AI. |
"""
    provider = FakeRemoteProvider()
    original_build_provider = ai_clip_analyzer.build_provider
    ai_clip_analyzer.build_provider = lambda provider_name=None: provider
    try:
        with TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "transcript.md"
            transcript_path.write_text(transcript, encoding="utf-8")
            result = analyze_task_transcript(
                AnalysisRequest(
                    task_id="remote-input-test",
                    transcript_path=transcript_path,
                    max_clip_duration_minutes=2,
                    target_clip_count=1,
                    ai_preference="whole episode",
                    provider_name="remote",
                    prompt_template="{{TRANSCRIPT_TEXT}}",
                )
            )
    finally:
        ai_clip_analyzer.build_provider = original_build_provider

    assert provider.calls == 1, provider.calls
    assert len(provider.prompts) == 1
    assert len(result.clips) == 1
    print("Remote AI transcript input test passed")


if __name__ == "__main__":
    main()
