"""康熙笑点优先 V2 的核心选片、音频与启用规则测试。"""

from __future__ import annotations

from array import array
import json
from pathlib import Path
import wave

from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.main import app
from app.services.ai.ai_clip_analyzer import TranscriptRow
from app.services.ai.variety_comedy_analyzer import (
    ComedyAnalysisRequest,
    analyze_variety_comedy,
    build_comedy_windows,
    dedupe_recall_moments,
    dedupe_scored_candidates,
    normalize_clip_bounds,
    score_comedy_candidate,
)
from app.services.audio_reaction_service import analyze_audio_reaction
from app.services.clip_feedback_service import list_recent_feedback_context, save_clip_feedback
from app.services.pipeline_engine import PipelineEngine
from app.services.storage_service import get_artifact_paths
from app.models.task import ClipFeedbackCreate


PREFIX = "test-comedy-v2-"


@pytest.fixture(autouse=True)
def comedy_v2_db_cleanup():
    init_db()
    _cleanup_rows()
    yield
    _cleanup_rows()


def _cleanup_rows() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM clip_feedback WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM ai_analysis_runs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _row(start: int, end: int, text: str = "对话") -> TranscriptRow:
    return TranscriptRow(
        start_time=_time(start),
        end_time=_time(end),
        start_seconds=start,
        end_seconds=end,
        text=text,
    )


def _time(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _insert_task(task_id: str, *, profile: str = "variety_comedy", final_target: int = 5) -> None:
    now = "2026-08-01T10:00:00"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, max_clip_duration, candidate_clip_count,
                selection_profile, final_clip_target, created_at, updated_at
            ) VALUES (?, ?, ?, 10, 12, ?, ?, ?, ?)
            """,
            (task_id, task_id, task_id, profile, final_target, now, now),
        )
        connection.commit()


def _insert_candidate(
    task_id: str,
    index: int,
    *,
    tier: str,
    quality: float,
    selected_by_default: bool,
) -> str:
    clip_id = f"{task_id}-clip-{index}"
    start = index * 180
    now = "2026-08-01T10:00:00"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO clip_candidates (
                id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                summary, highlight_reason, confidence_score, quality_tier, quality_score,
                humor_score, completeness_score, selected_by_default, enabled,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 90, ?, ?, ?, ?, ?, 90, 90, ?, ?, ?, ?)
            """,
            (
                clip_id,
                task_id,
                f"clip_{index:03d}",
                f"片段 {index}",
                _time(start),
                _time(start + 90),
                "完整笑点摘要",
                "有铺垫、反转和反应",
                quality / 100,
                tier,
                quality,
                1 if selected_by_default else 0,
                1 if selected_by_default else 0,
                now,
                now,
            ),
        )
        connection.commit()
    return clip_id


def _base_candidate(**overrides) -> dict:
    candidate = {
        "source_id": "w001_m01",
        "title": "小 S 补刀后全场笑",
        "start_time": "00:01:00",
        "end_time": "00:02:20",
        "duration_seconds": 80,
        "key_moment_time": "00:01:35",
        "topic_key": "嘉宾忘词",
        "summary": "嘉宾先认真解释，随后自曝忘词，主持人补刀后全场笑。",
        "highlight_reason": "严肃铺垫与忘词反转形成反差。",
        "arc_structure": "铺垫→忘词反转→主持人补刀→全场反应",
        "suggested_editing": "保留补刀后反应。",
        "humor_score": 90,
        "interaction_reaction_score": 88,
        "completeness_score": 90,
        "hook_score": 82,
        "novelty_score": 80,
        "title_score": 84,
        "audio_evidence": {"available": True, "score": 92, "labels": ["笑点后音量突增"]},
    }
    candidate.update(overrides)
    return candidate


