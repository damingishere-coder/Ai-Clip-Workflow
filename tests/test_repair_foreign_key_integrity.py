"""外键完整性修复脚本的安全回归测试。

这些测试使用独立的临时 SQLite 数据库，不接触项目活动数据库。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import database as database_module
from scripts.repair_foreign_key_integrity import (
    apply_repair_plan,
    build_repair_plan,
)


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    return connection


def _foreign_key_violations(database_path: Path) -> list[tuple]:
    with _connect(database_path) as connection:
        return connection.execute("PRAGMA foreign_key_check").fetchall()


def _assert_portable_backup(database_path: Path) -> None:
    with _connect(database_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(journal_mode).lower() == "delete"
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(f"{database_path}{suffix}").exists()


def _create_database(tmp_path: Path) -> Path:
    database_path = tmp_path / "workflow.sqlite3"
    runtime_settings = database_module.settings
    original_values = {
        "database_path": runtime_settings.database_path,
        "data_dir": runtime_settings.data_dir,
        "tasks_dir": runtime_settings.tasks_dir,
    }
    try:
        object.__setattr__(runtime_settings, "database_path", database_path)
        object.__setattr__(runtime_settings, "data_dir", tmp_path)
        object.__setattr__(runtime_settings, "tasks_dir", tmp_path / "tasks")
        database_module.init_db()
    finally:
        for name, value in original_values.items():
            object.__setattr__(runtime_settings, name, value)
    return database_path


def _insert_task(connection: sqlite3.Connection, task_id: str) -> None:
    now = "2026-08-24T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO tasks (id, task_name, task_dir_name, source_type, created_at, updated_at)
        VALUES (?, ?, ?, 'upload', ?, ?)
        """,
        (task_id, task_id, task_id, now, now),
    )


def _insert_orphan_references(
    database_path: Path,
    *,
    publish_task_id: str = "task-a",
    subtitle_task_id: str | None = "task-a",
    orphan_output_clip_id: str = "orphan-clip-001",
) -> None:
    with _connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        _insert_task(connection, publish_task_id)
        if subtitle_task_id and subtitle_task_id != publish_task_id:
            _insert_task(connection, subtitle_task_id)
        now = "2026-08-24T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, platform, video_file_path, video_path,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'douyin', ?, ?, ?, ?)
            """,
            (
                "publish-orphan-001",
                publish_task_id,
                orphan_output_clip_id,
                r"D:\private\source.mp4",
                r"D:\private\source.mp4",
                now,
                now,
            ),
        )
        if subtitle_task_id:
            connection.execute(
                """
                INSERT INTO subtitle_jobs (
                    id, task_id, output_clip_id, subtitle_file_path,
                    output_file_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "subtitle-orphan-001",
                    subtitle_task_id,
                    orphan_output_clip_id,
                    r"D:\private\subtitle.srt",
                    r"D:\private\subtitle.mp4",
                    now,
                    now,
                ),
            )
        connection.commit()


