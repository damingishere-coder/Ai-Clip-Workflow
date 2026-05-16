import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.ai.ai_clip_analyzer import (  # noqa: E402
    AnalysisRequest,
    _get_transcript_bounds,
    _parse_and_validate,
    _validate_clip_constraints,
)


def main() -> None:
    with TemporaryDirectory() as temp_dir:
        transcript_path = Path(temp_dir) / "transcript.md"
        transcript_text = """# 模拟转写

| 开始 | 结束 | 文本 |
| --- | --- | --- |
| 00:00:00 | 00:01:00 | 主播开场介绍本场直播主题。 |
| 00:01:00 | 00:02:30 | 主播系统解释了提高直播完播率的三个方法。 |
| 00:02:30 | 00:03:00 | 主播总结并提醒观众复盘数据。 |
"""
        transcript_path.write_text(transcript_text, encoding="utf-8")

        raw_json = json.dumps(
            {
                "task_id": "mock-task",
                "analysis_summary": "模拟转写中 00:01:00 到 00:02:30 适合切片。",
                "clips": [
                    {
                        "clip_id": "clip_001",
                        "title": "提高直播完播率的三个方法",
                        "start_time": "00:01:00",
                        "end_time": "00:02:30",
                        "duration_seconds": 90,
                        "summary": "主播系统解释提高直播完播率的方法。",
                        "highlight_reason": "结构完整，适合独立传播。",
                        "spread_value": "高",
                        "suggested_editing": "保留问题开头，并在三点方法处加字幕。",
                        "confidence_score": 0.9,
                        "selected_by_default": True,
                    }
                ],
            },
            ensure_ascii=False,
        )
        result = _parse_and_validate(raw_json)
        request = AnalysisRequest(
            task_id="mock-task",
            transcript_path=transcript_path,
            max_clip_duration_minutes=2,
            target_clip_count=3,
            ai_preference="知识型切片",
            provider_name="mock",
        )
        _validate_clip_constraints(result, request, _get_transcript_bounds(transcript_text))
        print(f"模拟 transcript 分析校验通过，候选片段数：{len(result.clips)}")


if __name__ == "__main__":
    main()
