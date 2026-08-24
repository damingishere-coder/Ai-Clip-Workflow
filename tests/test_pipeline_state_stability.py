"""P1.3：全自动流水线取消、终态和失败证据。"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from app.db.database import get_connection, init_db
from app.models.task import TaskCreate, TaskStatus
from app.services import job_service, task_service
from app.services.pipeline_engine import PipelineEngine
from app.services.task_lifecycle_service import create_task_record
from app.services.task_service import get_task


@pytest.fixture(autouse=True)
def cleanup_pipeline_data():
    yield
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE 'test-pipeline-state-%'")
        connection.execute("DELETE FROM workflow_jobs WHERE task_id LIKE 'test-pipeline-state-%'")
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE 'test-pipeline-state-%'")
        connection.execute("DELETE FROM tasks WHERE id LIKE 'test-pipeline-state-%'")
        connection.commit()


def _create_auto_task(task_id: str) -> dict:
    create_task_record(
        TaskCreate(
            task_name=task_id,
            source_type="upload",
            platform="general",
            selection_profile="general",
            auto_mode=True,
        ),
        task_id=task_id,
        task_dir_name=task_id,
    )
    return get_task(task_id, include_video_probe=False)


def _engine_for_last_step(monkeypatch, task_id: str) -> PipelineEngine:
    engine = PipelineEngine()
    monkeypatch.setattr(engine, "_create_publish_jobs", Mock(return_value={"created_count": 1}))
    monkeypatch.setattr(
        engine,
        "_write_task_summary",
        Mock(return_value={"summary_path": f"C:/tmp/{task_id}.json"}),
    )
    return engine


def test_success_stays_ready_to_publish(monkeypatch):
    task = _create_auto_task("test-pipeline-state-ready")
    engine = _engine_for_last_step(monkeypatch, task["id"])

    result = engine.run(task["id"], start_step=TaskStatus.PUBLISH_JOB_CREATING)

    assert result["status"] == "ready_to_publish"
    assert get_task(task["id"], include_video_probe=False)["status"] == TaskStatus.READY_TO_PUBLISH.value
    assert task_service.get_task_live_status(task["id"])["should_poll"] is False


def test_cancel_after_step_does_not_write_ready(monkeypatch):
    task = _create_auto_task("test-pipeline-state-cancel")
    job, _created = job_service.create_or_get_active_job(
        task_id=task["id"], job_type=job_service.JOB_TYPE_AUTO_PIPELINE
    )
    claimed = job_service.claim_job(job["id"], "cancel-after-step-owner")
    assert claimed is not None
    engine = _engine_for_last_step(monkeypatch, task["id"])
    cancelled = iter([False, True])
    monkeypatch.setattr(job_service, "is_cancel_requested", lambda _job_id: next(cancelled))
    monkeypatch.setattr(job_service, "update_job_progress", Mock())
    monkeypatch.setattr(job_service, "heartbeat_job", Mock())

    with job_service.job_lease_context(
        claimed["id"],
        "cancel-after-step-owner",
        claimed["lease_token"],
    ):
        result = engine.run(
            task["id"],
            start_step=TaskStatus.PUBLISH_JOB_CREATING,
            job_id=job["id"],
        )

    assert result["status"] == "cancelled"
    assert get_task(task["id"], include_video_probe=False)["status"] == TaskStatus.CANCELLED.value


def test_summary_write_failure_does_not_hide_business_failure(monkeypatch):
    task = _create_auto_task("test-pipeline-state-summary")
    engine = PipelineEngine()
    monkeypatch.setattr(engine, "_create_publish_jobs", Mock(side_effect=ValueError("原始业务错误")))
    monkeypatch.setattr(engine, "_write_task_summary", Mock(side_effect=OSError("磁盘暂不可写")))

    result = engine.run(task["id"], start_step=TaskStatus.PUBLISH_JOB_CREATING)

    assert result["status"] == "failed"
    assert result["last_error"] == "原始业务错误"
    assert result["summary_path"] == ""
    assert get_task(task["id"], include_video_probe=False)["last_error"] == "原始业务错误"


def test_request_cancel_marks_auto_task_cancelled():
    task = _create_auto_task("test-pipeline-state-request-cancel")
    job, _created = job_service.create_or_get_active_job(
        task_id=task["id"], job_type=job_service.JOB_TYPE_AUTO_PIPELINE
    )

    cancelled = job_service.request_job_cancel(job["id"])

    assert cancelled["status"] == job_service.JOB_STATUS_CANCELLED
    assert get_task(task["id"], include_video_probe=False)["status"] == TaskStatus.CANCELLED.value


def test_cancelled_status_survives_database_initialization():
    task = _create_auto_task("test-pipeline-state-restart")
    job, _created = job_service.create_or_get_active_job(
        task_id=task["id"], job_type=job_service.JOB_TYPE_AUTO_PIPELINE
    )
    job_service.request_job_cancel(job["id"])

    init_db()

    assert get_task(task["id"], include_video_probe=False)["status"] == TaskStatus.CANCELLED.value


def test_cancel_requested_running_job_cannot_be_marked_completed():
    task = _create_auto_task("test-pipeline-state-terminal-race")
    job, _created = job_service.create_or_get_active_job(
        task_id=task["id"], job_type=job_service.JOB_TYPE_AUTO_PIPELINE
    )
    claimed = job_service.claim_job(job["id"], "test-owner")
    token = claimed["lease_token"]
    job_service.request_job_cancel(job["id"])

    with job_service.job_lease_context(job["id"], "test-owner", token):
        with pytest.raises(job_service.JobLeaseLostError):
            job_service.mark_job_completed(job["id"], {"status": "ready_to_publish"})

    current = job_service.get_job(job["id"])
    assert current["status"] == job_service.JOB_STATUS_RUNNING
    assert current["cancel_requested"] == 1
    assert get_task(task["id"], include_video_probe=False)["status"] == TaskStatus.CANCELLED.value


def test_cancel_between_final_check_and_ready_write_wins(monkeypatch):
    task = _create_auto_task("test-pipeline-state-final-race")
    job, _created = job_service.create_or_get_active_job(
        task_id=task["id"], job_type=job_service.JOB_TYPE_AUTO_PIPELINE
    )
    claimed = job_service.claim_job(job["id"], "final-race-owner")
    assert claimed is not None
    engine = _engine_for_last_step(monkeypatch, task["id"])
    monkeypatch.setattr(job_service, "update_job_progress", Mock())
    monkeypatch.setattr(job_service, "heartbeat_job", Mock())
    monkeypatch.setattr(
        engine,
        "_checkpoint_outputs",
        Mock(
            return_value={
                "created_ids": ["checkpoint-publish"],
                "skipped_ids": [],
                "created_count": 1,
                "skipped_count": 0,
                "schedule_input": {"sha256": "test"},
            }
        ),
    )
    checks = 0

    def cancel_after_final_check(_job_id):
        nonlocal checks
        checks += 1
        if checks == 3:
            job_service.request_job_cancel(job["id"])

    monkeypatch.setattr(engine, "_raise_if_cancelled", cancel_after_final_check)

    with job_service.job_lease_context(job["id"], "final-race-owner", claimed["lease_token"]):
        result = engine.run(
            task["id"],
            start_step=TaskStatus.PUBLISH_JOB_CREATING,
            job_id=job["id"],
        )

    assert result["status"] == "cancelled"
    assert get_task(task["id"], include_video_probe=False)["status"] == TaskStatus.CANCELLED.value


def test_cancel_pipeline_cancels_only_jobs_created_by_current_run():
    task = _create_auto_task("test-pipeline-state-publish-cleanup")
    now = task_service._now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, output_file_path, output_file_name, status,
                is_active, created_at, updated_at
            ) VALUES ('pipeline-output', ?, '', 'clip.mp4', 'completed', 1, ?, ?)
            """,
            (task["id"], now, now),
        )
        for job_id, platform in (
            ("pipeline-created-job", "douyin"),
            ("pipeline-existing-job", "bilibili"),
        ):
            connection.execute(
                """
                INSERT INTO publish_jobs (
                    id, task_id, output_clip_id, platform, status, created_at, updated_at
                ) VALUES (?, ?, 'pipeline-output', ?, 'WAITING', ?, ?)
                """,
                (job_id, task["id"], platform, now, now),
            )
        connection.commit()

    cancelled = PipelineEngine()._cancel_unpublished_auto_jobs(
        task["id"],
        {TaskStatus.PUBLISH_JOB_CREATING.value: {"created": [{"id": "pipeline-created-job"}]}},
    )

    with get_connection() as connection:
        rows = {
            row["id"]: row["status"]
            for row in connection.execute(
                "SELECT id, status FROM publish_jobs WHERE task_id = ?", (task["id"],)
            ).fetchall()
        }
    assert cancelled == 1
    assert rows == {
        "pipeline-created-job": "CANCELLED",
        "pipeline-existing-job": "WAITING",
    }


