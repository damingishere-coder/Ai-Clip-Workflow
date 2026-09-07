from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.ai import ai_clip_analyzer, variety_comedy_analyzer
from app.services.ai.unit_checkpoint import AIUnitExecution
from app.services.ai.ai_clip_analyzer import AnalysisRequest, TranscriptChunk, TranscriptRow
from app.services.ai.variety_comedy_analyzer import (
    ComedyAnalysisRequest,
    ComedyTranscriptWindow,
)
from app.services.ai_analysis_workflow_service import validate_ai_analysis_meta_for_cut
from app.services.pipeline_engine import PipelineEngine
from app.services.video_cut_workflow_service import process_task_video_cuts


def _valid_general_payload(task_id: str) -> str:
    return json.dumps(
        {
            "task_id": task_id,
            "analysis_summary": "ok",
            "clips": [
                {
                    "clip_id": "clip-raw",
                    "title": "完整片段",
                    "start_time": "00:00:10",
                    "end_time": "00:01:10",
                    "duration_seconds": 60,
                    "cover_time_seconds": 30,
                    "summary": "摘要",
                    "highlight_reason": "亮点",
                    "spread_value": "高",
                    "suggested_editing": "保留上下文",
                    "confidence_score": 0.9,
                }
            ],
        },
        ensure_ascii=False,
    )


