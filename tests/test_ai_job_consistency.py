from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.task import AIClipAnalysisResult, AIClipItem, TaskCreate, TaskStatus
from app.services import job_service, job_worker
from app.services import ai_analysis_workflow_service as workflow
from app.services.ai import unit_checkpoint
from app.services.storage_service import get_artifact_paths
from app.services.task_lifecycle_service import create_task_record, update_task_status
from app.services.task_service import get_task, list_clip_candidates
from app.db.database import get_connection


PREFIX = "test-ai-consistency-"


@pytest.fixture(autouse=True)
def cleanup_rows():
    yield
    with get_connection() as connection:
        for table in ("publish_jobs", "workflow_jobs", "output_clip", "clip_candidates", "ai_analysis_runs"):
            connection.execute(f"DELETE FROM {table} WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _create_task(suffix: str, *, status: TaskStatus = TaskStatus.pending_ai) -> str:
    task_id = f"{PREFIX}{suffix}"
    create_task_record(TaskCreate(task_name=suffix, selection_profile="general"), task_id=task_id)
    update_task_status(task_id, status)
    transcript_path = get_artifact_paths(task_id)["transcript_path"]
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text("00:00:00 - 00:02:00 测试转写正文", encoding="utf-8")
    return task_id


def _analysis(task_id: str, *, incomplete: bool = False) -> AIClipAnalysisResult:
    return AIClipAnalysisResult(
        task_id=task_id,
        analysis_summary="隔离 AI 结果",
        clips=[
            AIClipItem(
                clip_id="clip_001",
                title="隔离候选",
                start_time="00:00:10",
                end_time="00:01:10",
                duration_seconds=60,
                cover_time_seconds=30,
                summary="摘要",
                highlight_reason="亮点",
                spread_value="高",
                suggested_editing="保留上下文",
                confidence_score=0.9,
            )
        ],
        analysis_meta={
            "schema_version": 2,
            "coverage_basis": "chunk_count",
            "expected_units": 2,
            "completed_units": 1 if incomplete else 2,
            "failed_units": 1 if incomplete else 0,
            "coverage_ratio": 0.5 if incomplete else 1.0,
            "coverage_percent": 50 if incomplete else 100,
            "analysis_incomplete": incomplete,
            "quality_degraded": False,
            "failed_stages": [{"stage": "chunk", "unit_id": "chunk_002"}] if incomplete else [],
        },
    )


def _claim_ai_job(task_id: str, owner: str = "ai-test-owner") -> tuple[dict, str]:
    job, _created = workflow.queue_task_ai_analysis(task_id, provider="remote")
    claimed = job_service.claim_job(job["id"], owner)
    assert claimed
    return claimed, owner


def test_manual_api_creates_and_reuses_persistent_ai_job(monkeypatch):
    task_id = _create_task("queue")
    monkeypatch.setattr(workflow, "_analyze_with_provider", lambda *_args, **_kwargs: pytest.fail("API 不应直接调用 Provider"))
    client = TestClient(app)

    first = client.post(f"/api/tasks/{task_id}/process/ai?provider=remote")
    second = client.post(f"/api/tasks/{task_id}/process/ai?provider=remote")

    assert first.status_code == 200
    assert first.json()["status"] == job_service.JOB_STATUS_QUEUED
    assert second.json()["job_id"] == first.json()["job_id"]
    with get_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS total FROM workflow_jobs WHERE task_id = ? AND job_type = ?",
            (task_id, job_service.JOB_TYPE_AI_ANALYSIS),
        ).fetchone()["total"]
    assert count == 1


def test_failed_ai_job_reuses_original_checkpoint_ledger():
    task_id = _create_task("retry-ledger")
    claimed, owner = _claim_ai_job(task_id, owner="retry-owner")
    checkpoint = {
        "_ai_analysis_units_v1": {
            "namespaces": {
                "general_chunks": {
                    "input_fingerprint": "stable",
                    "units": {"chunk_001": {"status": "uncertain", "error": "计费结果不确定"}},
                }
            }
        }
    }
    job_service.update_job_checkpoint(
        claimed["id"],
        checkpoint,
        lease_owner=owner,
        lease_token=claimed["lease_token"],
    )
    job_service.mark_job_failed(
        claimed["id"],
        "Provider 结果不确定",
        lease_owner=owner,
        lease_token=claimed["lease_token"],
    )

    retried, created = workflow.queue_task_ai_analysis(task_id, provider="remote")

    assert created is True
    assert retried["id"] == claimed["id"]
    assert retried["status"] == job_service.JOB_STATUS_QUEUED
    assert retried["checkpoint_json"] == checkpoint


