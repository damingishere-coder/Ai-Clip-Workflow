import json
from uuid import uuid4

import pytest

from app.db.database import get_connection
from app.models.task import ClipCandidateBatchItem
from app.services import task_service, job_service
from app.services.ai import content_decision_analyzer as analyzer, unit_checkpoint
from app.services.ai_analysis_workflow_service import (
    _insert_clip_candidates_with_connection,
)
from tests.test_content_decisions import item, rows


@pytest.fixture
def task_id():
    task = "test-decision-" + uuid4().hex[:8]
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO tasks(id,task_name,selection_profile,created_at,updated_at) VALUES (?,?,'variety_comedy','now','now')",
            (task, task),
        )
        connection.commit()
    yield task
    with get_connection() as connection:
        for table in (
            "clip_feedback",
            "workflow_jobs",
            "clip_candidates",
            "ai_analysis_runs",
            "tasks",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE {'id' if table == 'tasks' else 'task_id'}=?",
                (task,),
            )
        connection.commit()


def test_review_persists_and_requires_explicit_confirmation(task_id):
    clip = analyzer._payload(item(decision="review"), 1)
    clip["selected_by_default"] = (
        True  # stale score-based caller cannot enable a review.
    )
    with get_connection() as connection:
        _insert_clip_candidates_with_connection(connection, task_id, [clip], "now")
        connection.commit()
    saved = task_service.list_clip_candidates(task_id)[0]
    assert saved["decision"] == "review" and saved["enabled"] is False
    assert saved["review_issues"] == ["缺少最后两秒证据"]
    assert task_service.list_enabled_clip_candidates(task_id) == []
    payload = ClipCandidateBatchItem(
        id=saved["id"],
        title=saved["title"],
        start_time=saved["start_time"],
        end_time=saved["end_time"],
        enabled=True,
    )
    with pytest.raises(ValueError, match="明确确认"):
        task_service.update_clip_candidates_batch(task_id, [payload])
    assert task_service.list_enabled_clip_candidates(task_id) == []
    payload.confirm_ai_decision = True
    task_service.update_clip_candidates_batch(task_id, [payload])
    assert len(task_service.list_enabled_clip_candidates(task_id)) == 1
    approved = task_service.list_clip_candidates(task_id)[0]
    assert (
        approved["decision"] == "review"
    )  # history is not rewritten as an AI publish decision.
    assert approved["decision_confirmed"] == 1
    assert approved["decision_reason"] == saved["decision_reason"]


def test_invalid_final_unit_not_successfully_cached_and_valid_units_reused(task_id):
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AI_ANALYSIS)
    claimed = job_service.claim_job(job["id"], "decision-test")
    calls = []

    def validator(p):
        analyzer.validate_decisions(p, {"w001_m001": rows()}, final=True)

    def operation():
        calls.append(1)
        return {"clips": [item()]}

    kwargs = dict(
        task_id=task_id,
        namespace="content_global_judge",
        input_fingerprint="same-input",
        unit_id="w001_m001",
        request_fingerprint="same-request",
        operation=operation,
        validate_payload=validator,
    )
    with job_service.job_lease_context(
        job["id"], "decision-test", claimed["lease_token"]
    ):
        assert (
            unit_checkpoint.execute_checkpointed_ai_unit(**kwargs).status == "completed"
        )
        assert (
            unit_checkpoint.execute_checkpointed_ai_unit(**kwargs).status == "completed"
        )
        assert len(calls) == 1
        bad = item()
        bad["hook_score"] = "seventy"
        kwargs.update(unit_id="bad", operation=lambda: {"clips": [bad]})
        assert (
            unit_checkpoint.execute_checkpointed_ai_unit(**kwargs).status == "uncertain"
        )
    with get_connection() as connection:
        cp = json.loads(
            connection.execute(
                "SELECT checkpoint_json FROM workflow_jobs WHERE id=?", (job["id"],)
            ).fetchone()[0]
        )
    units = cp["_ai_analysis_units_v1"]["namespaces"]["content_global_judge"]["units"]
    assert units["w001_m001"]["status"] == "completed"
    assert units["bad"]["status"] == "uncertain"
    assert not units["bad"].get("result_json")


