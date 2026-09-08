from copy import deepcopy
import json

import pytest

from app.services.ai import content_decision_analyzer as analyzer
from app.services.ai.ai_clip_analyzer import TranscriptRow, AIAnalysisError
from app.services.ai.variety_comedy_analyzer import ComedyAnalysisRequest
from app.services.pipeline_engine import PipelineEngine


def rows():
    return [
        TranscriptRow(
            "00:00:00",
            "00:00:05",
            0,
            5,
            "你说什么？我说这是梦想。大家追问，他解释完毕。",
        )
    ]


def item(source="w001_m001", decision="publish"):
    return {
        "source_id": source,
        "title": "完整话题",
        "topic_key": "话题",
        "start_time": "00:00:00",
        "end_time": "00:00:05",
        "key_moment_time": "00:00:02",
        "summary": "完整对话",
        "highlight_reason": "反转",
        "arc_structure": "问题→回答→回应",
        "suggested_editing": "连续截取",
        **{k: 99 for k in analyzer.SCORE_FIELDS},
        "decision": decision,
        "decision_reason": "已核对" if decision == "publish" else "当前边界待审核",
        "review_issues": ["缺少最后两秒证据"] if decision == "review" else [],
        "duplicate_of": "",
        "evidence": [
            {
                "role": role,
                "start_time": "00:00:00",
                "end_time": "00:00:05",
                "quote": "大家追问，他解释完毕。",
            }
            for role in analyzer.ROLES
        ],
    }


@pytest.mark.parametrize(
    "value", ["forty", "70", True, None, -1, 101, float("nan"), float("inf")]
)
def test_scores_strict(value):
    candidate = item()
    candidate["hook_score"] = value
    with pytest.raises(AIAnalysisError):
        analyzer.validate_decisions(
            {"clips": [candidate]}, {candidate["source_id"]: rows()}, final=True
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "duplicate",
        "missing",
        "text",
        "boundary",
        "evidence",
        "issue",
        "role",
    ],
)
def test_invalid_decisions_rejected(mutation):
    candidate = item()
    payload = {"clips": [candidate]}
    if mutation == "unknown":
        candidate["source_id"] = "other"
    if mutation == "duplicate":
        payload["clips"].append(deepcopy(candidate))
    if mutation == "missing":
        payload["clips"] = []
    if mutation == "text":
        candidate["decision"] = "good"
    if mutation == "boundary":
        candidate["end_time"] = "00:00:06"
    if mutation == "evidence":
        candidate["evidence"][0]["quote"] = "编造的笑声"
    if mutation == "issue":
        candidate["review_issues"] = ["当前边界待审核"]
    if mutation == "role":
        candidate["evidence"].pop()
    with pytest.raises(AIAnalysisError):
        analyzer.validate_decisions(payload, {"w001_m001": rows()}, final=True)


def test_high_score_review_preserves_raw_opinion_and_short_bounds():
    candidate = item(decision="review")
    analyzer.validate_decisions(
        {"clips": [candidate]}, {candidate["source_id"]: rows()}, final=True
    )
    result = analyzer._payload(candidate, 1)
    assert result["selected_by_default"] is False
    assert result["duration_seconds"] == 5
    assert result["decision_reason"] == "当前边界待审核"
    assert result["rejection_reason"] == "缺少最后两秒证据"
    assert result["quality_evidence"]["ai_original_decision"] == candidate


def test_consecutive_subtitle_lines_can_support_one_sentence_without_guessing():
    context = [
        TranscriptRow("00:00:00", "00:00:02", 0, 2, "就是赵哥的粉丝"),
        TranscriptRow("00:00:02", "00:00:05", 2, 5, "绝对不是只有五十个而已"),
    ]
    candidate = item()
    for point in candidate["evidence"]:
        point["quote"] = "就是赵哥的粉丝，绝对不是只有五十个而已。"
    analyzer.validate_decisions(
        {"clips": [candidate]}, {candidate["source_id"]: context}, final=True
    )
    candidate["evidence"][0]["quote"] = "就是赵哥的粉丝，只有五十个而已。"
    with pytest.raises(AIAnalysisError, match="原文不一致"):
        analyzer.validate_decisions(
            {"clips": [candidate]}, {candidate["source_id"]: context}, final=True
        )