def test_failed_ai_job_cannot_switch_provider_and_drop_ledger():
    task_id = _create_task("retry-provider-change")
    claimed, owner = _claim_ai_job(task_id, owner="provider-owner")
    job_service.mark_job_failed(
        claimed["id"],
        "Provider 结果不确定",
        lease_owner=owner,
        lease_token=claimed["lease_token"],
    )

    with pytest.raises(workflow.AIAnalysisConflictError, match="不能在同一账本中切换"):
        workflow.queue_task_ai_analysis(task_id, provider="local")

    assert job_service.get_job(claimed["id"])["status"] == job_service.JOB_STATUS_FAILED


def test_ai_job_cancel_and_parent_failure_settle_task_state():
    cancel_task_id = _create_task("cancel-settle")
    cancel_job, cancel_owner = _claim_ai_job(cancel_task_id, owner="cancel-owner")
    with job_service.job_lease_context(cancel_job["id"], cancel_owner, cancel_job["lease_token"]):
        workflow._begin_ai_analysis(
            cancel_task_id,
            get_task(cancel_task_id, include_video_probe=False),
        )
    requested = job_service.request_job_cancel(cancel_job["id"])
    assert requested["cancel_requested"] == 1
    assert get_task(cancel_task_id, include_video_probe=False)["status"] == TaskStatus.pending_ai.value
    assert job_service.release_job_lease(
        cancel_job["id"],
        cancel_owner,
        cancel_job["lease_token"],
    )
    assert job_service.get_job(cancel_job["id"])["status"] == job_service.JOB_STATUS_CANCELLED

    failed_task_id = _create_task("parent-failure")
    failed_job, failed_owner = _claim_ai_job(failed_task_id, owner="failed-owner")
    with job_service.job_lease_context(failed_job["id"], failed_owner, failed_job["lease_token"]):
        workflow._begin_ai_analysis(
            failed_task_id,
            get_task(failed_task_id, include_video_probe=False),
        )
    job_service.mark_job_failed(
        failed_job["id"],
        "子进程异常退出",
        lease_owner=failed_owner,
        lease_token=failed_job["lease_token"],
    )
    failed_task = get_task(failed_task_id, include_video_probe=False)
    assert failed_task["status"] == TaskStatus.failed.value
    assert failed_task["last_error"] == "子进程异常退出"


def test_confirmed_ai_unit_is_reused_after_lease_takeover():
    task_id = _create_task("unit-reuse")
    claimed, owner = _claim_ai_job(task_id, owner="unit-owner-a")
    calls = 0

    def operation() -> dict:
        nonlocal calls
        calls += 1
        return {"clips": [{"clip_id": "confirmed"}]}

    with job_service.job_lease_context(claimed["id"], owner, claimed["lease_token"]):
        first = unit_checkpoint.execute_checkpointed_ai_unit(
            task_id=task_id,
            namespace="test_units",
            input_fingerprint="stable-input",
            unit_id="unit-001",
            operation=operation,
        )
    with get_connection() as connection:
        connection.execute(
            "UPDATE workflow_jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (claimed["id"],),
        )
        connection.commit()
    reclaimed = job_service.claim_job(claimed["id"], "unit-owner-b")
    assert reclaimed
    with job_service.job_lease_context(reclaimed["id"], "unit-owner-b", reclaimed["lease_token"]):
        second = unit_checkpoint.execute_checkpointed_ai_unit(
            task_id=task_id,
            namespace="test_units",
            input_fingerprint="stable-input",
            unit_id="unit-001",
            operation=operation,
        )

    assert first.status == "completed"
    assert second.status == "completed"
    assert second.reused is True
    assert calls == 1


