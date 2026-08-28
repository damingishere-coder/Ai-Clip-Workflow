from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.main import app
from app.models.task import TaskCreate
from app.routers import tasks as tasks_router
from app.services.task_lifecycle_service import create_task_record


PREFIX = "test-task-defaults-"


def _headers() -> dict[str, str]:
    if not settings.local_admin_token:
        return {}
    return {"Authorization": f"Bearer {settings.local_admin_token}"}


def test_task_create_and_upload_api_use_ten_minutes_and_twelve_candidates(monkeypatch, tmp_path):
    payload = TaskCreate(task_name="默认值模型测试", selection_profile="general")
    assert payload.max_clip_duration == 10
    assert payload.candidate_clip_count == 12

    captured: dict[str, TaskCreate] = {}
    saved_video = tmp_path / "source.mp4"
    saved_video.write_bytes(b"fake-video")

    monkeypatch.setattr(
        tasks_router,
        "allocate_task_dir_name",
        lambda task_name, exclude_task_id=None: "test-default-upload",
    )
    monkeypatch.setattr(
        tasks_router,
        "save_uploaded_video",
        lambda task_id, filename, source, task_dir_name: saved_video,
    )

    def fake_create_task_record(
        upload_payload: TaskCreate,
        task_id: str | None = None,
        task_dir_name: str | None = None,
    ) -> dict:
        captured["payload"] = upload_payload
        return {
            "id": task_id,
            "task_name": upload_payload.task_name,
            "detail_url": f"/tasks/{task_id}",
            "message": "任务已创建并写入数据库。",
        }

    monkeypatch.setattr(tasks_router.task_service, "create_task_record", fake_create_task_record)

    response = TestClient(app).post(
        "/api/tasks/upload",
        data={"task_name": "上传默认值测试", "platform": "general", "selection_profile": "general"},
        files={"video_file": ("source.mp4", b"fake-video", "video/mp4")},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert captured["payload"].max_clip_duration == 10
    assert captured["payload"].candidate_clip_count == 12


def test_new_task_page_selects_new_defaults():
    response = TestClient(app).get("/tasks/new", headers=_headers())

    assert response.status_code == 200
    assert re.search(r'name="max_clip_duration"[^>]*value="10"', response.text)
    assert re.search(r'<option value="12"\s+selected>12 条</option>', response.text)


def test_new_defaults_persist_without_rewriting_explicit_historical_values(monkeypatch):
    init_db()
    default_task_id = f"{PREFIX}new"
    historical_task_id = f"{PREFIX}historical"
    monkeypatch.setattr(
        "app.services.task_lifecycle_service.create_task_directory",
        lambda task_id, task_dir_name: None,
    )

    try:
        create_task_record(
            TaskCreate(task_name="新默认值", selection_profile="general"),
            task_id=default_task_id,
            task_dir_name=default_task_id,
        )
        create_task_record(
            TaskCreate(
                task_name="历史显式值",
                selection_profile="general",
                max_clip_duration=5,
                candidate_clip_count=5,
            ),
            task_id=historical_task_id,
            task_dir_name=historical_task_id,
        )

        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, max_clip_duration, candidate_clip_count
                FROM tasks
                WHERE id IN (?, ?)
                """,
                (default_task_id, historical_task_id),
            ).fetchall()
        values = {row["id"]: dict(row) for row in rows}

        assert values[default_task_id]["max_clip_duration"] == 10
        assert values[default_task_id]["candidate_clip_count"] == 12
        assert values[historical_task_id]["max_clip_duration"] == 5
        assert values[historical_task_id]["candidate_clip_count"] == 5
    finally:
        with get_connection() as connection:
            connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
            connection.commit()
