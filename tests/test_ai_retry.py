from __future__ import annotations

import hashlib
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_connection
from app.main import app
from app.models.settings import AIConfigUpdate
from app.models.task import TaskCreate, TaskStatus
from app.services import job_service, task_service
from app.services.pipeline_checkpoint_service import AUTO_PIPELINE_CHECKPOINT_KIND
from app.services.pipeline_engine import PipelineEngine
from app.services.storage_service import get_artifact_paths
from app.services.task_lifecycle_service import create_task_record


client = TestClient(app)


@pytest.fixture(autouse=True)
def cleanup_ai_retry_data():
    yield
    pattern = "test-ai-retry-%"
    with get_connection() as connection:
        task_ids = [
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM tasks WHERE id LIKE ?",
                (pattern,),
            ).fetchall()
        ]
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM ai_analysis_windows WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM ai_analysis_runs WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM workflow_jobs WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (pattern,))
        connection.commit()
    for task_id in task_ids:
        shutil.rmtree(get_artifact_paths(task_id)["task_dir"], ignore_errors=True)


def _create_task(task_id: str) -> None:
    create_task_record(
        TaskCreate(
            task_name=task_id,
            source_type="upload",
            platform="general",
            selection_profile="variety_comedy",
            auto_mode=True,
        ),
        task_id=task_id,
        task_dir_name=task_id,
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _failed_job(task_id: str, transcript: bytes = b"original transcript") -> dict:
    paths = get_artifact_paths(task_id)
    paths["transcript_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["transcript_path"].write_bytes(transcript)
    job = job_service.create_job(
        task_id,
        job_service.JOB_TYPE_AUTO_PIPELINE,
        payload={"retry": False, "start_step": None},
    )
    checkpoint = {
        "kind": AUTO_PIPELINE_CHECKPOINT_KIND,
        "task_id": task_id,
        "run_key": "test-run-key",
        "start_step": TaskStatus.PREPARING_SOURCE.value,
        "current_step": TaskStatus.CLIP_SELECTING.value,
        "completed_steps": [
            TaskStatus.PREPARING_SOURCE.value,
            TaskStatus.TRANSCRIBING.value,
            TaskStatus.AI_ANALYZING.value,
        ],
        "steps": {
            TaskStatus.PREPARING_SOURCE.value: {
                "state": "succeeded",
                "attempts": 1,
                "outputs": {},
            },
            TaskStatus.TRANSCRIBING.value: {
                "state": "succeeded",
                "attempts": 1,
                "outputs": {"transcript": {"sha256": _sha256(transcript)}},
            },
            TaskStatus.AI_ANALYZING.value: {
                "state": "succeeded",
                "attempts": 1,
                "outputs": {},
            },
            TaskStatus.CLIP_SELECTING.value: {
                "state": "failed",
                "attempts": 1,
                "outputs": {},
                "error": "analysis incomplete",
            },
        },
        "last_error": "analysis incomplete",
        "_ai_analysis_units_v1": {
            "namespaces": {
                "variety_recall": {
                    "input_fingerprint": "input-v1",
                    "units": {
                        "window_001": {
                            "status": "completed",
                            "result_json": "{}",
                            "result_checksum": _sha256(b"{}"),
                        },
                        "window_004": {
                            "status": "uncertain",
                            "error": "Codex CLI timeout; billing uncertain",
                        },
                    },
                }
            }
        },
    }
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE workflow_jobs
            SET status = ?, checkpoint_json = ?, error_message = 'analysis incomplete'
            WHERE id = ?
            """,
            (job_service.JOB_STATUS_FAILED, json.dumps(checkpoint), job["id"]),
        )
        connection.execute(
            "UPDATE tasks SET status = ? WHERE id = ?",
            (TaskStatus.FAILED_CLIP_SELECTING.value, task_id),
        )
        connection.commit()
    return job_service.get_job(job["id"])


def test_retry_requires_structured_confirmation() -> None:
    assert AIConfigUpdate().ai_codex_timeout_seconds == 600
    task_id = "test-ai-retry-confirm"
    _create_task(task_id)
    old_job = _failed_job(task_id)

    response = client.post(f"/api/tasks/{task_id}/process/auto-retry")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "ai_retry_confirmation_required"
    assert detail["uncertain_unit_count"] == 1
    assert detail["retry_mode"] == "resume_uncertain"
    assert detail["previous_job_id"] == old_job["id"]


def test_confirmed_same_input_retries_only_uncertain_unit() -> None:
    task_id = "test-ai-retry-resume"
    _create_task(task_id)
    old_job = _failed_job(task_id)

    response = client.post(
        f"/api/tasks/{task_id}/process/auto-retry?confirm_uncertain_ai=true"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == old_job["id"]
    assert payload["retry_mode"] == "resume_uncertain"
    stored = job_service.get_job(old_job["id"])
    units = stored["checkpoint_json"]["_ai_analysis_units_v1"]["namespaces"]["variety_recall"]["units"]
    assert units["window_001"]["status"] == "completed"
    assert units["window_004"]["status"] == "retryable_failed"
    assert units["window_004"]["retry_authorized_reason"] == "explicit_user_confirmation"
    assert stored["checkpoint_json"]["completed_steps"] == [
        TaskStatus.PREPARING_SOURCE.value,
        TaskStatus.TRANSCRIBING.value,
    ]
    assert stored["checkpoint_json"]["current_step"] == TaskStatus.AI_ANALYZING.value
    assert task_service.get_task(task_id, include_video_probe=False)["status"] == TaskStatus.FAILED_AI_ANALYZING.value


def test_confirmed_changed_input_creates_fresh_ai_job_and_preserves_old_evidence() -> None:
    task_id = "test-ai-retry-fresh"
    _create_task(task_id)
    old_job = _failed_job(task_id)
    old_checkpoint = old_job["checkpoint_json"]
    get_artifact_paths(task_id)["transcript_path"].write_bytes(b"simplified transcript")

    response = client.post(
        f"/api/tasks/{task_id}/process/auto-retry?confirm_uncertain_ai=true"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["retry_mode"] == "fresh_ai"
    assert payload["job_id"] != old_job["id"]
    new_job = job_service.get_job(payload["job_id"])
    assert new_job["payload_json"]["start_step"] == TaskStatus.AI_ANALYZING.value
    assert new_job["checkpoint_json"] == {}
    preserved = job_service.get_job(old_job["id"])
    assert preserved["status"] == job_service.JOB_STATUS_FAILED
    assert preserved["checkpoint_json"] == old_checkpoint
    assert task_service.get_task(task_id, include_video_probe=False)["status"] == TaskStatus.FAILED_AI_ANALYZING.value


def test_retry_refuses_existing_downstream_records() -> None:
    task_id = "test-ai-retry-downstream"
    _create_task(task_id)
    _failed_job(task_id)
    now = task_service._now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, output_file_path, output_file_name, status,
                created_at, updated_at
            ) VALUES ('output-existing', ?, 'clip.mp4', 'clip.mp4', 'completed', ?, ?)
            """,
            (task_id, now, now),
        )
        connection.commit()

    response = client.post(f"/api/tasks/{task_id}/process/auto-retry")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ai_retry_downstream_conflict"


def test_incomplete_analysis_fails_in_ai_stage(monkeypatch) -> None:
    task_id = "test-ai-retry-incomplete-stage"
    _create_task(task_id)
    incomplete_meta = {
        "analysis_incomplete": True,
        "coverage_ratio": 0.9444,
        "coverage_percent": 94.44,
    }
    monkeypatch.setattr(
        task_service,
        "process_task_ai_analysis",
        lambda _task_id: {
            "clips": [{"clip_id": "one"}],
            "analysis_run_id": "run-one",
            "analysis_path": "candidate_clips.json",
            "analysis_run": {"analysis_meta": incomplete_meta},
        },
    )
    monkeypatch.setattr(
        task_service,
        "validate_ai_analysis_meta_for_cut",
        lambda meta, _profile: meta,
    )

    result = PipelineEngine().run(
        task_id,
        retry=True,
        start_step=TaskStatus.AI_ANALYZING,
    )

    assert result["failed_step"] == TaskStatus.AI_ANALYZING.value
    assert result["failed_status"] == TaskStatus.FAILED_AI_ANALYZING.value
    assert task_service.get_task(task_id, include_video_probe=False)["status"] == TaskStatus.FAILED_AI_ANALYZING.value