def test_started_ai_unit_without_local_result_is_not_rebilled_after_takeover(monkeypatch):
    task_id = _create_task("unit-uncertain")
    claimed, owner = _claim_ai_job(task_id, owner="uncertain-owner-a")
    original_finish = unit_checkpoint._finish_unit_success
    monkeypatch.setattr(
        unit_checkpoint,
        "_finish_unit_success",
        lambda **_kwargs: (_ for _ in ()).throw(job_service.JobLeaseLostError("crashed before save")),
    )
    with job_service.job_lease_context(claimed["id"], owner, claimed["lease_token"]):
        with pytest.raises(job_service.JobLeaseLostError):
            unit_checkpoint.execute_checkpointed_ai_unit(
                task_id=task_id,
                namespace="test_uncertain",
                input_fingerprint="stable-input",
                unit_id="unit-001",
                operation=lambda: {"clips": [{"clip_id": "provider-returned"}]},
            )
    monkeypatch.setattr(unit_checkpoint, "_finish_unit_success", original_finish)
    with get_connection() as connection:
        connection.execute(
            "UPDATE workflow_jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (claimed["id"],),
        )
        connection.commit()
    reclaimed = job_service.claim_job(claimed["id"], "uncertain-owner-b")
    provider_called = False

    def forbidden_operation() -> dict:
        nonlocal provider_called
        provider_called = True
        return {}

    with job_service.job_lease_context(reclaimed["id"], "uncertain-owner-b", reclaimed["lease_token"]):
        result = unit_checkpoint.execute_checkpointed_ai_unit(
            task_id=task_id,
            namespace="test_uncertain",
            input_fingerprint="stable-input",
            unit_id="unit-001",
            operation=forbidden_operation,
        )

    assert result.status == "uncertain"
    assert result.reused is True
    assert provider_called is False


