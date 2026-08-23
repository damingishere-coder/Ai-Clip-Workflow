from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.main import app
from app.models.task import TaskCreate, TaskStatus
from app.routers import tasks as tasks_router
from app.services import job_service
from app.services import task_lifecycle_service
from app.services.storage_service import (
    StorageSafetyError,
    configure_runtime_media_storage,
    save_uploaded_video,
)
from app.services.task_lifecycle_service import (
    TaskDeletionConflictError,
    create_task_record,
    delete_task_permanently,
    update_task_status,
)
from scripts.purge_deleted_task_media import apply_report, build_report


@pytest.fixture
def isolated_media_settings(tmp_path, monkeypatch):
    original_temp = tempfile.tempdir
    original_env = {name: os.environ.get(name) for name in ("TEMP", "TMP")}

    project_root = tmp_path / "project"
    data_dir = project_root / "data"
    storage_root = tmp_path / "e-drive" / "直播间切片工作流存储"
    replacements = {
        "project_root": project_root,
        "data_dir": data_dir,
        "database_path": data_dir / "workflow.sqlite3",
        "storage_root": storage_root,
        "tasks_dir": storage_root,
        "upload_temp_dir": storage_root / "_临时上传",
        "publish_scheduler_export_dir": storage_root / "_发布包",
    }
    settings_objects = []
    seen_settings = set()
    for module_name, module in tuple(sys.modules.items()):
        if not (module_name.startswith("app.") or module_name == "scripts.purge_deleted_task_media"):
            continue
        candidate = getattr(module, "settings", None)
        if candidate is None or not hasattr(candidate, "database_path") or id(candidate) in seen_settings:
            continue
        seen_settings.add(id(candidate))
        settings_objects.append(candidate)

    original_values = {
        id(candidate): {
            name: getattr(candidate, name)
            for name in replacements
        }
        for candidate in settings_objects
    }
    for candidate in settings_objects:
        for name, value in replacements.items():
            object.__setattr__(candidate, name, value)
    init_db()
    monkeypatch.setattr(
        task_lifecycle_service,
        "preflight_media",
        lambda *_args, **_kwargs: SimpleNamespace(to_dict=lambda: {"warnings": []}),
    )

    try:
        yield replacements
    finally:
        tempfile.tempdir = original_temp
        for name, value in original_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for candidate in settings_objects:
            for name, value in original_values[id(candidate)].items():
                object.__setattr__(candidate, name, value)


def _create_managed_task(task_id: str, task_dir: Path, *, auto_mode: bool = False) -> Path:
    source_path = task_dir / "source" / "source.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"managed-video")
    create_task_record(
        TaskCreate(
            task_name=task_id,
            source_type="upload",
            selection_profile="general",
            original_video_path=str(source_path),
            auto_mode=auto_mode,
        ),
        task_id=task_id,
        task_dir_name=task_id,
    )
    return source_path


def _headers() -> dict[str, str]:
    if not settings.local_admin_token:
        return {}
    return {"Authorization": f"Bearer {settings.local_admin_token}"}


def test_runtime_media_storage_uses_configured_e_drive(isolated_media_settings):
    result = configure_runtime_media_storage()

    assert Path(result["tasks_dir"]) == settings.tasks_dir.resolve()
    assert Path(result["upload_temp_dir"]) == settings.upload_temp_dir.resolve()
    assert Path(result["publish_export_dir"]) == settings.publish_scheduler_export_dir.resolve()
    assert tempfile.tempdir == str(settings.upload_temp_dir.resolve())
    assert os.environ["TEMP"] == str(settings.upload_temp_dir.resolve())
    assert os.environ["TMP"] == str(settings.upload_temp_dir.resolve())