def test_old_worker_cannot_write_ready_after_lease_takeover():
    task = _create_auto_task("test-pipeline-state-ready-fence")
    job, _created = job_service.create_or_get_active_job(
        task_id=task["id"], job_type=job_service.JOB_TYPE_AUTO_PIPELINE
    )
    old_claim = job_service.claim_job(job["id"], "old-owner")
    with get_connection() as connection:
        connection.execute(
            "UPDATE workflow_jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (job["id"],),
        )
        connection.commit()
    new_claim = job_service.claim_job(job["id"], "new-owner")
    assert new_claim is not None

    with job_service.job_lease_context(job["id"], "old-owner", old_claim["lease_token"]):
        with pytest.raises(job_service.JobLeaseLostError):
            PipelineEngine()._mark_ready_to_publish(task["id"], job["id"])

    assert get_task(task["id"], include_video_probe=False)["status"] == TaskStatus.CREATED.value


def test_old_worker_cannot_cancel_publish_draft_after_lease_takeover():
    task = _create_auto_task("test-pipeline-state-cancel-publish-fence")
    job, _created = job_service.create_or_get_active_job(
        task_id=task["id"], job_type=job_service.JOB_TYPE_AUTO_PIPELINE
    )
    old_claim = job_service.claim_job(job["id"], "old-cancel-owner")
    assert old_claim is not None
    now = task_service._now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, output_file_path, output_file_name, status,
                is_active, created_at, updated_at
            ) VALUES ('pipeline-fenced-output', ?, '', 'clip.mp4', 'completed', 1, ?, ?)
            """,
            (task["id"], now, now),
        )
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, platform, status, created_at, updated_at
            ) VALUES ('pipeline-fenced-publish', ?, 'pipeline-fenced-output', 'douyin', 'WAITING', ?, ?)
            """,
            (task["id"], now, now),
        )
        connection.execute(
            "UPDATE workflow_jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (job["id"],),
        )
        connection.commit()
    new_claim = job_service.claim_job(job["id"], "new-cancel-owner")
    assert new_claim is not None

    with job_service.job_lease_context(job["id"], "old-cancel-owner", old_claim["lease_token"]):
        with pytest.raises(job_service.JobLeaseLostError):
            PipelineEngine()._cancel_unpublished_auto_jobs(
                task["id"],
                {
                    "workflow_job_id": job["id"],
                    TaskStatus.PUBLISH_JOB_CREATING.value: {
                        "created": [{"id": "pipeline-fenced-publish"}]
                    },
                },
            )

    with get_connection() as connection:
        status = connection.execute(
            "SELECT status FROM publish_jobs WHERE id = 'pipeline-fenced-publish'"
        ).fetchone()["status"]
    assert status == "WAITING"


