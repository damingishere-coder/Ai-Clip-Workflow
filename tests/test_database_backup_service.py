from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.db import database as database_module
from app.services.database_backup_service import (
    BackupSafetyError,
    apply_cleanup_plan,
    build_cleanup_plan,
    create_media_cleanup_backup,
    create_publish_migration_backup,
    create_schema_migration_backup,
    sqlite_quick_check,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def _create_database(path: Path, value: str = "ok") -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO sample(value) VALUES (?)", (value,))
    connection.commit()
    connection.close()


def _assert_portable_backup(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(journal_mode).lower() == "delete"
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(f"{path}{suffix}").exists()


def _set_local_time(path: Path, value: datetime) -> None:
    timestamp = value.timestamp()
    os.utime(path, (timestamp, timestamp))


def test_cleanup_uses_earlier_valid_backup_when_newest_is_corrupt(tmp_path):
    database_path = tmp_path / "workflow.sqlite3"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _create_database(database_path)

    older = backup_dir / "workflow-before-publish-migration-older.sqlite3"
    newer = backup_dir / "workflow-before-publish-migration-newer.sqlite3"
    _create_database(older, "older")
    newer.write_bytes(b"not a sqlite database")
    _set_local_time(older, datetime(2026, 7, 28, 1, 0, tzinfo=BEIJING))
    _set_local_time(newer, datetime(2026, 7, 28, 2, 0, tzinfo=BEIJING))

    plan = build_cleanup_plan(database_path, backup_dir)

    assert plan.keep_files == (older,)
    assert newer in plan.delete_files
    assert plan.invalid_files == (newer,)


def test_cleanup_keeps_latest_valid_backup_for_recent_14_days(tmp_path):
    database_path = tmp_path / "workflow.sqlite3"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _create_database(database_path)
    unrelated = backup_dir / "manual-backup.sqlite3"
    unrelated.write_text("do not touch", encoding="utf-8")

    backups = []
    start = datetime(2026, 7, 1, 12, 0, tzinfo=BEIJING)
    for offset in range(15):
        backup = backup_dir / (
            f"workflow-before-publish-migration-day-{offset:02d}.sqlite3"
        )
        _create_database(backup, str(offset))
        _set_local_time(backup, start + timedelta(days=offset))
        backups.append(backup)

    plan = build_cleanup_plan(database_path, backup_dir, keep_days=14)
    result = apply_cleanup_plan(plan, progress_every=0)

    assert len(plan.keep_files) == 14
    assert plan.delete_files == (backups[0],)
    assert result.deleted_files == 1
    assert unrelated.read_text(encoding="utf-8") == "do not touch"


def test_repeated_backup_within_24_hours_creates_only_one_file(tmp_path):
    database_path = tmp_path / "workflow.sqlite3"
    backup_dir = tmp_path / "backups"
    _create_database(database_path)
    now = datetime(2026, 7, 28, 1, 0, tzinfo=BEIJING)

    first = create_publish_migration_backup(
        database_path,
        backup_dir,
        now=now,
    )
    second = create_publish_migration_backup(
        database_path,
        backup_dir,
        now=now + timedelta(hours=1),
    )

    backups = list(backup_dir.glob("workflow-before-publish-migration-*.sqlite3"))
    assert first is not None
    assert second is None
    assert backups == [first]
    assert sqlite_quick_check(first) == "ok"
    _assert_portable_backup(first)


def test_schema_migration_backup_is_portable(tmp_path):
    database_path = tmp_path / "workflow.sqlite3"
    backup_dir = tmp_path / "backups"
    _create_database(database_path)

    backup = create_schema_migration_backup(
        database_path,
        backup_dir,
        "schema-test",
    )

    assert sqlite_quick_check(backup) == "ok"
    _assert_portable_backup(backup)


def test_media_cleanup_backup_is_always_created_and_valid(tmp_path):
    database_path = tmp_path / "workflow.sqlite3"
    backup_dir = tmp_path / "backups"
    _create_database(database_path)

    backup = create_media_cleanup_backup(database_path, backup_dir)

    assert backup.name.startswith("workflow-before-media-cleanup-")
    assert sqlite_quick_check(backup) == "ok"
    _assert_portable_backup(backup)


def test_concurrent_publish_migration_creates_one_valid_backup(monkeypatch, tmp_path):
    database_path = tmp_path / "workflow.sqlite3"
    settings = SimpleNamespace(
        database_path=database_path,
        data_dir=tmp_path,
        publish_default_mode="local_browser",
    )
    monkeypatch.setattr(database_module, "settings", settings)

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, platform TEXT)")
    connection.execute(
        """
        CREATE TABLE publish_jobs (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            output_clip_id TEXT,
            platform TEXT,
            publish_mode TEXT,
            status TEXT,
            provider_response TEXT,
            created_at TEXT,
            updated_at TEXT,
            error_code TEXT,
            error_message TEXT,
            last_error TEXT
        )
        """
    )
    database_module._migrate_publish_jobs_table(connection)
    connection.commit()
    connection.executemany(
        """
        INSERT INTO publish_jobs (
            id, task_id, output_clip_id, platform, publish_mode, status,
            created_at, updated_at
        ) VALUES (?, '', 'clip-1', 'douyin', 'local_browser', 'WAITING', ?, ?)
        """,
        [
            ("job-1", "2026-07-28T00:00:00+08:00", "2026-07-28T00:00:00+08:00"),
            ("job-2", "2026-07-28T00:01:00+08:00", "2026-07-28T00:01:00+08:00"),
        ],
    )
    connection.commit()
    connection.close()

    def run_migration() -> None:
        worker_connection = sqlite3.connect(database_path, timeout=10)
        worker_connection.row_factory = sqlite3.Row
        worker_connection.execute("PRAGMA busy_timeout = 10000")
        try:
            database_module._migrate_publish_jobs_table(worker_connection)
            worker_connection.commit()
        finally:
            worker_connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: run_migration(), range(2)))

    backups = list(
        (tmp_path / "backups").glob(
            "workflow-before-publish-migration-*.sqlite3"
        )
    )
    assert len(backups) == 1
    assert sqlite_quick_check(backups[0]) == "ok"
    connection = sqlite3.connect(database_path)
    statuses = [
        row[0]
        for row in connection.execute(
            "SELECT status FROM publish_jobs ORDER BY id"
        ).fetchall()
    ]
    connection.close()
    assert statuses.count("WAITING") == 1
    assert statuses.count("CANCELLED") == 1


