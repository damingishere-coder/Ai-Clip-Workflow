from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
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
    build_task_media_cleanup_plan,
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
        "max_upload_size_bytes": 4 * 1024 * 1024 * 1024,
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

    assert response.status_code == 200, response.text
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
    from app.services import storage_service

    original_limit = storage_service.settings.max_upload_size_bytes
    object.__setattr__(storage_service.settings, "max_upload_size_bytes", 1024)
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
        object.__setattr__(storage_service.settings, "max_upload_size_bytes", original_limit)

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
            selection_profile="general",
            original_video_path=str(external_source),
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

    monkeypatch.setattr(task_lifecycle_service, "stage_task_media_cleanup_plan", fail_cleanup)
    with pytest.raises(RuntimeError, match="文件被占用"):
        delete_task_permanently(task_id)

    assert task_dir.exists()
    with get_connection() as connection:
        row = connection.execute("SELECT is_deleted FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row["is_deleted"] == 0


def test_delete_rejects_path_traversal(isolated_media_settings):
    from app.services import storage_service

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

    with pytest.raises(storage_service.StorageSafetyError, match="不安全路径"):
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


def _staged_cleanup_api():
    """返回 P0.3 约定的暂存 API；生产实现尚未接入时给出明确测试失败。"""
    from app.services import storage_service

    names = (
        "stage_task_media_cleanup_plan",
        "rollback_staged_task_media_cleanup",
        "finalize_staged_task_media_cleanup",
    )
    missing = [name for name in names if not hasattr(storage_service, name)]
    if missing:
        pytest.fail(
            "P0.3 暂存删除 API 尚未实现：" + ", ".join(missing)
        )
    return tuple(getattr(storage_service, name) for name in names)


def _task_cleanup_plan(task_id: str):
    with get_connection() as connection:
        task = connection.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    assert task is not None
    return build_task_media_cleanup_plan(dict(task), include_legacy=False)


def test_staged_cleanup_rolls_back_when_second_move_fails(monkeypatch, isolated_media_settings):
    """第二个托管目录移动失败时，第一个目录必须恢复到原位置。"""
    stage_cleanup, _rollback_cleanup, _finalize_cleanup = _staged_cleanup_api()
    task_id = "staged-delete-second-move-failure"
    task_dir = settings.tasks_dir / task_id
    _create_managed_task(task_id, task_dir)
    export_dir = settings.publish_scheduler_export_dir / task_id
    export_dir.mkdir(parents=True)
    (export_dir / "clip.mp4").write_bytes(b"export")
    plan = _task_cleanup_plan(task_id)

    from app.services import storage_service

    original_move = storage_service.shutil.move
    move_calls = 0

    def fail_second_move(source, destination):
        nonlocal move_calls
        move_calls += 1
        if move_calls == 2:
            raise OSError("模拟第二个托管目录移动失败")
        return original_move(source, destination)

    monkeypatch.setattr(storage_service.shutil, "move", fail_second_move)
    with pytest.raises((OSError, RuntimeError), match="移动失败"):
        stage_cleanup(plan)

    assert task_dir.exists()
    assert export_dir.exists()
    assert (task_dir / "source" / "source.mp4").read_bytes() == b"managed-video"
    assert (export_dir / "clip.mp4").read_bytes() == b"export"


def test_database_commit_failure_restores_staged_media(monkeypatch, isolated_media_settings):
    """数据库提交失败时，删除流程不得留下文件已移走、任务仍可见的状态。"""
    _staged_cleanup_api()
    task_id = "staged-delete-db-commit-failure"
    task_dir = settings.tasks_dir / task_id
    _create_managed_task(task_id, task_dir)
    export_dir = settings.publish_scheduler_export_dir / task_id
    export_dir.mkdir(parents=True)
    (export_dir / "clip.mp4").write_bytes(b"export")

    original_get_connection = task_lifecycle_service.get_connection

    class CommitFailingConnection:
        def __init__(self, connection):
            self._connection = connection

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def commit(self):
            raise sqlite3.OperationalError("模拟数据库提交失败")

    @contextmanager
    def failing_connection():
        with original_get_connection() as connection:
            yield CommitFailingConnection(connection)

    monkeypatch.setattr(task_lifecycle_service, "get_connection", failing_connection)
    with pytest.raises(sqlite3.OperationalError, match="提交失败"):
        delete_task_permanently(task_id)

    assert task_dir.exists()
    assert export_dir.exists()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT is_deleted FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    assert row["is_deleted"] == 0


def test_final_cleanup_failure_returns_cleanup_pending_after_db_commit(
    monkeypatch,
    isolated_media_settings,
):
    """数据库已提交后隔离区清理失败，应保留已删除状态并返回待清理。"""
    _staged_cleanup_api()
    task_id = "staged-delete-final-cleanup-failure"
    task_dir = settings.tasks_dir / task_id
    _create_managed_task(task_id, task_dir)

    from app.services import storage_service

    original_rmtree = storage_service.shutil.rmtree
    rmtree_calls = 0

    def fail_final_cleanup(path, *args, **kwargs):
        nonlocal rmtree_calls
        rmtree_calls += 1
        if rmtree_calls == 1:
            raise OSError("模拟最终隔离区清理失败")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(storage_service.shutil, "rmtree", fail_final_cleanup)
    result = delete_task_permanently(task_id)

    assert result["status"] == "cleanup_pending"
    with get_connection() as connection:
        row = connection.execute(
            "SELECT is_deleted FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    assert row["is_deleted"] == 1