def test_expired_cancel_requested_job_is_recovered_as_cancelled():
    task = _create_auto_task("test-pipeline-state-cancel-recovery")
    job, _created = job_service.create_or_get_active_job(
        task_id=task["id"], job_type=job_service.JOB_TYPE_AUTO_PIPELINE
    )
    claimed = job_service.claim_job(job["id"], "cancel-recovery-owner")
    assert claimed is not None
    job_service.request_job_cancel(job["id"])
    with get_connection() as connection:
        connection.execute(
            "UPDATE workflow_jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (job["id"],),
        )
        connection.commit()

    job_service.claim_next_job("recovery-worker")

    recovered = job_service.get_job(job["id"])
    assert recovered["status"] == job_service.JOB_STATUS_CANCELLED
    assert recovered["lease_owner"] is None
    assert recovered["lease_token"] is None


def test_job_cancel_cleans_only_publish_jobs_linked_to_same_pipeline():
    task = _create_auto_task("test-pipeline-state-linked-publish")
    job, _created = job_service.create_or_get_active_job(
        task_id=task["id"], job_type=job_service.JOB_TYPE_AUTO_PIPELINE
    )
    now = task_service._now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, output_file_path, output_file_name, status,
                is_active, created_at, updated_at
            ) VALUES ('linked-pipeline-output', ?, '', 'clip.mp4', 'completed', 1, ?, ?)
            """,
            (task["id"], now, now),
        )
        for publish_job_id, workflow_job_id, platform in (
            ("linked-pipeline-publish", job["id"], "douyin"),
            ("other-pipeline-publish", "another-workflow-job", "bilibili"),
        ):
            provider_response = json.dumps(
                {"source": "auto_pipeline", "workflow_job_id": workflow_job_id},
                ensure_ascii=False,
            )
            connection.execute(
                """
                INSERT INTO publish_jobs (
                    id, task_id, output_clip_id, platform, status,
                    provider_response, created_at, updated_at
                ) VALUES (?, ?, 'linked-pipeline-output', ?, 'SCHEDULED', ?, ?, ?)
                """,
                (publish_job_id, task["id"], platform, provider_response, now, now),
            )
        connection.commit()

    job_service.request_job_cancel(job["id"])

    with get_connection() as connection:
        statuses = {
            row["id"]: row["status"]
            for row in connection.execute(
                "SELECT id, status FROM publish_jobs WHERE task_id = ?",
                (task["id"],),
            ).fetchall()
        }
    assert statuses == {
        "linked-pipeline-publish": "CANCELLED",
        "other-pipeline-publish": "SCHEDULED",
    }
