from __future__ import annotations

from uuid import uuid4

import pytest

from app.db.database import get_connection, init_db
from app.models.task import AIPromptPresetUpdate, ClipCandidateBatchItem, ClipFeedbackCreate
from app.services import task_service
from app.services import ai_analysis_workflow_service as ai_workflow
from app.services.ai_prompt_preset_service import (
    get_task_ai_prompt_snapshot,
    update_ai_prompt_preset,
)
from app.services.clip_feedback_service import list_recent_feedback_context, save_clip_feedback


PREFIX = "test-content-review-foundation-"


@pytest.fixture(autouse=True)
def content_review_foundation_cleanup():
    init_db()
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM clip_feedback WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM ai_analysis_runs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM ai_prompt_versions WHERE preset_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM ai_prompt_presets WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _insert_task_and_candidate(*, enabled: bool = True, reviewed: bool = False) -> tuple[str, str]:
    suffix = uuid4().hex[:8]
    task_id = f"{PREFIX}{suffix}"
    clip_id = f"{task_id}-clip"
    now = "2026-08-28T10:00:00"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, selection_profile,
                max_clip_duration, created_at, updated_at
            ) VALUES (?, ?, ?, 'variety_comedy', 10, ?, ?)
            """,
            (task_id, task_id, task_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO clip_candidates (
                id, task_id, clip_key, title, start_time, end_time,
                duration_seconds, summary, enabled, reviewed, created_at, updated_at
            ) VALUES (?, ?, 'clip_001', '测试片段', '00:00:10', '00:01:10',
                      60, '测试摘要', ?, ?, ?, ?)
            """,
            (clip_id, task_id, int(enabled), int(reviewed), now, now),
        )
        connection.commit()
    return task_id, clip_id