def _insert_unexpected_task_orphan(database_path: Path) -> None:
    with _connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        now = "2026-08-24T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, clip_candidate_id, output_file_path, output_file_name,
                status, created_at, updated_at
            ) VALUES (?, ?, NULL, ?, ?, 'completed', ?, ?)
            """,
            (
                "output-with-missing-task",
                "task-does-not-exist",
                r"D:\private\original.mp4",
                "original.mp4",
                now,
                now,
            ),
        )
        connection.commit()


def test_build_repair_plan_is_dry_run_and_counts_supported_tombstone(
    tmp_path: Path,
):
    database_path = _create_database(tmp_path)
    _insert_orphan_references(database_path)
    before = _foreign_key_violations(database_path)

    plan = build_repair_plan(database_path)

    assert plan.violation_count == 2
    assert plan.tombstone_count == 1
    assert not plan.unsupported_violations
    assert len(_foreign_key_violations(database_path)) == len(before) == 2
    with _connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM output_clip WHERE id = ?",
            ("orphan-clip-001",),
        ).fetchone()[0] == 0


def test_apply_creates_verified_backup_and_clears_foreign_key_violations(
    tmp_path: Path,
):
    database_path = _create_database(tmp_path)
    _insert_orphan_references(database_path)
    backup_dir = tmp_path / "backups"

    result = apply_repair_plan(
        database_path,
        backup_dir,
        expected_violation_count=2,
    )

    backup_path = Path(result["backup_path"])
    assert backup_path.exists()
    _assert_portable_backup(backup_path)
    assert result["before_violation_count"] == 2
    assert result["after_violation_count"] == 0
    assert result["tombstone_count"] == 1
    assert _foreign_key_violations(database_path) == []
    with _connect(database_path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    with _connect(backup_path) as backup_connection:
        assert backup_connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert len(backup_connection.execute("PRAGMA foreign_key_check").fetchall()) == 2


def test_tombstone_is_inactive_and_does_not_copy_media_paths(tmp_path: Path):
    database_path = _create_database(tmp_path)
    _insert_orphan_references(database_path)

    apply_repair_plan(database_path, tmp_path / "backups", expected_violation_count=2)

    with _connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT task_id, status, is_active, output_file_path, output_file_name
            FROM output_clip WHERE id = ?
            """,
            ("orphan-clip-001",),
        ).fetchone()
    assert row["task_id"] == "task-a"
    assert row["status"] == "integrity_repair_tombstone"
    assert row["is_active"] == 0
    assert row["output_file_path"] in (None, "")
    assert row["output_file_name"] in (None, "")


def test_apply_rejects_unexpected_foreign_key_violation_without_writing(
    tmp_path: Path,
):
    database_path = _create_database(tmp_path)
    _insert_orphan_references(database_path)
    _insert_unexpected_task_orphan(database_path)
    before = len(_foreign_key_violations(database_path))
    backup_dir = tmp_path / "backups"

    plan = build_repair_plan(database_path)
    assert plan.unsupported_violations
    with pytest.raises((RuntimeError, ValueError), match="(unsupported|不支持|异常|拒绝)"):
        apply_repair_plan(database_path, backup_dir, expected_violation_count=before)

    assert len(_foreign_key_violations(database_path)) == before == 3
    with _connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM output_clip WHERE id = ?",
            ("orphan-clip-001",),
        ).fetchone()[0] == 0
    assert not list(backup_dir.glob("*")) if backup_dir.exists() else True


def test_apply_rejects_cross_task_orphan_reference_without_writing(tmp_path: Path):
    database_path = _create_database(tmp_path)
    _insert_orphan_references(database_path, subtitle_task_id="task-b")
    before = len(_foreign_key_violations(database_path))
    backup_dir = tmp_path / "backups"

    plan = build_repair_plan(database_path)
    assert plan.unsupported_violations
    with pytest.raises((RuntimeError, ValueError), match="(task|任务|冲突|拒绝)"):
        apply_repair_plan(database_path, backup_dir, expected_violation_count=before)

    assert len(_foreign_key_violations(database_path)) == before == 2
    assert not list(backup_dir.glob("*")) if backup_dir.exists() else True


def test_apply_rejects_unexpected_violation_count_without_writing(tmp_path: Path):
    database_path = _create_database(tmp_path)
    _insert_orphan_references(database_path)
    backup_dir = tmp_path / "backups"

    with pytest.raises((RuntimeError, ValueError), match="(count|数量|expected|预期)"):
        apply_repair_plan(database_path, backup_dir, expected_violation_count=99)

    assert len(_foreign_key_violations(database_path)) == 2
    with _connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM output_clip WHERE id = ?",
            ("orphan-clip-001",),
        ).fetchone()[0] == 0
    assert not list(backup_dir.glob("*")) if backup_dir.exists() else True
