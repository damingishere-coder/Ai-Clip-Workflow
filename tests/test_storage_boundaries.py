from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import media as media_router
from app.services import storage_service


@pytest.fixture
def isolated_storage_boundaries(tmp_path):
    runtime_settings = storage_service.settings
    replacements = {
        "project_root": tmp_path / "project",
        "data_dir": tmp_path / "data",
        "database_path": tmp_path / "data" / "missing.sqlite3",
        "storage_root": tmp_path / "storage",
        "tasks_dir": tmp_path / "storage" / "tasks",
    }
    originals = {name: getattr(runtime_settings, name) for name in replacements}
    for name, value in replacements.items():
        object.__setattr__(runtime_settings, name, value)
    runtime_settings.tasks_dir.mkdir(parents=True)
    try:
        yield replacements
    finally:
        for name, value in originals.items():
            object.__setattr__(runtime_settings, name, value)


@pytest.mark.parametrize("unsafe_name", ["..\\outside", "C:\\Windows", "\\\\server\\share"])
def test_task_directory_rejects_unsafe_names(isolated_storage_boundaries, unsafe_name):
    with pytest.raises(storage_service.StorageSafetyError, match="不安全路径"):
        storage_service.create_task_directory("task-safe", unsafe_name)


def test_task_directory_allows_literal_tilde(isolated_storage_boundaries):
    task_dir = storage_service.create_task_directory("task-tilde", "直播~精选")

    assert task_dir == storage_service.settings.tasks_dir / "直播~精选"
    assert task_dir.is_dir()