def test_large_multipart_upload_spools_in_e_drive(monkeypatch, isolated_media_settings):
    captured = {}
    previous_temp = tempfile.tempdir
    previous_temp_env = {name: os.environ.get(name) for name in ("TEMP", "TMP")}

    def capture_upload(task_id, filename, file_object, task_dir_name=None):
        captured["rolled"] = bool(getattr(file_object, "_rolled", False))
        captured["tempdir"] = tempfile.tempdir
        return save_uploaded_video(task_id, filename, file_object, task_dir_name)

    monkeypatch.setattr(tasks_router, "save_uploaded_video", capture_upload)
    with TestClient(app) as client:
        response = client.post(
            "/api/tasks/upload",
            data={"task_name": "large-upload-e-drive", "platform": "general", "selection_profile": "general"},
            files={"video_file": ("source.mp4", b"V" * (2 * 1024 * 1024), "video/mp4")},
            headers=_headers(),
        )

    assert response.status_code == 200
    assert captured["rolled"] is True
    assert captured["tempdir"] == str(settings.upload_temp_dir.resolve())
    assert tempfile.tempdir == previous_temp
    assert {name: os.environ.get(name) for name in ("TEMP", "TMP")} == previous_temp_env
    with get_connection() as connection:
        row = connection.execute(
            "SELECT original_video_path FROM tasks WHERE id = ?",
            (response.json()["id"],),
        ).fetchone()
    assert Path(row["original_video_path"]).is_relative_to(settings.tasks_dir)


def test_failed_upload_removes_partial_task_directory(monkeypatch, isolated_media_settings):
    original_limit = settings.max_upload_size_bytes
    object.__setattr__(settings, "max_upload_size_bytes", 1024)
    monkeypatch.setattr(
        tasks_router,
        "allocate_task_dir_name",
        lambda task_name, exclude_task_id=None: "failed-upload-directory",
    )
    try:
        response = TestClient(app).post(
            "/api/tasks/upload",
            data={"task_name": "failed-upload", "platform": "general", "selection_profile": "general"},
            files={"video_file": ("source.mp4", b"V" * 2048, "video/mp4")},
            headers=_headers(),
        )
    finally:
        object.__setattr__(settings, "max_upload_size_bytes", original_limit)

    assert response.status_code == 400
    assert not (settings.tasks_dir / "failed-upload-directory").exists()