def test_rejected_duplicate_can_reference_review_and_keep_uncertain_end_point():
    candidate = item(decision="reject")
    candidate["duplicate_of"] = "retained-review"
    candidate["key_moment_time"] = candidate["end_time"]
    analyzer.validate_decisions(
        {"clips": [candidate]},
        {candidate["source_id"]: rows()},
        final=True,
        catalog_ids={"retained-review"},
    )
    assert analyzer._payload(candidate, 1)["selected_by_default"] is False
    candidate["decision"] = "publish"
    candidate["duplicate_of"] = ""
    with pytest.raises(AIAnalysisError):
        analyzer.validate_decisions(
            {"clips": [candidate]}, {candidate["source_id"]: rows()}, final=True
        )


@pytest.mark.parametrize("count", [0, 2, 20])
def test_full_analysis_has_no_quota_or_minimum(monkeypatch, tmp_path, count):
    monkeypatch.setattr(analyzer, "_extract_transcript_rows", lambda text: rows())
    monkeypatch.setattr(analyzer, "_read_transcript", lambda path: "same transcript")
    seen = []

    class Provider:
        def generate_json_with_schema(
            self, prompt, output_schema, retry_instruction=None
        ):
            seen.append(prompt)
            if output_schema == analyzer.RECALL_OUTPUT_SCHEMA:
                return json.dumps(
                    {
                        "moments": [
                            {
                                "key_time": "00:00:02",
                                "title": str(i),
                                "topic_key": str(i),
                                "humor_reason": "依据",
                                "recall_score": 80,
                            }
                            for i in range(count)
                        ]
                    }
                )
            import re

            source = re.search(r'"source_id": "(w\d+_m\d+)"', prompt).group(1)
            candidate = item(source)
            if output_schema == analyzer.EXPANSION_OUTPUT_SCHEMA:
                candidate = {
                    k: v
                    for k, v in candidate.items()
                    if k in output_schema["properties"]["clips"]["items"]["properties"]
                }
            return json.dumps({"clips": [candidate]})

    monkeypatch.setattr(analyzer, "build_provider", lambda name: Provider())
    result = analyzer.analyze_content_decisions(
        ComedyAnalysisRequest(
            "test",
            tmp_path / "t",
            tmp_path / "a",
            12,
            5,
            "",
            "codex",
            "完整基线规则ABC",
            "content",
        )
    )
    assert len(result.clips) == count
    assert sum(c.selected_by_default for c in result.clips) == count
    assert result.analysis_meta["analysis_incomplete"] is False
    assert result.analysis_meta["coverage_percent"] == 100
    assert all("完整基线规则ABC" in p for p in seen)


@pytest.mark.parametrize("count,review", [(0, 0), (0, 1), (2, 1), (20, 1)])
def test_auto_selection_obeys_decision_even_with_99_score(
    monkeypatch, tmp_path, count, review
):
    from app.services import pipeline_engine

    monkeypatch.setattr(
        pipeline_engine,
        "get_artifact_paths",
        lambda task: {"analysis_path": tmp_path / "candidates.json"},
    )
    engine = PipelineEngine()
    selected_ids = []
    monkeypatch.setattr(
        engine, "_update_selected_clips", lambda task, ids: selected_ids.extend(ids)
    )
    candidates = [{**item(str(i)), "id": str(i)} for i in range(count)]
    candidates.append({**item("manual-disabled"), "id": "manual-disabled", "reviewed": True, "enabled": False})
    candidates.extend(
        {**item(f"review-{i}", "review"), "id": f"review-{i}", "quality_score": 99}
        for i in range(review)
    )
    result = engine._select_content_decisions("test", candidates, {})
    assert len(selected_ids) == count
    assert result["content_terminal"] == (count == 0)
    assert result["review_count"] == review


def test_new_tasks_default_content_but_old_db_rows_remain_legacy():
    from app.models.task import TaskCreate
    from app.db.database import get_connection

    assert (
        TaskCreate(
            task_name="new", selection_profile="variety_comedy"
        ).selection_count_mode
        == "content"
    )
    with get_connection() as connection:
        default = next(
            r[4]
            for r in connection.execute("PRAGMA table_info(tasks)")
            if r[1] == "selection_count_mode"
        )
    assert default == "'legacy'"
