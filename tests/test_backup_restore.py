import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from scripts.backup_restore import (
    BackupRestoreError,
    _assert_exclusive_database_access,
    create_backup_bundle,
    restore_backup_bundle,
    verify_backup_bundle,
)


COUNT_TABLES = (
    "tasks",
    "clip_candidates",
    "output_clip",
    "publish_jobs",
)


def _create_database(path: Path, *, multiplier: int = 1) -> dict[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE tasks (id TEXT PRIMARY KEY);
            CREATE TABLE clip_candidates (id TEXT PRIMARY KEY);
            CREATE TABLE output_clip (id TEXT PRIMARY KEY);
            CREATE TABLE publish_jobs (id TEXT PRIMARY KEY);
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            """
        )
        counts = {
            "tasks": 2 * multiplier,
            "clip_candidates": 3 * multiplier,
            "output_clip": 1 * multiplier,
            "publish_jobs": 2 * multiplier,
        }
        for table, count in counts.items():
            connection.executemany(
                f"INSERT INTO {table}(id) VALUES (?)",
                [(f"{table}-{index}",) for index in range(count)],
            )
        connection.commit()
        return counts
    finally:
        connection.close()


def _replace_database(path: Path, *, multiplier: int) -> dict[str, int]:
    if path.exists():
        path.unlink()
    return _create_database(path, multiplier=multiplier)


def _read_counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in COUNT_TABLES
        }
    finally:
        connection.close()


def test_backup_bundle_contains_verified_database_env_and_media(tmp_path: Path) -> None:
    database = tmp_path / "data" / "workflow.sqlite3"
    expected_counts = _create_database(database)
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"LOCAL_ADMIN_TOKEN=test-secret\n")
    media_root = tmp_path / "tasks"
    (media_root / "task-a").mkdir(parents=True)
    (media_root / "task-a" / "clip.mp4").write_bytes(b"demo-video")

    archive = create_backup_bundle(
        database_path=database,
        env_path=env_file,
        backup_dir=tmp_path / "backups",
        include_env=True,
        include_media=True,
        media_root=media_root,
        label="test",
        project_root=tmp_path,
    )

    manifest = verify_backup_bundle(archive)
    assert manifest["table_counts"] == expected_counts
    assert manifest["includes_env"] is True
    assert manifest["contains_secrets"] is True
    assert manifest["includes_media"] is True
    assert manifest["media_file_count"] == 1

    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("config/.env") == b"LOCAL_ADMIN_TOKEN=test-secret\n"
        assert bundle.read("media/tasks/task-a/clip.mp4") == b"demo-video"


def test_restore_roundtrip_creates_rollback_and_restores_counts(tmp_path: Path) -> None:
    database = tmp_path / "data" / "workflow.sqlite3"
    original_counts = _create_database(database)
    env_file = tmp_path / ".env"
    env_file.write_text("VALUE=original\n", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    archive = create_backup_bundle(
        database_path=database,
        env_path=env_file,
        backup_dir=backup_dir,
        include_env=True,
        label="roundtrip",
        project_root=tmp_path,
    )

    changed_counts = _replace_database(database, multiplier=2)
    env_file.write_text("VALUE=changed\n", encoding="utf-8")

    result = restore_backup_bundle(
        archive_path=archive,
        database_path=database,
        env_path=env_file,
        backup_dir=backup_dir,
        restore_env=True,
        project_root=tmp_path,
    )

    assert result["table_counts"] == original_counts
    assert env_file.read_text(encoding="utf-8") == "VALUE=original\n"

    rollback = Path(str(result["rollback_archive"]))
    assert rollback.is_file()
    rollback_manifest = verify_backup_bundle(rollback)
    assert rollback_manifest["table_counts"] == changed_counts
    assert rollback_manifest["label"] == "pre-restore"


def test_restore_media_conflict_keeps_database_and_destination_unchanged(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data" / "workflow.sqlite3"
    _create_database(database)
    env_file = tmp_path / ".env"
    env_file.write_text("VALUE=original\n", encoding="utf-8")
    media_root = tmp_path / "media-source"
    media_root.mkdir()
    (media_root / "source.mp4").write_bytes(b"source-video")

    archive = create_backup_bundle(
        database_path=database,
        env_path=env_file,
        backup_dir=tmp_path / "backups",
        include_env=False,
        include_media=True,
        media_root=media_root,
        label="media",
        project_root=tmp_path,
    )

    current_counts = _replace_database(database, multiplier=2)
    destination = tmp_path / "media-restore"
    destination.mkdir()
    (destination / "existing.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(BackupRestoreError, match="必须为空"):
        restore_backup_bundle(
            archive_path=archive,
            database_path=database,
            env_path=env_file,
            backup_dir=tmp_path / "backups",
            media_destination=destination,
            project_root=tmp_path,
        )

    assert _read_counts(database) == current_counts
    assert (destination / "existing.txt").read_text(encoding="utf-8") == "keep"
    assert env_file.read_text(encoding="utf-8") == "VALUE=original\n"


def test_invalid_archive_never_replaces_current_database(tmp_path: Path) -> None:
    database = tmp_path / "data" / "workflow.sqlite3"
    current_counts = _create_database(database)
    env_file = tmp_path / ".env"
    env_file.write_text("VALUE=current\n", encoding="utf-8")
    invalid_archive = tmp_path / "invalid.zip"

    with zipfile.ZipFile(invalid_archive, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "archive_format_version": 1,
                    "files": [],
                    "table_counts": current_counts,
                }
            ),
        )

    with pytest.raises(BackupRestoreError, match="缺少数据库快照"):
        restore_backup_bundle(
            archive_path=invalid_archive,
            database_path=database,
            env_path=env_file,
            backup_dir=tmp_path / "backups",
            restore_env=True,
            project_root=tmp_path,
        )

    assert _read_counts(database) == current_counts
    assert env_file.read_text(encoding="utf-8") == "VALUE=current\n"


def test_restore_refuses_database_with_active_write_lock(tmp_path: Path) -> None:
    database = tmp_path / "workflow.sqlite3"
    _create_database(database)
    connection = sqlite3.connect(database, isolation_level=None)
    connection.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(BackupRestoreError, match="独占锁"):
            _assert_exclusive_database_access(database)
    finally:
        connection.execute("ROLLBACK")
        connection.close()