def test_failed_backup_rolls_back_publish_data_migration(monkeypatch, tmp_path):
    database_path = tmp_path / "workflow.sqlite3"
    settings = SimpleNamespace(
        database_path=database_path,
        data_dir=tmp_path,
        publish_default_mode="local_browser",
    )
    monkeypatch.setattr(database_module, "settings", settings)

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, platform TEXT)")
    connection.execute(
        """
        CREATE TABLE publish_jobs (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            output_clip_id TEXT,
            platform TEXT,
            publish_mode TEXT,
            status TEXT,
            provider_response TEXT,
            created_at TEXT,
            updated_at TEXT,
            error_code TEXT,
            error_message TEXT,
            last_error TEXT
        )
        """
    )
    database_module._migrate_publish_jobs_table(connection)
    connection.commit()
    connection.execute(
        """
        INSERT INTO publish_jobs (
            id, task_id, output_clip_id, platform, publish_mode, status,
            created_at, updated_at
        ) VALUES (
            'job-1', '', 'clip-1', 'manual_export', 'manual_export', 'WAITING',
            '2026-07-28T00:00:00+08:00', '2026-07-28T00:00:00+08:00'
        )
        """
    )
    connection.commit()

    def fail_backup(*_args, **_kwargs):
        raise BackupSafetyError("simulated backup failure")

    monkeypatch.setattr(
        database_module,
        "create_publish_migration_backup",
        fail_backup,
    )

    with pytest.raises(BackupSafetyError, match="simulated backup failure"):
        database_module._migrate_publish_jobs_table(connection)

    row = connection.execute(
        "SELECT platform, publish_mode FROM publish_jobs WHERE id = 'job-1'"
    ).fetchone()
    connection.close()
    assert tuple(row) == ("manual_export", "manual_export")
