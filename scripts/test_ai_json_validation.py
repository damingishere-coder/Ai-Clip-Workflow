import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.ai.ai_clip_analyzer import _parse_and_validate  # noqa: E402


def main() -> None:
    raw_json = json.dumps(
        {
            "task_id": "demo-task",
            "analysis_summary": "示例转写中存在一个完整观点片段。",
            "clips": [
                {
                    "clip_id": "clip_001",
                    "title": "如何提高直播完播率",
                    "start_time": "00:01:00",
                    "end_time": "00:02:30",
                    "duration_seconds": 90,
                    "summary": "主播解释了提高完播率的三个方法。",
                    "highlight_reason": "观点集中，逻辑完整。",
                    "spread_value": "高",
                    "suggested_editing": "保留开头问题句，结尾加总结字幕。",
                    "confidence_score": 0.92,
                    "selected_by_default": True,
                }
            ],
        },
        ensure_ascii=False,
    )
    result = _parse_and_validate(raw_json)
    print(f"JSON 校验通过，候选片段数：{len(result.clips)}")


if __name__ == "__main__":
    main()
