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
        transcript = prompt.split("转写文本：", 1)[-1]
        times = re.findall(r"\b\d{2}:\d{2}:\d{2}\b", transcript)
        start = times[0]
        end = times[1] if len(times) > 1 else times[0]
        return json.dumps(
            {
                "task_id": "chunk-test",
                "analysis_summary": "fake local chunk result",
                "clips": [
                    {
                        "clip_id": f"clip_fake_{self.calls:03d}",
                        "title": f"候选片段 {self.calls}",
                        "start_time": start,
                        "end_time": end,
                        "duration_seconds": 60,
                        "summary": "这一段适合作为短视频候选。",
                        "highlight_reason": "观点集中，适合传播。",
                        "spread_value": "中",
                        "suggested_editing": "保留核心对话并补字幕。",
                        "confidence_score": 0.8,
                        "selected_by_default": True,
                    }
                ],
            },
            ensure_ascii=False,
        )


def main() -> None:
    rows = []
    for minute in range(12):
        rows.append(
            f"| 00:{minute:02d}:00 | 00:{minute + 1:02d}:00 | "
            f"这是第 {minute + 1} 分钟的测试转写内容，用来验证本地 AI 会被拆成小段处理。 |"
        )
    transcript = "\n".join(
        [
            "# 测试转写",
            "",
            "## 分钟级转写",
            "",
            "| 开始 | 结束 | 文本 |",
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
                ai_preference="传播性强",
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
    print("本地 AI 分段分析测试通过：", json.dumps(plan, ensure_ascii=False))


if __name__ == "__main__":
    main()