def _insert_analysis_run(task_id: str, run_number: int, *, is_active: bool) -> str:
    run_id = f"{task_id}-run-{run_number}"
    now = "2026-08-28T10:00:00"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ai_analysis_runs (
                id, task_id, run_number, provider, provider_label, model,
                requested_clip_count, clip_count, analysis_payload_json,
                created_at, is_active
            ) VALUES (?, ?, ?, 'test', 'Test', 'test-model', 1, 1, '{}', ?, ?)
            """,
            (run_id, task_id, run_number, now, int(is_active)),
        )
        connection.commit()
    return run_id


def _payload(clip_id: str, *, enabled: bool, reason: str | None = None) -> ClipCandidateBatchItem:
    return ClipCandidateBatchItem(
        id=clip_id,
        title="测试片段",
        start_time="00:00:10",
        end_time="00:01:10",
        enabled=enabled,
        summary="测试摘要",
        feedback_reason_code=reason,
    )


def _feedback_rows(task_id: str) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT decision, reason_code, decision_source
            FROM clip_feedback WHERE task_id = ? ORDER BY rowid
            """,
            (task_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def test_review_toggle_feedback_is_atomic_and_deduplicated():
    task_id, clip_id = _insert_task_and_candidate(enabled=True, reviewed=False)

    first = task_service.update_clip_candidates_batch(task_id, [_payload(clip_id, enabled=True)])
    repeated = task_service.update_clip_candidates_batch(task_id, [_payload(clip_id, enabled=True)])
    rejected = task_service.update_clip_candidates_batch(
        task_id,
        [_payload(clip_id, enabled=False, reason="not_funny")],
    )
    changed_reason = task_service.update_clip_candidates_batch(
        task_id,
        [_payload(clip_id, enabled=False, reason="dragging")],
    )

    assert first["feedback_count"] == 1
    assert repeated["feedback_count"] == 0
    assert rejected["feedback_count"] == 1
    assert changed_reason["feedback_count"] == 1
    assert _feedback_rows(task_id) == [
        {"decision": "keep", "reason_code": "worth_publishing", "decision_source": "review_toggle"},
        {"decision": "reject", "reason_code": "not_funny", "decision_source": "review_toggle"},
        {"decision": "reject", "reason_code": "dragging", "decision_source": "review_toggle"},
    ]


def test_review_toggle_feedback_failure_rolls_back_candidate(monkeypatch):
    task_id, clip_id = _insert_task_and_candidate(enabled=True, reviewed=False)

    def fail_feedback(*args, **kwargs):
        raise RuntimeError("feedback write failed")

    monkeypatch.setattr(task_service, "record_review_toggle_feedback_with_connection", fail_feedback)
    with pytest.raises(RuntimeError, match="feedback write failed"):
        task_service.update_clip_candidates_batch(
            task_id,
            [_payload(clip_id, enabled=False, reason="other")],
        )

    with get_connection() as connection:
        clip = connection.execute(
            "SELECT enabled, reviewed FROM clip_candidates WHERE id = ?",
            (clip_id,),
        ).fetchone()
        feedback_count = connection.execute(
            "SELECT COUNT(*) FROM clip_feedback WHERE task_id = ?",
            (task_id,),
        ).fetchone()[0]
    assert dict(clip) == {"enabled": 1, "reviewed": 0}
    assert feedback_count == 0


def test_legacy_feedback_endpoint_marks_source_and_latest_context_uses_one_per_candidate():
    task_id, clip_id = _insert_task_and_candidate(enabled=True, reviewed=False)
    save_clip_feedback(
        task_id,
        clip_id,
        ClipFeedbackCreate(decision="reject", reason_code="not_funny"),
    )
    save_clip_feedback(
        task_id,
        clip_id,
        ClipFeedbackCreate(decision="keep", reason_code="worth_publishing"),
    )

    rows = _feedback_rows(task_id)
    context = list_recent_feedback_context("variety_comedy", limit=20)
    task_context = [row for row in context if row["title_snapshot"] == "测试片段"]
    assert [row["decision_source"] for row in rows] == ["explicit_feedback", "explicit_feedback"]
    assert len(task_context) == 1
    assert task_context[0]["decision"] == "keep"


def test_explicit_feedback_uses_candidate_source_run_instead_of_active_run():
    task_id, clip_id = _insert_task_and_candidate(enabled=True, reviewed=False)
    source_run_id = _insert_analysis_run(task_id, 1, is_active=False)
    active_run_id = _insert_analysis_run(task_id, 2, is_active=True)
    with get_connection() as connection:
        connection.execute(
            "UPDATE clip_candidates SET source_analysis_run_id = ? WHERE id = ?",
            (source_run_id, clip_id),
        )
        connection.commit()

    save_clip_feedback(
        task_id,
        clip_id,
        ClipFeedbackCreate(decision="reject", reason_code="not_funny"),
    )

    with get_connection() as connection:
        feedback = connection.execute(
            "SELECT analysis_run_id FROM clip_feedback WHERE clip_candidate_id = ?",
            (clip_id,),
        ).fetchone()
    assert feedback["analysis_run_id"] == source_run_id
    assert feedback["analysis_run_id"] != active_run_id


@pytest.mark.parametrize("source_kind", ["missing", "unknown", "cross_task"])
def test_explicit_feedback_without_trustworthy_source_run_stays_unattributed(source_kind: str):
    task_id, clip_id = _insert_task_and_candidate(enabled=True, reviewed=False)
    active_run_id = _insert_analysis_run(task_id, 2, is_active=True)
    source_run_id = None
    if source_kind == "unknown":
        source_run_id = f"{task_id}-missing-run"
    elif source_kind == "cross_task":
        other_task_id, _ = _insert_task_and_candidate(enabled=True, reviewed=False)
        source_run_id = _insert_analysis_run(other_task_id, 1, is_active=True)
    with get_connection() as connection:
        connection.execute(
            "UPDATE clip_candidates SET source_analysis_run_id = ? WHERE id = ?",
            (source_run_id, clip_id),
        )
        connection.commit()

    save_clip_feedback(
        task_id,
        clip_id,
        ClipFeedbackCreate(decision="keep", reason_code="worth_publishing"),
    )

    with get_connection() as connection:
        feedback = connection.execute(
            "SELECT analysis_run_id FROM clip_feedback WHERE clip_candidate_id = ?",
            (clip_id,),
        ).fetchone()
    assert feedback["analysis_run_id"] is None
    assert feedback["analysis_run_id"] != active_run_id


def test_prompt_version_changes_only_when_prompt_content_changes():
    suffix = uuid4().hex[:8]
    preset_id = f"{PREFIX}preset-{suffix}"
    task_id = f"{PREFIX}task-{suffix}"
    now = "2026-08-28T10:00:00"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ai_prompt_presets (
                id, slot, name, prompt_text, is_default, created_at, updated_at
            ) VALUES (?, 991, '测试 Prompt', '第一版内容', 0, ?, ?)
            """,
            (preset_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, ai_prompt_preset_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, task_id, task_id, preset_id, now, now),
        )
        connection.commit()

    first = get_task_ai_prompt_snapshot(task_id)
    second = get_task_ai_prompt_snapshot(task_id)
    update_ai_prompt_preset(
        preset_id,
        AIPromptPresetUpdate(name="仅改名称", prompt_text="第一版内容"),
    )
    same_content = get_task_ai_prompt_snapshot(task_id)
    update_ai_prompt_preset(
        preset_id,
        AIPromptPresetUpdate(name="第二版", prompt_text="第二版内容"),
    )
    changed = get_task_ai_prompt_snapshot(task_id)

    assert first["prompt_version_id"] == second["prompt_version_id"] == same_content["prompt_version_id"]
    assert first["prompt_version_number"] == 1
    assert changed["prompt_version_number"] == 2
    assert changed["prompt_version_id"] != first["prompt_version_id"]
    assert changed["prompt_sha256"] != first["prompt_sha256"]


def test_new_candidate_and_analysis_run_share_prompt_provenance():
    suffix = uuid4().hex[:8]
    preset_id = f"{PREFIX}preset-{suffix}"
    task_id = f"{PREFIX}task-{suffix}"
    run_id = f"run-{suffix}"
    now = "2026-08-28T10:00:00"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ai_prompt_presets (
                id, slot, name, prompt_text, is_default, created_at, updated_at
            ) VALUES (?, 992, '归因测试', '归因 Prompt', 0, ?, ?)
            """,
            (preset_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, ai_prompt_preset_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, task_id, task_id, preset_id, now, now),
        )
        connection.commit()
    snapshot = get_task_ai_prompt_snapshot(task_id)
    clip = {
        "clip_id": "clip_001",
        "title": "归因片段",
        "start_time": "00:00:10",
        "end_time": "00:01:10",
        "duration_seconds": 60,
        "summary": "归因摘要",
        "highlight_reason": "归因理由",
        "spread_value": "归因价值",
        "suggested_editing": "直接切片",
        "confidence_score": 0.9,
    }
    payload = {"analysis_summary": "归因分析", "clips": [clip], "analysis_meta": {}}
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        ai_workflow._insert_clip_candidates_with_connection(
            connection,
            task_id,
            [clip],
            now,
            source_analysis_run_id=run_id,
        )
        ai_workflow._insert_ai_analysis_run_with_connection(
            connection,
            task_id=task_id,
            analysis_payload=payload,
            provider="codex",
            provider_label="Codex CLI",
            model="test-model",
            fallback_notice="",
            prompt_preset=snapshot,
            requested_clip_count=1,
            now=now,
            run_id=run_id,
        )
        connection.commit()

    with get_connection() as connection:
        candidate = connection.execute(
            "SELECT source_analysis_run_id FROM clip_candidates WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        run = connection.execute(
            """
            SELECT prompt_version_id, prompt_text_sha256
            FROM ai_analysis_runs WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    assert candidate["source_analysis_run_id"] == run_id
    assert run["prompt_version_id"] == snapshot["prompt_version_id"]
    assert run["prompt_text_sha256"] == snapshot["prompt_sha256"]