@pytest.mark.parametrize(
    "scenario", ["recovered", "invalid", "tampered", "changed_request"]
)
def test_rejected_response_revalidated_without_repeat_billing(task_id, scenario):
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AI_ANALYSIS)
    claimed = job_service.claim_job(job["id"], "receipt-test")
    payload = {"clips": [item()]}
    if scenario == "invalid":
        payload["clips"][0]["evidence"][0]["quote"] = "原文根本没有这句话"
    calls = []

    def operation():
        calls.append(1)
        return payload

    def too_strict(_):
        raise ValueError("旧本地校验不接受跨字幕行证据")

    kwargs = dict(
        task_id=task_id,
        namespace="judge",
        input_fingerprint="input",
        unit_id="one",
        request_fingerprint="request",
        operation=operation,
        validate_payload=too_strict,
    )
    with job_service.job_lease_context(
        job["id"], "receipt-test", claimed["lease_token"]
    ):
        assert (
            unit_checkpoint.execute_checkpointed_ai_unit(**kwargs).status == "uncertain"
        )
        if scenario == "tampered":
            with get_connection() as connection:
                cp = json.loads(
                    connection.execute(
                        "SELECT checkpoint_json FROM workflow_jobs WHERE id=?",
                        (job["id"],),
                    ).fetchone()[0]
                )
                cp["_ai_analysis_units_v1"]["namespaces"]["judge"]["units"]["one"][
                    "rejected_response_checksum"
                ] = "wrong"
                connection.execute(
                    "UPDATE workflow_jobs SET checkpoint_json=? WHERE id=?",
                    (json.dumps(cp), job["id"]),
                )
                connection.commit()
        kwargs["validate_payload"] = lambda p: analyzer.validate_decisions(
            p, {"w001_m001": rows()}, final=True
        )
        if scenario == "changed_request":
            kwargs["request_fingerprint"] = "different-request"
        result = unit_checkpoint.execute_checkpointed_ai_unit(**kwargs)
    assert len(calls) == 1
    assert result.reused
    assert result.status == ("completed" if scenario == "recovered" else "uncertain")
    with get_connection() as connection:
        cp = json.loads(
            connection.execute(
                "SELECT checkpoint_json FROM workflow_jobs WHERE id=?", (job["id"],)
            ).fetchone()[0]
        )
    state = cp["_ai_analysis_units_v1"]["namespaces"]["judge"]
    if scenario == "recovered":
        assert state["units"]["one"]["recovered_by_validation"]
        assert state["validation_recoveries"][0]["status"] == "uncertain"
    else:
        assert not state["units"]["one"].get("result_json")


@pytest.mark.parametrize("review_count", [0, 2])
def test_zero_usable_pipeline_never_reaches_cut_or_publish(
    monkeypatch, task_id, review_count
):
    from app.services import pipeline_engine
    from app.models.task import TaskStatus

    engine = pipeline_engine.PipelineEngine()
    states = []
    monkeypatch.setattr(
        engine, "_get_task", lambda task: {"id": task, "auto_mode": True}
    )
    monkeypatch.setattr(engine, "_load_auto_config", lambda task: {})
    monkeypatch.setattr(
        task_service, "update_task_status", lambda task, status: states.append(status)
    )
    monkeypatch.setattr(
        engine,
        "_select_clips",
        lambda task, context: {
            "content_terminal": True,
            "review_count": review_count,
            "message": "已完成，无可用片段",
        },
    )

    def must_not_run(*args):
        raise AssertionError("零可用结果不能切片或发布")

    monkeypatch.setattr(engine, "_cut_video", must_not_run)
    monkeypatch.setattr(engine, "_create_publish_jobs", must_not_run)
    result = engine.run(task_id, start_step=TaskStatus.CLIP_SELECTING)
    assert states[-1] == (
        TaskStatus.pending_review if review_count else TaskStatus.completed
    )
    assert result["status"] == states[-1].value


def test_completed_zero_result_commits_and_resumes_without_model_call(task_id):
    from app.services.ai_analysis_workflow_service import (
        _commit_ai_analysis_result,
        _resume_committed_ai_analysis,
    )
    from app.services.ai_prompt_preset_service import get_task_ai_prompt_snapshot

    with get_connection() as connection:
        connection.execute(
            "UPDATE tasks SET status='ai_analyzing' WHERE id=?", (task_id,)
        )
        connection.commit()
    prompt = get_task_ai_prompt_snapshot(task_id)
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AI_ANALYSIS)
    claimed = job_service.claim_job(job["id"], "zero-test")
    payload = {
        "task_id": task_id,
        "clips": [],
        "analysis_summary": "分析完成，暂无可用片段",
        "analysis_meta": {
            "schema_version": 2,
            "selection_profile": "variety_comedy",
            "selection_count_mode": "content",
            "decision_contract": analyzer.CONTRACT_VERSION,
            "analysis_incomplete": False,
            "quality_degraded": False,
            "expected_units": 1,
            "completed_units": 1,
            "failed_units": 0,
            "invalid_item_count": 0,
            "failed_stages": [],
            "coverage_ratio": 1,
            "coverage_percent": 100,
            "workflow_job_id": job["id"],
        },
    }
    with job_service.job_lease_context(job["id"], "zero-test", claimed["lease_token"]):
        run = _commit_ai_analysis_result(
            task_id=task_id,
            analysis_payload=payload,
            provider="codex",
            provider_label="Codex CLI",
            model="gpt-6-astra",
            fallback_notice="",
            prompt_preset=prompt,
            requested_clip_count=12,
        )
        assert run["clip_count"] == 0
        assert task_service.get_task(task_id)["status"] == "completed"
        resumed = _resume_committed_ai_analysis(task_id)
        assert resumed[0]["id"] == run["id"]
        assert resumed[1]["clips"] == []