def test_delete_removes_managed_media_and_keeps_database_history(isolated_media_settings):
    task_id = "media-delete-001"
    task_dir = settings.tasks_dir / task_id
    _create_managed_task(task_id, task_dir)
    clip_path = task_dir / "05_clips" / "clip.mp4"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    clip_path.write_bytes(b"clip-video")
    export_path = settings.publish_scheduler_export_dir / task_id / "clip-1" / "clip.mp4"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_bytes(b"export-video")
    job_service.create_job(task_id, job_service.JOB_TYPE_VIDEO_CUT)

    result = delete_task_permanently(task_id)

    assert result["status"] == "deleted"
    assert result["freed_bytes"] >= len(b"managed-videoclip-videoexport-video")
    assert not task_dir.exists()
    assert not (settings.publish_scheduler_export_dir / task_id).exists()
    with get_connection() as connection:
        task = connection.execute("SELECT is_deleted FROM tasks WHERE id = ?", (task_id,)).fetchone()
        job = connection.execute(
            "SELECT status FROM workflow_jobs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    assert task["is_deleted"] == 1
    assert job["status"] == "cancelled"

    repeated = delete_task_permanently(task_id)
    assert repeated["status"] == "already_deleted"
    assert repeated["freed_bytes"] == 0


def test_delete_preserves_external_source(isolated_media_settings):
    task_id = "media-delete-external"
    external_source = settings.storage_root / "共享原片" / "source.mp4"
    external_source.parent.mkdir(parents=True, exist_ok=True)
    external_source.write_bytes(b"unique-original")
    create_task_record(
        TaskCreate(
            task_name=task_id,
            source_type="nas",
            selection_profile="general",
            nas_file_path=str(external_source),
        ),
        task_id=task_id,
        task_dir_name=task_id,
    )

    result = delete_task_permanently(task_id)

    assert result["external_source_preserved"] is True
    assert external_source.read_bytes() == b"unique-original"
    assert not (settings.tasks_dir / task_id).exists()


def test_delete_rejects_running_task(isolated_media_settings):
    task_id = "media-delete-running"
    task_dir = settings.tasks_dir / task_id
    _create_managed_task(task_id, task_dir)
    update_task_status(task_id, TaskStatus.transcribing)

    with pytest.raises(TaskDeletionConflictError, match="正在处理"):
        delete_task_permanently(task_id)

    assert task_dir.exists()
    with get_connection() as connection:
        row = connection.execute("SELECT is_deleted FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row["is_deleted"] == 0


def test_delete_rejects_running_workflow_job(isolated_media_settings):
    task_id = "media-delete-job"
    task_dir = settings.tasks_dir / task_id
    _create_managed_task(task_id, task_dir)
    job = job_service.create_job(task_id, job_service.JOB_TYPE_VIDEO_CUT)
    job_service.mark_job_running(job["id"])

    with pytest.raises(TaskDeletionConflictError, match="后台切片"):
        delete_task_permanently(task_id)

    assert task_dir.exists()


def test_delete_failure_keeps_task_visible(monkeypatch, isolated_media_settings):
    task_id = "media-delete-failure"
    task_dir = settings.tasks_dir / task_id
    _create_managed_task(task_id, task_dir)

    def fail_cleanup(_plan):
        raise RuntimeError("模拟文件被占用")

    monkeypatch.setattr(task_lifecycle_service, "apply_task_media_cleanup_plan", fail_cleanup)
    with pytest.raises(RuntimeError, match="文件被占用"):
        delete_task_permanently(task_id)

    assert task_dir.exists()
    with get_connection() as connection:
        row = connection.execute("SELECT is_deleted FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row["is_deleted"] == 0


def test_delete_rejects_path_traversal(isolated_media_settings):
    now = "2026-08-02T00:00:00+00:00"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (id, task_name, task_dir_name, status, created_at, updated_at)
            VALUES ('unsafe-task', 'unsafe-task', '..\\outside', 'completed', ?, ?)
            """,
            (now, now),
        )
        connection.commit()

    with pytest.raises(StorageSafetyError, match="不安全路径"):
        delete_task_permanently("unsafe-task")


def test_cleanup_report_dry_run_then_apply_only_deletes_hidden_tasks(isolated_media_settings):
    hidden_id = "media-hidden-001"
    active_id = "media-active-001"
    hidden_dir = settings.tasks_dir / hidden_id
    active_dir = settings.tasks_dir / active_id
    _create_managed_task(hidden_id, hidden_dir)
    _create_managed_task(active_id, active_dir)
    with get_connection() as connection:
        connection.execute(
            "UPDATE tasks SET is_deleted = 1, deleted_at = updated_at WHERE id = ?",
            (hidden_id,),
        )
        connection.commit()

    report = build_report()

    assert report["mode"] == "dry-run"
    reported_ids = {item["task_id"] for item in report["items"]}
    assert hidden_id in reported_ids
    assert active_id not in reported_ids
    assert hidden_dir.exists()
    assert active_dir.exists()

    applied = apply_report(report)

    assert applied["mode"] == "apply"
    assert applied["released_bytes"] > 0
    assert Path(applied["backup_path"]).exists()
    assert not hidden_dir.exists()
    assert active_dir.exists()
    assert applied["active_task_count_verified"] >= 1


def test_cleanup_aborts_before_deleting_overlapping_active_directory(isolated_media_settings):
    hidden_id = "media-hidden-overlap"
    active_id = "media-active-overlap"
    hidden_dir = settings.tasks_dir / hidden_id
    active_dir = settings.tasks_dir / active_id
    _create_managed_task(hidden_id, hidden_dir)
    _create_managed_task(active_id, active_dir)
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET is_deleted = 1, deleted_at = updated_at, task_dir_name = ?
            WHERE id = ?
            """,
            (active_id, hidden_id),
        )
        connection.commit()

    report = build_report()
    with pytest.raises(RuntimeError, match="有效任务目录重叠"):
        apply_report(report)

    assert active_dir.exists()
