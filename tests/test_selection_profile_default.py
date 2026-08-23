"""选片模式固定为「康熙笑点选片模式」的默认值与兼容测试。

新建任务不再提供「通用内容价值」选项，默认使用 variety_comedy；
历史 general 任务保持原样，label 显示为「通用模式（历史任务）」。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.main import app
from app.models.task import TaskCreate, TaskSelectionSettingsUpdate
from app.routers import tasks as tasks_router


def _headers() -> dict[str, str]:
    if not settings.local_admin_token:
        return {}
    return {"Authorization": f"Bearer {settings.local_admin_token}"}


def test_task_create_defaults_to_variety_comedy():
    payload = TaskCreate(task_name="选片默认值测试")
    assert payload.selection_profile == "variety_comedy"

    settings_update = TaskSelectionSettingsUpdate()
    assert settings_update.selection_profile == "variety_comedy"


def test_new_task_page_fixed_to_kangxi_profile():
    response = TestClient(app).get("/tasks/new", headers=_headers())

    assert response.status_code == 200
    assert '<input type="hidden" name="selection_profile" value="variety_comedy">' in response.text
    assert '<select name="selection_profile">' not in response.text
    assert "通用内容价值" not in response.text
    assert "综艺笑点优先" not in response.text
    assert "康熙笑点选片模式" in response.text


def test_upload_form_defaults_to_variety_comedy(monkeypatch, tmp_path):
    captured: dict[str, TaskCreate] = {}
    saved_video = tmp_path / "source.mp4"
    saved_video.write_bytes(b"fake-video")

    monkeypatch.setattr(
        tasks_router,
        "allocate_task_dir_name",
        lambda task_name, exclude_task_id=None: "test-default-selection",
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
        data={"task_name": "上传选片默认值测试", "platform": "general"},
        files={"video_file": ("source.mp4", b"fake-video", "video/mp4")},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert captured["payload"].selection_profile == "variety_comedy"


def test_variety_comedy_task_label_is_kangxi(monkeypatch):
    from app.services.task_lifecycle_service import create_task_record
    from app.services.task_service import get_task

    init_db()
    task_id = "sel-profile-label-test"
    monkeypatch.setattr(
        "app.services.task_lifecycle_service.create_task_directory",
        lambda task_id, task_dir_name: None,
    )

    try:
        create_task_record(
            TaskCreate(task_name="康熙默认任务"),
            task_id=task_id,
            task_dir_name=task_id,
        )
        task = get_task(task_id, include_video_probe=False)
        assert task["selection_profile"] == "variety_comedy"
        assert task["selection_profile_label"] == "康熙笑点选片模式"
    finally:
        with get_connection() as connection:
            connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            connection.commit()


def test_legacy_general_task_label_kept(monkeypatch):
    from app.services.task_lifecycle_service import create_task_record
    from app.services.task_service import get_task

    init_db()
    task_id = "sel-profile-general-test"
    monkeypatch.setattr(
        "app.services.task_lifecycle_service.create_task_directory",
        lambda task_id, task_dir_name: None,
    )

    try:
        create_task_record(
            TaskCreate(task_name="历史通用任务", selection_profile="general"),
            task_id=task_id,
            task_dir_name=task_id,
        )
        task = get_task(task_id, include_video_probe=False)
        assert task["selection_profile"] == "general"
        assert task["selection_profile_label"] == "通用模式（历史任务）"
    finally:
        with get_connection() as connection:
            connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            connection.commit()