def test_general_partial_chunk_failure_is_structured_and_not_retried(monkeypatch, tmp_path: Path):
    task_id = "test-partial-general"
    transcript = tmp_path / "transcript.md"
    transcript.write_text("00:00:00 - 00:20:00 测试正文", encoding="utf-8")

    class Provider:
        name = "remote"

        def __init__(self):
            self.calls = 0

        def generate_json(self, _prompt: str) -> str:
            self.calls += 1
            return _valid_general_payload(task_id) if self.calls == 1 else "not-json"

    provider = Provider()
    monkeypatch.setattr(ai_clip_analyzer, "build_provider", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(
        ai_clip_analyzer,
        "_build_local_analysis_chunks",
        lambda *_args, **_kwargs: [
            TranscriptChunk(1, 2, 0, 600, "00:00:00 - 00:10:00 第一段", 30),
            TranscriptChunk(2, 2, 600, 1200, "00:10:00 - 00:20:00 第二段", 30),
        ],
    )

    result = ai_clip_analyzer.analyze_task_transcript(
        AnalysisRequest(
            task_id=task_id,
            transcript_path=transcript,
            max_clip_duration_minutes=3,
            target_clip_count=2,
            ai_preference="",
            provider_name="remote",
        )
    )

    assert provider.calls == 2
    assert len(result.clips) == 1
    assert result.analysis_meta["analysis_incomplete"] is True
    assert result.analysis_meta["expected_units"] == 2
    assert result.analysis_meta["completed_units"] == 1
    assert result.analysis_meta["coverage_ratio"] == 0.5
    assert result.analysis_meta["failed_stages"][0]["unit_id"] == "chunk_002"


def test_variety_partial_recall_failure_is_structured(monkeypatch, tmp_path: Path):
    rows = [TranscriptRow("00:00:00", "00:03:00", 0, 180, "综艺正文")]
    windows = [
        ComedyTranscriptWindow(1, 2, 0, 90, tuple(rows), "第一窗"),
        ComedyTranscriptWindow(2, 2, 90, 180, tuple(rows), "第二窗"),
    ]
    moment = {
        "source_id": "moment-1",
        "key_time": "00:01:00",
        "key_seconds": 60,
        "title": "笑点",
        "topic_key": "综艺",
        "humor_reason": "反差",
        "recall_score": 90,
    }
    expanded = {
        "source_id": "moment-1",
        "title": "完整笑点",
        "start_time": "00:00:10",
        "end_time": "00:01:20",
        "duration_seconds": 70,
        "key_moment_time": "00:01:00",
        "topic_key": "综艺",
        "summary": "摘要",
        "highlight_reason": "反差",
        "arc_structure": "铺垫到反转",
        "suggested_editing": "保留反应",
    }
    monkeypatch.setattr(variety_comedy_analyzer, "_read_transcript", lambda _path: "正文")
    monkeypatch.setattr(variety_comedy_analyzer, "_extract_transcript_rows", lambda _text: rows)
    monkeypatch.setattr(variety_comedy_analyzer, "build_provider", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(variety_comedy_analyzer, "build_comedy_windows", lambda *_args, **_kwargs: windows)
    monkeypatch.setattr(
        variety_comedy_analyzer,
        "_recall_moments",
        lambda *_args, **_kwargs: (
            [moment],
            ["召回窗口 2/2 跳过：invalid JSON"],
            {"expected_units": 2, "completed_units": 1, "failed_units": 1, "empty_unit_count": 0, "invalid_item_count": 0},
        ),
    )
    monkeypatch.setattr(
        variety_comedy_analyzer,
        "_expand_moments",
        lambda *_args, **_kwargs: (
            [expanded],
            [],
            {"expected_units": 1, "completed_units": 1, "failed_units": 0, "empty_unit_count": 0, "invalid_item_count": 0},
        ),
    )
    monkeypatch.setattr(variety_comedy_analyzer, "analyze_audio_reaction", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(variety_comedy_analyzer, "list_recent_feedback_context", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(variety_comedy_analyzer, "_global_judge", lambda *_args, **_kwargs: ({}, ""))
    monkeypatch.setattr(
        variety_comedy_analyzer,
        "score_comedy_candidate",
        lambda candidate, _judge: {
            **candidate,
            "quality_tier": "A",
            "quality_score": 90,
            "text_quality_score": 90,
            "humor_score": 90,
            "completeness_score": 90,
            "audio_reaction_score": 0,
            "quality_evidence": {},
            "rejection_reason": "",
            "selected_by_default": False,
        },
    )

    result = variety_comedy_analyzer.analyze_variety_comedy(
        ComedyAnalysisRequest(
            task_id="test-partial-variety",
            transcript_path=tmp_path / "transcript.md",
            audio_path=tmp_path / "audio.wav",
            candidate_pool_limit=5,
            final_clip_target=1,
            ai_preference="",
            provider_name="remote",
        )
    )

    assert len(result.clips) == 1
    assert result.analysis_meta["analysis_incomplete"] is True
    assert result.analysis_meta["expected_units"] == 4
    assert result.analysis_meta["completed_units"] == 3
    assert result.analysis_meta["failed_units"] == 1
    assert result.analysis_meta["failed_stages"][0]["stage"] == "recall"


@pytest.mark.parametrize("profile", ["general", "variety_comedy"])
def test_auto_pipeline_blocks_incomplete_analysis_for_every_profile(monkeypatch, profile: str):
    engine = PipelineEngine()
    monkeypatch.setattr(engine, "_get_task", lambda _task_id: {"selection_profile": profile})
    monkeypatch.setattr(
        "app.services.pipeline_engine.task_service.get_task_ai_analysis_meta",
        lambda _task_id: {"analysis_incomplete": True, "coverage_ratio": 0.5, "coverage_percent": 50},
    )

    with pytest.raises(ValueError, match="不会进入自动切片"):
        engine._select_clips("test-partial-gate", {"config": {}})


@pytest.mark.parametrize("profile", ["general", "variety_comedy"])
def test_manual_cut_blocks_incomplete_analysis_for_every_profile(monkeypatch, profile: str):
    monkeypatch.setattr(
        "app.services.task_service.get_task",
        lambda *_args, **_kwargs: {"id": "test-partial-cut", "selection_profile": profile},
    )
    monkeypatch.setattr(
        "app.services.ai_analysis_workflow_service.get_task_ai_analysis_meta",
        lambda _task_id: {"analysis_incomplete": True, "coverage_ratio": 0.5, "coverage_percent": 50},
    )

    with pytest.raises(ValueError, match="不会生成切片"):
        process_task_video_cuts("test-partial-cut")


def test_quality_degraded_analysis_is_manual_review_only(monkeypatch):
    engine = PipelineEngine()
    monkeypatch.setattr(engine, "_get_task", lambda _task_id: {"selection_profile": "variety_comedy"})
    degraded = {
        "schema_version": 2,
        "selection_profile": "variety_comedy",
        "analysis_incomplete": False,
        "quality_degraded": True,
        "coverage_ratio": 1.0,
        "coverage_percent": 100,
    }
    monkeypatch.setattr(
        "app.services.pipeline_engine.task_service.get_task_ai_analysis_meta",
        lambda _task_id: degraded,
    )
    with pytest.raises(ValueError, match="质量评审未完整通过"):
        engine._select_clips("test-quality-gate", {"config": {}})

    monkeypatch.setattr(
        "app.services.task_service.get_task",
        lambda *_args, **_kwargs: {"id": "test-quality-cut", "selection_profile": "variety_comedy"},
    )
    monkeypatch.setattr(
        "app.services.ai_analysis_workflow_service.get_task_ai_analysis_meta",
        lambda _task_id: degraded,
    )
    with pytest.raises(ValueError, match="质量评审未完整通过"):
        process_task_video_cuts("test-quality-cut")


def test_complete_long_live_analysis_meta_passes_cut_validation():
    meta = {
        "schema_version": 2,
        "selection_profile": "long_live_talk",
        "analysis_incomplete": False,
        "quality_degraded": False,
        "coverage_ratio": 1.0,
        "coverage_percent": 100.0,
        "invalid_item_count": 0,
        "window_count": 2,
        "completed_window_count": 2,
        "failed_window_count": 0,
        "failed_windows": [],
    }

    assert validate_ai_analysis_meta_for_cut(meta, "long_live_talk") == meta


def test_missing_analysis_meta_blocks_manual_and_auto_cut(monkeypatch):
    engine = PipelineEngine()
    monkeypatch.setattr(engine, "_get_task", lambda _task_id: {"selection_profile": "general"})
    monkeypatch.setattr(
        "app.services.pipeline_engine.task_service.get_task_ai_analysis_meta",
        lambda _task_id: {},
    )
    with pytest.raises(ValueError, match="缺少可信的完整性元数据"):
        engine._select_clips("test-missing-meta-auto", {"config": {}})

    monkeypatch.setattr(
        "app.services.task_service.get_task",
        lambda *_args, **_kwargs: {"id": "test-missing-meta-cut", "selection_profile": "general"},
    )
    monkeypatch.setattr(
        "app.services.ai_analysis_workflow_service.get_task_ai_analysis_meta",
        lambda _task_id: {},
    )
    with pytest.raises(ValueError, match="缺少可信的完整性元数据"):
        process_task_video_cuts("test-missing-meta-cut")


def test_analysis_profile_mismatch_fails_closed():
    meta = {
        "schema_version": 2,
        "selection_profile": "general",
        "analysis_incomplete": False,
        "quality_degraded": False,
        "coverage_ratio": 1.0,
        "coverage_percent": 100.0,
    }

    result = validate_ai_analysis_meta_for_cut(meta, "long_live_talk")
    assert result["analysis_incomplete"] is True
    assert result["quality_degraded"] is True
    assert result["integrity_error"] == "selection_profile_mismatch"


def test_complete_analysis_requires_structured_unit_evidence():
    meta = {
        "schema_version": 2,
        "selection_profile": "general",
        "analysis_incomplete": False,
        "quality_degraded": False,
        "coverage_ratio": 1.0,
        "coverage_percent": 100.0,
        "invalid_item_count": 0,
    }

    result = validate_ai_analysis_meta_for_cut(meta, "general")

    assert result["analysis_incomplete"] is True
    assert result["integrity_error"] == "analysis_unit_evidence_invalid"


def test_complete_analysis_coverage_must_match_unit_ledger():
    meta = {
        "schema_version": 2,
        "selection_profile": "general",
        "analysis_incomplete": False,
        "quality_degraded": False,
        "coverage_ratio": 0.5,
        "coverage_percent": 50.0,
        "expected_units": 2,
        "completed_units": 2,
        "failed_units": 0,
        "failed_stages": [],
        "invalid_item_count": 0,
    }

    result = validate_ai_analysis_meta_for_cut(meta, "general")

    assert result["analysis_incomplete"] is True
    assert result["integrity_error"] == "analysis_unit_evidence_invalid"


def test_corrupt_replayed_variety_unit_is_not_counted_complete(monkeypatch):
    window = ComedyTranscriptWindow(
        1,
        1,
        0,
        60,
        (TranscriptRow("00:00:00", "00:01:00", 0, 60, "正文"),),
        "正文",
    )
    monkeypatch.setattr(
        variety_comedy_analyzer,
        "execute_checkpointed_ai_unit",
        lambda **_kwargs: AIUnitExecution(status="completed", payload={}, reused=True),
    )

    moments, failures, stats = variety_comedy_analyzer._recall_moments(
        object(),
        [window],
        "",
        task_id="test-corrupt-variety-replay",
        input_fingerprint="stable",
    )

    assert moments == []
    assert stats["completed_units"] == 0
    assert stats["failed_units"] == 1
    assert "moments 不是数组" in failures[0]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2.9),
        ("coverage_ratio", True),
        ("coverage_percent", True),
        ("coverage_ratio", float("nan")),
        ("coverage_percent", float("inf")),
        ("coverage_percent", 99.0),
    ],
)
def test_noncanonical_analysis_meta_fails_closed(field: str, value: object):
    meta = {
        "schema_version": 2,
        "selection_profile": "general",
        "analysis_incomplete": False,
        "quality_degraded": False,
        "coverage_ratio": 1.0,
        "coverage_percent": 100.0,
    }
    meta[field] = value

    result = validate_ai_analysis_meta_for_cut(meta, "general")

    assert result["analysis_incomplete"] is True
    assert result["quality_degraded"] is True
    assert result["integrity_error"] in {"analysis_schema_invalid", "analysis_coverage_invalid"}


def test_variety_global_judge_requires_complete_candidate_coverage():
    candidates = []
    for source_id in ("candidate-a", "candidate-b"):
        candidates.append(
            {
                "source_id": source_id,
                "title": source_id,
                "start_time": "00:00:00",
                "end_time": "00:01:00",
                "duration_seconds": 60,
                "topic_key": "topic",
                "summary": "summary",
                "highlight_reason": "reason",
                "arc_structure": "arc",
                "audio_evidence": {},
            }
        )

    class Provider:
        def generate_json(self, _prompt: str, retry_instruction: str | None = None) -> str:
            del retry_instruction
            return json.dumps({"ranked_clips": [{"source_id": "candidate-a"}]})

    judged, warning = variety_comedy_analyzer._global_judge(
        Provider(),
        candidates,
        "",
        [],
        task_id="test-partial-judge",
        input_fingerprint="stable-input",
    )

    assert set(judged) == {"candidate-a"}
    assert "缺少 1 个候选" in warning
