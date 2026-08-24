"""P1.3：任务状态机与删除任务写保护。"""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.db.database import get_connection
from app.main import app
from app.models.task import TaskCreate, TaskStatus
from app.services import job_service
from app.services.task_lifecycle_service import (
    TaskStatusConflictError,
    create_task_record,
    update_task_status,
)


def _headers() -> dict[str, str]:
    if settings.local_admin_token:
        return {"Authorization": f"Bearer {settings.local_admin_token}"}
    return {}


@pytest.fixture(autouse=True)
def cleanup_tasks():
    yield
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE 'test-state-%'")
        connection.execute("DELETE FROM workflow_jobs WHERE task_id LIKE 'test-state-%'")
        connection.execute("DELETE FROM tasks WHERE id LIKE 'test-state-%'")
        connection.commit()


def _create_task(task_id: str, *, auto_mode: bool = False) -> dict:
    return create_task_record(
        TaskCreate(
            task_name=task_id,
            source_type="upload",
            platform="general",
            selection_profile="general",
            auto_mode=auto_mode,
        ),
        task_id=task_id,
        task_dir_name=task_id,
    )


def test_status_api_rejects_jump_from_empty_task_to_completed():
    task = _create_task("test-state-no-jump")

    with TestClient(app) as client:
        response = client.patch(
            f"/api/tasks/{task['id']}/status",
            json={"status": TaskStatus.completed.value},
            headers=_headers(),
        )

    assert response.status_code == 409
    with get_connection() as connection:
        row = connection.execute("SELECT status FROM tasks WHERE id = ?", (task["id"],)).fetchone()
    assert row["status"] == TaskStatus.pending_video.value


def test_status_api_allows_declared_adjacent_transition():
    task = _create_task("test-state-adjacent")
    with get_connection() as connection:
        connection.execute(
            "UPDATE tasks SET original_video_path = 'C:/managed/source.mp4' WHERE id = ?",
            (task["id"],),
        )
        connection.commit()

    with TestClient(app) as client:
        response = client.patch(
            f"/api/tasks/{task['id']}/status",
            json={"status": TaskStatus.pending_processing.value},
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json()["status"] == TaskStatus.pending_processing.value


def test_status_api_requires_source_before_pending_processing():
    task = _create_task("test-state-source-required")

    with TestClient(app) as client:
        response = client.patch(
            f"/api/tasks/{task['id']}/status",
            json={"status": TaskStatus.pending_processing.value},
            headers=_headers(),
        )

    assert response.status_code == 409
    assert "尚未绑定源视频" in response.json()["detail"]


def test_internal_status_write_rejects_deleted_task():
    task = _create_task("test-state-deleted")
    with get_connection() as connection:
        connection.execute("UPDATE tasks SET is_deleted = 1 WHERE id = ?", (task["id"],))
        connection.commit()

    with pytest.raises(TaskStatusConflictError, match="已永久删除"):
        update_task_status(task["id"], TaskStatus.completed)

    with get_connection() as connection:
        row = connection.execute("SELECT status FROM tasks WHERE id = ?", (task["id"],)).fetchone()
    assert row["status"] == TaskStatus.pending_video.value


def test_deleted_task_is_hidden_from_detail_and_media_apis():
    task = _create_task("test-state-hidden")
    with get_connection() as connection:
        connection.execute("UPDATE tasks SET is_deleted = 1 WHERE id = ?", (task["id"],))
        connection.commit()

    with TestClient(app) as client:
        detail = client.get(f"/api/tasks/{task['id']}", headers=_headers())
        media = client.get(f"/media/tasks/{task['id']}/source-video", headers=_headers())

    assert detail.status_code == 404
    assert media.status_code == 404


def test_public_cancel_also_requests_active_auto_pipeline_stop():
    task = _create_task("test-state-public-cancel", auto_mode=True)
    update_task_status(task["id"], TaskStatus.PREPARING_SOURCE)
    job, _created = job_service.create_or_get_active_job(
        task_id=task["id"],
        job_type=job_service.JOB_TYPE_AUTO_PIPELINE,
    )
    claimed = job_service.claim_job(job["id"], "public-cancel-owner")
    assert claimed is not None

    with TestClient(app) as client:
        response = client.patch(
            f"/api/tasks/{task['id']}/status",
            json={"status": TaskStatus.CANCELLED.value},
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json()["status"] == TaskStatus.CANCELLED.value
    current_job = job_service.get_job(job["id"])
    assert current_job["status"] == job_service.JOB_STATUS_RUNNING
    assert current_job["cancel_requested"] == 1
