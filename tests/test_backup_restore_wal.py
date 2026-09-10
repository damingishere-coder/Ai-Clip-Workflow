from pathlib import Path
import sqlite3
import zipfile

import pytest

from scripts.backup_restore import DATABASE_ENTRY
from scripts.backup_restore_runtime import (
    BackupRestoreError,
    create_backup_bundle,
    verify_backup_bundle,
)


def _create_wal_database(path: Path) -> dict[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        assert str(mode).lower() == "wal"
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
            "tasks": 2,
            "clip_candidates": 3,
            "output_clip": 1,
            "publish_jobs": 2,
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


def test_wal_backup_is_single_file_delete_mode_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "data" / "workflow.sqlite3"
    expected_counts = _create_wal_database(database)

    archive_path = create_backup_bundle(
        database_path=database,
        env_path=tmp_path / ".env",
        backup_dir=tmp_path / "backups",
        include_env=False,
        label="wal",
        project_root=tmp_path,
    )

    manifest = verify_backup_bundle(archive_path)
    assert manifest["table_counts"] == expected_counts

    with zipfile.ZipFile(archive_path, mode="r") as archive:
        names = archive.namelist()
        assert DATABASE_ENTRY in names
        assert not any(
            name.endswith(("-wal", "-shm", "-journal")) for name in names
        )
        extracted = tmp_path / "extracted.sqlite3"
        extracted.write_bytes(archive.read(DATABASE_ENTRY))

    connection = sqlite3.connect(extracted)
    try:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "delete"
    finally:
        connection.close()


def test_verify_rejects_sqlite_sidecar_even_when_zip_is_valid(tmp_path: Path) -> None:
    archive_path = tmp_path / "sidecar.zip"
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr(DATABASE_ENTRY, b"not-a-database")
        archive.writestr(f"{DATABASE_ENTRY}-wal", b"temporary")

    with pytest.raises(BackupRestoreError, match="SQLite 临时 sidecar"):
        verify_backup_bundle(archive_path)
