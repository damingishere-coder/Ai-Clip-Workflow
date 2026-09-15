from __future__ import annotations

import re
import json
import pytest

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


@pytest.mark.parametrize('strategy', [None, 'original', 'review'])
def test_task_create_and_upload_api_use_ten_minutes_and_twelve_candidates(monkeypatch, tmp_path, strategy):
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
        data={"task_name": "上传默认值测试", "platform": "general", "selection_profile": "general",
              **({'subtitle_strategy': strategy} if strategy else {})},
        files={"video_file": ("source.mp4", b"fake-video", "video/mp4")},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert captured["payload"].max_clip_duration == 10
    assert captured["payload"].candidate_clip_count == 12
    assert captured["payload"].subtitle_strategy == strategy


def test_new_task_page_selects_new_defaults():
    response = TestClient(app).get("/tasks/new", headers=_headers())

    assert response.status_code == 200
    assert re.search(r'name="max_clip_duration"[^>]*value="10"', response.text)
    assert re.search(r'<option value="12"\s+selected>12 条</option>', response.text)
    assert 'name="subtitle_strategy"' in response.text


@pytest.mark.parametrize('strategy', [None, 'original', 'review'])
def test_creation_freezes_subtitle_settings_without_changing_legacy_default(monkeypatch, strategy):
    task_id = f'{PREFIX}subtitle'
    monkeypatch.setattr('app.services.task_lifecycle_service.create_task_directory', lambda *_: None)
    try:
        create_task_record(TaskCreate(task_name='字幕设置', selection_profile='general',
                                     subtitle_strategy=strategy), task_id=task_id, task_dir_name=task_id)
        with get_connection() as c:
            config = json.loads(c.execute('SELECT auto_config_json FROM tasks WHERE id=?', (task_id,)).fetchone()[0])
        if strategy is None:
            assert 'subtitle_strategy' not in config and 'subtitle_delivery_mode' not in config
        else:
            assert config['subtitle_strategy'] == strategy
            assert config['subtitle_delivery_mode'] == ('original' if strategy == 'original' else 'subtitled')
            assert config['subtitle_decided_at']
    finally:
        with get_connection() as c:
            c.execute('DELETE FROM tasks WHERE id=?', (task_id,))
            c.commit()


def test_original_output_review_cannot_restart_auto_selection(monkeypatch):
    monkeypatch.setattr(tasks_router.task_service, 'get_task', lambda *a, **k: {
        'auto_mode':True, 'status':'pending_review', 'subtitle_strategy':'original',
        'output_clip_count':2, 'analysis_exists':True})
    monkeypatch.setattr(tasks_router, 'start_auto_pipeline', lambda *a, **k: pytest.fail('不应重新选片'))
    response = TestClient(app).post('/api/tasks/creation-skip/process/auto-resume', headers=_headers())
    assert response.status_code == 400 and '检查成片' in response.json()['detail']


def test_created_subtitle_policy_blocks_original_fallback_before_sync_mutations(monkeypatch):
    from app.services import publish_service
    task_id = f'{PREFIX}subtitle-sync'
    monkeypatch.setattr('app.services.task_lifecycle_service.create_task_directory', lambda *_: None)
    try:
        create_task_record(TaskCreate(task_name='新增字幕', selection_profile='general', subtitle_strategy='review'),
                           task_id=task_id, task_dir_name=task_id)
        monkeypatch.setattr(publish_service, '_list_completed_publish_clips', lambda *_: [{'output_clip_id':'unrendered'}])
        monkeypatch.setattr(publish_service, '_supersede_stale_publish_jobs', lambda *_: pytest.fail('不可先改发布记录'))
        with pytest.raises(ValueError, match='生成完整成片'):
            publish_service.sync_task_publish_jobs(task_id, prefer_subtitled=False)
    finally:
        with get_connection() as c:
            c.execute('DELETE FROM tasks WHERE id=?', (task_id,))
            c.commit()


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