def test_remote_windows_are_five_minutes_with_sixty_second_overlap():
    rows = [_row(second, second + 10, f"第 {second // 10} 句") for second in range(0, 900, 10)]
    windows = build_comedy_windows(rows, provider_name="remote")

    assert len(windows) >= 4
    assert all(window.end_seconds - window.start_seconds <= 300 for window in windows)
    for previous, current in zip(windows, windows[1:], strict=False):
        assert current.start_seconds < previous.end_seconds
        assert previous.end_seconds - current.start_seconds >= 50


def test_local_windows_are_three_minutes_with_overlap():
    rows = [_row(second, second + 10) for second in range(0, 600, 10)]
    windows = build_comedy_windows(rows, provider_name="local")

    assert all(window.end_seconds - window.start_seconds <= 180 for window in windows)
    assert all(
        current.start_seconds < previous.end_seconds
        for previous, current in zip(windows, windows[1:], strict=False)
    )


def test_cross_window_recall_keeps_only_one_copy_of_same_moment():
    moments = [
        {"source_id": "w1", "key_seconds": 295, "recall_score": 82, "topic_key": "同一笑点"},
        {"source_id": "w2", "key_seconds": 302, "recall_score": 94, "topic_key": "同一笑点"},
    ]

    result = dedupe_recall_moments(moments)

    assert len(result) == 1
    assert result[0]["source_id"] == "w2"


def test_clip_bounds_expand_to_complete_sixty_to_one_hundred_fifty_seconds():
    rows = [_row(second, second + 10) for second in range(0, 300, 10)]

    bounds = normalize_clip_bounds(92, 112, 102, rows)

    assert bounds is not None
    start, end = bounds
    assert 60 <= end - start <= 150
    assert start <= 102 < end


def test_sentence_boundary_snap_does_not_shrink_clip_below_sixty_seconds():
    rows = [_row(second, second + 7) for second in range(0, 280, 7)]

    bounds = normalize_clip_bounds(69, 126, 98, rows)

    assert bounds is not None
    start, end = bounds
    assert 60 <= end - start <= 150


def test_global_dedupe_keeps_highest_quality_complete_version():
    best = {**_base_candidate(), "source_id": "best", "quality_score": 91}
    short = {
        **_base_candidate(),
        "source_id": "short",
        "start_time": "00:01:25",
        "end_time": "00:02:10",
        "quality_score": 81,
    }

    result = dedupe_scored_candidates([short, best])

    assert [item["source_id"] for item in result] == ["best"]


def test_program_score_applies_weights_and_a_grade_gates():
    result = score_comedy_candidate(_base_candidate(), {})

    assert result["quality_tier"] == "A"
    assert result["quality_score"] >= 78
    assert result["humor_score"] >= 75
    assert result["completeness_score"] >= 70


def test_audio_only_adds_and_cannot_bypass_humor_gate():
    text_only = score_comedy_candidate(
        _base_candidate(audio_evidence={"available": False, "score": 0}),
        {},
    )
    weak_humor = score_comedy_candidate(
        _base_candidate(humor_score=60, audio_evidence={"available": True, "score": 100}),
        {},
    )
    weak_audio = score_comedy_candidate(
        _base_candidate(audio_evidence={"available": True, "score": 5}),
        {},
    )

    assert weak_audio["quality_score"] == text_only["quality_score"]
    assert weak_humor["quality_tier"] != "A"


def test_missing_audio_degrades_to_text_review(tmp_path: Path):
    result = analyze_audio_reaction(tmp_path / "missing.wav", 0, 60, 30, [])

    assert result["available"] is False
    assert result["score"] == 0
    assert "音频" in result["reason"]