def test_unknown_ai_unit_checkpoint_state_is_not_rebilled():
    task_id = _create_task("unit-corrupt")
    claimed, owner = _claim_ai_job(task_id, owner="corrupt-owner")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE workflow_jobs SET checkpoint_json = ? WHERE id = ?
            """,
            (
                json.dumps(
                    {
                        "_ai_analysis_units_v1": {
                            "namespaces": {
                                "test_corrupt": {
                                    "input_fingerprint": "stable-input",
                                    "units": {"unit-001": {"status": "unexpected"}},
                                }
                            }
                        }
                    }
                ),
                claimed["id"],
            ),
        )
        connection.commit()
    provider_called = False

    def forbidden_operation() -> dict:
        nonlocal provider_called
        provider_called = True
        return {}

    with job_service.job_lease_context(claimed["id"], owner, claimed["lease_token"]):
        result = unit_checkpoint.execute_checkpointed_ai_unit(
            task_id=task_id,
            namespace="test_corrupt",
            input_fingerprint="stable-input",
            unit_id="unit-001",
            operation=forbidden_operation,
        )

    assert result.status == "uncertain"
    assert result.reused is True
    assert provider_called is False


def test_changed_ai_unit_fingerprint_preserves_old_ledger():
    task_id = _create_task("unit-fingerprint-change")
    claimed, owner = _claim_ai_job(task_id, owner="fingerprint-owner")
    with get_connection() as connection:
        checkpoint = {
            "_ai_analysis_units_v1": {
                "namespaces": {
                    "test_changed": {
                        "input_fingerprint": "old-input",
                        "units": {"unit-001": {"status": "uncertain", "error": "旧请求不确定"}},
                    }
                }
            }
        }
        connection.execute(
            "UPDATE workflow_jobs SET checkpoint_json = ? WHERE id = ?",
            (json.dumps(checkpoint), claimed["id"]),
        )
        connection.commit()
    provider_called = False

    def forbidden_operation() -> dict:
        nonlocal provider_called
        provider_called = True
        return {}

    with job_service.job_lease_context(claimed["id"], owner, claimed["lease_token"]):
        with pytest.raises(ValueError, match="旧恢复证据不会被覆盖"):
            unit_checkpoint.execute_checkpointed_ai_unit(
                task_id=task_id,
                namespace="test_changed",
                input_fingerprint="new-input",
                unit_id="unit-001",
                operation=forbidden_operation,
            )

    assert provider_called is False
    assert job_service.get_job(claimed["id"])["checkpoint_json"] == checkpoint


def test_ai_worker_commits_candidates_run_task_and_job(monkeypatch):
    task_id = _create_task("worker")
    monkeypatch.setattr(workflow, "_analyze_with_provider", lambda *_args, **_kwargs: _analysis(task_id))
    claimed, owner = _claim_ai_job(task_id)

    completed = job_worker.execute_job(
        claimed["id"],
        lease_owner=owner,
        lease_token=claimed["lease_token"],
        already_claimed=True,
    )

    assert completed["status"] == job_service.JOB_STATUS_COMPLETED
    assert completed["result_json"]["clip_count"] == 1
    assert get_task(task_id, include_video_probe=False)["status"] == TaskStatus.pending_review.value
    assert [clip["title"] for clip in list_clip_candidates(task_id)] == ["隔离候选"]
    latest = workflow.get_latest_ai_analysis_run(task_id)
    assert latest["analysis_meta"]["workflow_job_id"] == claimed["id"]


def test_ai_result_transaction_rolls_back_on_run_insert_failure(monkeypatch):
    task_id = _create_task("rollback", status=TaskStatus.pending_review)
    workflow._replace_clip_candidates(task_id, [workflow.result_to_jsonable(_analysis(task_id))["clips"][0]])
    old_run = workflow._insert_ai_analysis_run(
        task_id=task_id,
        analysis_payload=workflow.result_to_jsonable(_analysis(task_id)),
        provider="remote",
        provider_label="远程 AI",
        model="old-model",
        fallback_notice="",
        prompt_preset={},
        requested_clip_count=1,
    )
    monkeypatch.setattr(workflow, "_analyze_with_provider", lambda *_args, **_kwargs: _analysis(task_id))
    monkeypatch.setattr(
        workflow,
        "_insert_ai_analysis_run_with_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("run insert failed")),
    )
    claimed, owner = _claim_ai_job(task_id)

    with job_service.job_lease_context(claimed["id"], owner, claimed["lease_token"]):
        with pytest.raises(ValueError, match="run insert failed"):
            workflow.process_task_ai_analysis(task_id, provider="remote")

    assert [clip["title"] for clip in list_clip_candidates(task_id)] == ["隔离候选"]
    runs = workflow.list_ai_analysis_runs(task_id)
    assert len(runs) == 1
    assert runs[0]["id"] == old_run["id"]


def test_stale_ai_worker_cannot_commit_after_lease_takeover(monkeypatch):
    task_id = _create_task("fencing")
    claimed, owner = _claim_ai_job(task_id, owner="old-owner")
    replacement: dict = {}

    def steal_lease(*_args, **_kwargs):
        with get_connection() as connection:
            connection.execute(
                "UPDATE workflow_jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                (claimed["id"],),
            )
            connection.commit()
        replacement.update(job_service.claim_job(claimed["id"], "new-owner") or {})
        return _analysis(task_id)

    monkeypatch.setattr(workflow, "_analyze_with_provider", steal_lease)

    with job_service.job_lease_context(claimed["id"], owner, claimed["lease_token"]):
        with pytest.raises(job_service.JobLeaseLostError):
            workflow.process_task_ai_analysis(task_id, provider="remote")

    assert replacement["lease_owner"] == "new-owner"
    assert list_clip_candidates(task_id) == []
    assert workflow.list_ai_analysis_runs(task_id) == []
    assert get_task(task_id, include_video_probe=False)["status"] == TaskStatus.ai_analyzing.value


def test_taken_over_job_reuses_committed_run_and_rebuilds_file_without_provider(monkeypatch):
    task_id = _create_task("resume")
    calls = 0

    def analyze(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _analysis(task_id)

    monkeypatch.setattr(workflow, "_analyze_with_provider", analyze)
    claimed, owner = _claim_ai_job(task_id, owner="first-owner")
    with job_service.job_lease_context(claimed["id"], owner, claimed["lease_token"]):
        first = workflow.process_task_ai_analysis(task_id, provider="remote")
    assert first["analysis_run_id"]
    analysis_path: Path = get_artifact_paths(task_id)["analysis_path"]
    analysis_path.unlink(missing_ok=True)

    with get_connection() as connection:
        connection.execute(
            "UPDATE workflow_jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (claimed["id"],),
        )
        connection.commit()
    takeover = job_service.claim_job(claimed["id"], "second-owner")
    assert takeover
    with job_service.job_lease_context(takeover["id"], "second-owner", takeover["lease_token"]):
        resumed = workflow.process_task_ai_analysis(task_id, provider="remote")

    assert resumed["analysis_run_id"] == first["analysis_run_id"]
    assert calls == 1
    assert analysis_path.exists()


def test_corrupt_active_analysis_meta_fails_closed():
    task_id = _create_task("corrupt-meta", status=TaskStatus.pending_review)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ai_analysis_runs (
                id, task_id, run_number, provider, provider_label, model,
                requested_clip_count, clip_count, analysis_summary,
                analysis_payload_json, is_active, created_at
            ) VALUES (?, ?, 1, 'remote', '远程 AI', 'test-model', 1, 1, '', ?, 1, CURRENT_TIMESTAMP)
            """,
            ("corrupt-meta-run", task_id, json.dumps({"clips": []})),
        )
        connection.commit()

    meta = workflow.get_task_ai_analysis_meta(task_id)

    assert meta["analysis_incomplete"] is True
    assert meta["quality_degraded"] is True
    assert meta["integrity_error"] == "active_run_meta_missing"
