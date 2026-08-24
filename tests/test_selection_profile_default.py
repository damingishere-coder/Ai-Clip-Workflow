"""新建任务必须显式选择选片模式，历史数据保持兼容。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.main import app
from app.models.task import TaskCreate
from app.routers import tasks as tasks_router


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.local_admin_token}"} if settings.local_admin_token else {}


def test_task_create_requires_selection_profile():
    with pytest.raises(ValidationError):
        TaskCreate(task_name="缺少模式")
    for profile in ("general", "variety_comedy", "long_live_talk"):
        assert TaskCreate(task_name=profile, selection_profile=profile).selection_profile == profile


def test_new_task_page_has_required_three_option_select():
    response = TestClient(app).get("/tasks/new", headers=_headers())
    assert response.status_code == 200
    assert '<select id="selection-profile" name="selection_profile" required>' in response.text
    assert 'name="selection_profile" value="variety_comedy"' not in response.text
    assert "通用内容价值" in response.text
    assert "康熙笑点选片模式" in response.text
    assert "长直播高光（语言类）" in response.text


def test_upload_form_rejects_missing_selection_profile():
    response = TestClient(app).post(
        "/api/tasks/upload",
        data={"task_name": "缺少模式", "platform": "general"},
        files={"video_file": ("source.mp4", b"fake-video", "video/mp4")},
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "请选择选片模式"


def test_json_task_creation_api_is_removed():
    response = TestClient(app).post("/api/tasks", json={"task_name": "缺少模式"}, headers=_headers())
    assert response.status_code == 405


@pytest.mark.parametrize("profile", ["general", "variety_comedy", "long_live_talk"])
def test_upload_accepts_each_explicit_profile(monkeypatch, tmp_path, profile):
    captured: dict[str, TaskCreate] = {}
    saved_video = tmp_path / "source.mp4"
    saved_video.write_bytes(b"fake-video")
    monkeypatch.setattr(tasks_router, "allocate_task_dir_name", lambda *_args, **_kwargs: "profile-test")
    monkeypatch.setattr(tasks_router, "save_uploaded_video", lambda *_args, **_kwargs: saved_video)

    def fake_create(payload, task_id=None, task_dir_name=None):
        captured["payload"] = payload
        return {"id": task_id, "detail_url": f"/tasks/{task_id}", "message": "已创建"}

    monkeypatch.setattr(tasks_router.task_service, "create_task_record", fake_create)
    response = TestClient(app).post(
        "/api/tasks/upload",
        data={"task_name": profile, "selection_profile": profile},
        files={"video_file": ("source.mp4", b"fake-video", "video/mp4")},
        headers=_headers(),
    )
    assert response.status_code == 200
    assert captured["payload"].selection_profile == profile


def test_historical_general_and_variety_modes_remain_unchanged(monkeypatch):
    from app.services.task_lifecycle_service import create_task_record
    from app.services.task_service import get_task

    init_db()
    monkeypatch.setattr("app.services.task_lifecycle_service.create_task_directory", lambda *_args: None)
    task_ids = ["sel-history-general", "sel-history-variety"]
    try:
        create_task_record(TaskCreate(task_name="历史通用", selection_profile="general"), task_id=task_ids[0], task_dir_name=task_ids[0])
        create_task_record(TaskCreate(task_name="历史综艺", selection_profile="variety_comedy"), task_id=task_ids[1], task_dir_name=task_ids[1])
        assert get_task(task_ids[0], include_video_probe=False)["selection_profile"] == "general"
        assert get_task(task_ids[1], include_video_probe=False)["selection_profile"] == "variety_comedy"
    finally:
        with get_connection() as connection:
            connection.executemany("DELETE FROM tasks WHERE id = ?", [(item,) for item in task_ids])
            connection.commit()