def test_synthetic_audio_detects_loudness_burst_and_reaction_density(tmp_path: Path):
    audio_path = tmp_path / "reaction.wav"
    sample_rate = 16_000
    quiet = array("h", [300 if index % 2 else -300 for index in range(sample_rate * 2)])
    loud = array("h", [12_000 if index % 2 else -12_000 for index in range(sample_rate * 2)])
    with wave.open(str(audio_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes((quiet + loud).tobytes())
    transcript_rows = [_row(0, 2, "他说完大家哈哈哈"), _row(2, 4, "真的太突然了")]

    result = analyze_audio_reaction(audio_path, 0, 4, 2, transcript_rows)

    assert result["available"] is True
    assert result["score"] > 40
    assert result["reaction_loudness_ratio"] > 5
    assert result["laughter_token_count"] >= 1


def test_synthetic_silence_is_available_but_does_not_fake_a_reaction(tmp_path: Path):
    audio_path = tmp_path / "silence.wav"
    sample_rate = 16_000
    with wave.open(str(audio_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(array("h", [0] * sample_rate * 3).tobytes())

    result = analyze_audio_reaction(audio_path, 0, 3, 1.5, [])

    assert result["available"] is True
    assert result["score"] == 0
    assert result["silence_ratio"] == 1


class _FakeComedyProvider:
    def generate_json(self, prompt: str, *, retry_instruction: str | None = None) -> str:
        if "只做宽召回" in prompt:
            return json.dumps(
                {
                    "moments": [
                        {
                            "key_time": "00:01:30",
                            "title": "忘词反转",
                            "topic_key": "嘉宾忘词",
                            "humor_reason": "认真铺垫后突然忘词，主持人补刀。",
                            "recall_score": 92,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        if "综艺短视频剪辑导演" in prompt:
            return json.dumps(
                {
                    "clips": [
                        {
                            "source_id": "w001_m01",
                            "title": "嘉宾认真半天却忘词",
                            "start_time": "00:00:50",
                            "end_time": "00:02:10",
                            "key_moment_time": "00:01:30",
                            "topic_key": "嘉宾忘词",
                            "summary": "嘉宾认真铺垫后忘词，小 S 接话补刀，众人笑完自然收尾。",
                            "highlight_reason": "认真与忘词形成反差。",
                            "arc_structure": "认真铺垫→忘词→补刀与笑声→解释收尾",
                            "suggested_editing": "保留补刀后的现场反应。",
                            "humor_score": 90,
                            "interaction_reaction_score": 90,
                            "completeness_score": 92,
                            "hook_score": 85,
                            "novelty_score": 82,
                            "title_score": 86,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "ranked_clips": [
                    {
                        "source_id": "w001_m01",
                        "title": "嘉宾认真解释半天，最后一句把小 S 逗笑",
                        "topic_key": "嘉宾忘词",
                        "humor_score": 92,
                        "interaction_reaction_score": 91,
                        "completeness_score": 93,
                        "hook_score": 86,
                        "novelty_score": 84,
                        "title_score": 88,
                        "arc_structure": "铺垫→忘词反转→补刀笑声→自然收尾",
                        "why_selected": "反差明确，补刀和笑后解释都完整。",
                        "rejection_reason": "",
                    }
                ]
            },
            ensure_ascii=False,
        )


def test_three_stage_flow_allows_weak_episode_to_select_less_than_target(monkeypatch, tmp_path: Path):
    transcript_path = tmp_path / "transcript.md"
    lines = ["## 逐句时间戳原文", "", "| 开始 | 结束 | 原文 |", "|---|---|---|"]
    lines.extend(
        f"| {_time(second)} | {_time(second + 10)} | 第 {second // 10} 句对话 |"
        for second in range(0, 240, 10)
    )
    transcript_path.write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setattr("app.services.ai.variety_comedy_analyzer.build_provider", lambda _: _FakeComedyProvider())
    monkeypatch.setattr(
        "app.services.ai.variety_comedy_analyzer.analyze_audio_reaction",
        lambda *args, **kwargs: {"available": True, "score": 95, "labels": ["模拟笑声反应"]},
    )
    monkeypatch.setattr("app.services.ai.variety_comedy_analyzer.list_recent_feedback_context", lambda *args, **kwargs: [])

    result = analyze_variety_comedy(
        ComedyAnalysisRequest(
            task_id=f"{PREFIX}flow",
            transcript_path=transcript_path,
            audio_path=tmp_path / "not-needed.wav",
            candidate_pool_limit=12,
            final_clip_target=5,
            ai_preference="更喜欢主持人补刀",
            provider_name="remote",
            prompt_template="保留完整笑点，不要凑数。",
        )
    )

    assert len(result.clips) == 1
    assert result.clips[0].selected_by_default is True
    assert result.clips[0].quality_tier == "A"
    assert 60 <= result.clips[0].duration_seconds <= 150


def test_auto_pipeline_only_enables_selected_a_grade_clips():
    task_id = f"{PREFIX}auto"
    _insert_task(task_id, final_target=5)
    get_artifact_paths(task_id)["analysis_path"].parent.mkdir(parents=True, exist_ok=True)
    selected_ids = {
        _insert_candidate(task_id, index, tier="A", quality=95 - index, selected_by_default=True)
        for index in range(1, 5)
    }
    _insert_candidate(task_id, 5, tier="A", quality=99, selected_by_default=False)
    _insert_candidate(task_id, 6, tier="B", quality=76, selected_by_default=True)

    result = PipelineEngine()._select_clips(task_id, {"config": {}})

    assert result["target_count"] == 5
    assert result["selected_count"] == 4
    assert {item["clip_id"] for item in result["selected"]} == selected_ids
    with get_connection() as connection:
        enabled = {
            row["id"]
            for row in connection.execute(
                "SELECT id FROM clip_candidates WHERE task_id = ? AND enabled = 1",
                (task_id,),
            ).fetchall()
        }
    assert enabled == selected_ids


def test_feedback_is_saved_and_becomes_future_taste_context():
    task_id = f"{PREFIX}feedback"
    _insert_task(task_id)
    clip_id = _insert_candidate(task_id, 1, tier="B", quality=72, selected_by_default=False)

    result = save_clip_feedback(
        task_id,
        clip_id,
        ClipFeedbackCreate(decision="reject", reason_code="not_funny"),
    )
    context = list_recent_feedback_context("variety_comedy")

    assert result["enabled"] is False
    assert context[0]["reason_code"] == "not_funny"
    assert context[0]["title_snapshot"] == "片段 1"


def test_database_adds_quality_fields_feedback_table_and_builtin_prompt():
    with get_connection() as connection:
        task_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
        clip_columns = {row["name"] for row in connection.execute("PRAGMA table_info(clip_candidates)")}
        feedback_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'clip_feedback'"
        ).fetchone()
        preset = connection.execute(
            "SELECT name, prompt_text FROM ai_prompt_presets WHERE id = 'preset_004'"
        ).fetchone()

    assert {"selection_profile", "final_clip_target"} <= task_columns
    assert {"quality_tier", "quality_score", "audio_reaction_score", "quality_evidence_json"} <= clip_columns
    assert feedback_table is not None
    assert preset["name"] == "康熙笑点优先 V2"
    assert "不要为凑数量" in preset["prompt_text"]


def test_selection_settings_api_and_review_page_expose_v2_controls():
    task_id = f"{PREFIX}api"
    _insert_task(task_id, profile="general", final_target=5)
    _insert_candidate(task_id, 1, tier="B", quality=72, selected_by_default=False)
    headers = {"Authorization": f"Bearer {settings.local_admin_token}"} if settings.local_admin_token else {}
    with TestClient(app) as client:
        response = client.patch(
            f"/api/tasks/{task_id}/selection-settings",
            json={"selection_profile": "variety_comedy", "final_clip_target": 4},
            headers=headers,
        )
        page = client.get(f"/tasks/{task_id}/clips", headers=headers)

    assert response.status_code == 200
    assert response.json()["task"]["selection_profile"] == "variety_comedy"
    assert response.json()["task"]["final_clip_target"] == 4
    assert page.status_code == 200
    assert "不好笑" in page.text
    assert "铺垫不足" in page.text
