import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.ai.ai_clip_analyzer import _parse_and_validate  # noqa: E402


def _valid_payload() -> dict:
    return {
        "task_id": "demo-task",
        "analysis_summary": "demo summary",
        "clips": [
            {
                "clip_id": "clip_001",
                "title": "demo title",
                "start_time": "00:01:00",
                "end_time": "00:02:30",
                "duration_seconds": 90,
                "summary": "demo clip summary",
                "highlight_reason": "demo highlight reason",
                "spread_value": "high",
                "suggested_editing": "demo editing suggestion",
                "confidence_score": 0.92,
                "selected_by_default": True,
            }
        ],
    }


def _run_case(name: str, raw_text: str) -> None:
    result = _parse_and_validate(raw_text)
    assert len(result.clips) == 1, name
    print(f"{name}: OK")


def main() -> None:
    standard_json = json.dumps(_valid_payload(), ensure_ascii=False)
    _run_case("standard json", standard_json)

    _run_case("markdown fenced json", f"```json\n{standard_json}\n```")

    _run_case(
        "trailing commas",
        """
        {
          "analysis_summary": "demo summary",
          "clips": [
            {
              "clip_id": "clip_001",
              "title": "demo title",
              "start_time": "00:01:00",
              "end_time": "00:02:30",
              "duration_seconds": 90,
              "summary": "demo clip summary",
              "highlight_reason": "demo highlight reason",
              "spread_value": "high",
              "suggested_editing": "demo editing suggestion",
              "confidence_score": 0.92,
              "selected_by_default": true,
            },
          ],
        }
        """,
    )

    _run_case(
        "unquoted object keys",
        """
        {
          analysis_summary: "demo summary",
          clips: [
            {
              clip_id: "clip_001",
              title: "demo title",
              start_time: "00:01:00",
              end_time: "00:02:30",
              duration_seconds: 90,
              summary: "demo clip summary",
              highlight_reason: "demo highlight reason",
              spread_value: "high",
              suggested_editing: "demo editing suggestion",
              confidence_score: 0.92,
              selected_by_default: true
            }
          ]
        }
        """,
    )

    _run_case(
        "python literal",
        repr(_valid_payload()),
    )

    legacy_payload = {
        "task_id": "demo-task",
        "analysis_summary": "demo summary",
        "clips": [
            {
                "clip_key": "clip_01",
                "title": "demo title",
                "start_time": "00:01:00",
                "end_time": "00:02:30",
                "reason": "demo highlight reason",
                "viral_value": "high",
                "editing_suggestion": "demo editing suggestion",
                "confidence_score": 0.92,
                "selected_by_default": True,
            }
        ],
    }
    legacy_result = _parse_and_validate(json.dumps(legacy_payload, ensure_ascii=False))
    legacy_clip = legacy_result.clips[0]
    assert legacy_clip.clip_id == "clip_01"
    assert legacy_clip.spread_value == "high"
    assert legacy_clip.highlight_reason == "demo highlight reason"
    assert legacy_clip.suggested_editing == "demo editing suggestion"
    assert legacy_clip.duration_seconds == 90
    print("legacy ai field aliases: OK")

    ten_point_payload = _valid_payload()
    ten_point_payload["clips"][0]["confidence_score"] = 8.9
    ten_point_result = _parse_and_validate(json.dumps(ten_point_payload, ensure_ascii=False))
    assert ten_point_result.clips[0].confidence_score == 0.89
    print("ten point confidence score: OK")

    percent_payload = _valid_payload()
    percent_payload["clips"][0]["confidence_score"] = "92%"
    percent_result = _parse_and_validate(json.dumps(percent_payload, ensure_ascii=False))
    assert percent_result.clips[0].confidence_score == 0.92
    print("percent confidence score: OK")


if __name__ == "__main__":
    main()