def test_task_directory_rejects_symlink_escape(isolated_storage_boundaries, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = storage_service.settings.tasks_dir / "linked-task"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前 Windows 环境不允许创建测试符号链接：{exc}")

    with pytest.raises(storage_service.StorageSafetyError, match="不安全"):
        storage_service.get_task_directory("task-link", "linked-task")


def test_atomic_task_directory_reservation_avoids_same_name_collision(isolated_storage_boundaries):
    def reserve() -> str:
        return storage_service.allocate_task_dir_name("同名并发任务")

    with ThreadPoolExecutor(max_workers=2) as executor:
        names = list(executor.map(lambda _index: reserve(), range(2)))

    assert len(set(names)) == 2
    assert all((storage_service.settings.tasks_dir / name).is_dir() for name in names)


def test_container_path_mapping_rejects_traversal(isolated_storage_boundaries):
    with pytest.raises(storage_service.StorageSafetyError, match="不安全路径"):
        storage_service.resolve_video_file_path("/workspace/tasks/../../outside.mp4")


def test_task_media_resolver_accepts_only_current_task_clip_directory(isolated_storage_boundaries, tmp_path):
    task_dir = storage_service.create_task_directory("task-media", "task-media")
    managed_clip = task_dir / "05_clips" / "clip.mp4"
    managed_clip.write_bytes(b"clip")
    outside_clip = tmp_path / "private.mp4"
    outside_clip.write_bytes(b"private")

    accepted = storage_service.resolve_task_media_file_path(
        str(managed_clip),
        task_id="task-media",
        task_dir_name="task-media",
        allowed_subdirectories=("05_clips", "clips"),
    )
    rejected = storage_service.resolve_task_media_file_path(
        str(outside_clip),
        task_id="task-media",
        task_dir_name="task-media",
        allowed_subdirectories=("05_clips", "clips"),
    )

    assert accepted == managed_clip
    assert rejected is None


def test_task_media_resolver_rejects_symlinked_clip_escape(isolated_storage_boundaries, tmp_path):
    task_dir = storage_service.create_task_directory("task-symlink", "task-symlink")
    outside_clip = tmp_path / "outside.mp4"
    outside_clip.write_bytes(b"private")
    linked_clip = task_dir / "05_clips" / "linked.mp4"
    try:
        linked_clip.symlink_to(outside_clip)
    except OSError as exc:
        pytest.skip(f"当前 Windows 环境不允许创建测试符号链接：{exc}")

    resolved = storage_service.resolve_task_media_file_path(
        str(linked_clip),
        task_id="task-symlink",
        task_dir_name="task-symlink",
        allowed_subdirectories=("05_clips",),
    )

    assert resolved is None


def test_output_media_route_rejects_database_path_outside_task(
    isolated_storage_boundaries,
    tmp_path,
    monkeypatch,
):
    storage_service.create_task_directory("task-route", "task-route")
    outside_clip = tmp_path / "sensitive.mp4"
    outside_clip.write_bytes(b"private")
    monkeypatch.setattr(
        media_router.task_service,
        "get_task",
        lambda *_args, **_kwargs: {"id": "task-route", "task_dir_name": "task-route"},
    )
    monkeypatch.setattr(
        media_router.task_service,
        "get_output_clip",
        lambda *_args, **_kwargs: {"id": "clip-1", "output_file_path": str(outside_clip)},
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(media_router.get_task_output_clip("task-route", "clip-1"))

    assert exc_info.value.status_code == 404


def test_output_media_route_accepts_current_task_clip(isolated_storage_boundaries, monkeypatch):
    task_dir = storage_service.create_task_directory("task-route-ok", "task-route-ok")
    clip_path = task_dir / "05_clips" / "clip.mp4"
    clip_path.write_bytes(b"clip")
    monkeypatch.setattr(
        media_router.task_service,
        "get_task",
        lambda *_args, **_kwargs: {"id": "task-route-ok", "task_dir_name": "task-route-ok"},
    )
    monkeypatch.setattr(
        media_router.task_service,
        "get_output_clip",
        lambda *_args, **_kwargs: {"id": "clip-1", "output_file_path": str(clip_path)},
    )

    response = asyncio.run(media_router.get_task_output_clip("task-route-ok", "clip-1"))

    assert Path(response.path) == clip_path


def test_source_media_route_keeps_allowed_external_source(isolated_storage_boundaries, monkeypatch):
    source_path = storage_service.settings.storage_root / "共享原片" / "source.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"source")
    monkeypatch.setattr(
        media_router.task_service,
        "get_task",
        lambda *_args, **_kwargs: {
            "id": "task-source",
            "task_dir_name": "task-source",
            "source_type": "upload",
            "original_video_path": str(source_path),
        },
    )

    response = asyncio.run(media_router.get_task_source_video("task-source"))

    assert Path(response.path) == source_path


def test_subtitled_media_route_rejects_database_path_outside_task(
    isolated_storage_boundaries,
    tmp_path,
    monkeypatch,
):
    storage_service.create_task_directory("task-subtitle", "task-subtitle")
    outside_clip = tmp_path / "sensitive.mp4"
    outside_clip.write_bytes(b"private")
    monkeypatch.setattr(
        media_router.task_service,
        "get_task",
        lambda *_args, **_kwargs: {"id": "task-subtitle", "task_dir_name": "task-subtitle"},
    )
    monkeypatch.setattr(
        media_router.task_service,
        "get_output_clip",
        lambda *_args, **_kwargs: {
            "id": "clip-1",
            "subtitled_output_file_path": str(outside_clip),
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(media_router.get_task_subtitled_clip("task-subtitle", "clip-1"))

    assert exc_info.value.status_code == 404


def test_cover_route_turns_unsafe_persisted_task_directory_into_404(monkeypatch):
    monkeypatch.setattr(
        media_router.task_service,
        "get_task",
        lambda *_args, **_kwargs: {"id": "task-cover", "task_dir_name": "..\\outside"},
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(media_router.get_task_cover("task-cover", "cover.jpg"))

    assert exc_info.value.status_code == 404


def test_cover_route_rejects_symlink_escape(isolated_storage_boundaries, tmp_path, monkeypatch):
    task_dir = storage_service.create_task_directory("task-cover-link", "task-cover-link")
    outside_cover = tmp_path / "private.jpg"
    outside_cover.write_bytes(b"private")
    linked_cover = task_dir / "07_covers" / "cover.jpg"
    try:
        linked_cover.symlink_to(outside_cover)
    except OSError as exc:
        pytest.skip(f"当前 Windows 环境不允许创建测试符号链接：{exc}")
    monkeypatch.setattr(
        media_router.task_service,
        "get_task",
        lambda *_args, **_kwargs: {
            "id": "task-cover-link",
            "task_dir_name": "task-cover-link",
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(media_router.get_task_cover("task-cover-link", "cover.jpg"))

    assert exc_info.value.status_code == 404
