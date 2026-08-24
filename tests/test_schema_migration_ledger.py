import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.db import database as database_module


@pytest.fixture
def isolated_database(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    database_path = data_dir / "migration-ledger.sqlite3"
    tasks_dir = tmp_path / "tasks"
    test_settings = SimpleNamespace(
        data_dir=data_dir,
        database_path=database_path,
        tasks_dir=tasks_dir,
        publish_default_mode="local_browser",
    )
    monkeypatch.setattr(database_module, "settings", test_settings)
    return database_path


def _connect(database_path):
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _index_names(connection):
    return {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }


def test_init_records_migration_once_and_switches_unique_index(isolated_database):
    database_module.init_db()
    database_module.init_db()

    with _connect(isolated_database) as connection:
        migrations = connection.execute(
            "SELECT version, name, checksum, applied_at FROM schema_migrations"
        ).fetchall()
        indexes = _index_names(connection)

    assert len(migrations) == 2
    migrations_by_version = {row["version"]: row for row in migrations}
    publish_migration = migrations_by_version[database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION]
    assert publish_migration["name"] == database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_NAME
    assert publish_migration["checksum"] == database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_CHECKSUM
    assert publish_migration["applied_at"]
    upload_migration = migrations_by_version[database_module.TASK_UPLOAD_ONLY_MIGRATION_VERSION]
    assert upload_migration["name"] == database_module.TASK_UPLOAD_ONLY_MIGRATION_NAME
    assert upload_migration["checksum"] == database_module.TASK_UPLOAD_ONLY_MIGRATION_CHECKSUM
    assert upload_migration["applied_at"]
    assert database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_NAME in indexes
    assert database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_LEGACY_NAME not in indexes


def test_checksum_drift_refuses_startup(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = ?",
            (database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION,),
        )
        connection.commit()

    with pytest.raises(database_module.SchemaMigrationError, match="checksum"):
        database_module.init_db()


def test_existing_database_is_backed_up_before_publish_index_migration(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version = ?",
            (database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION,),
        )
        connection.execute(
            f"DROP INDEX {database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_NAME}"
        )
        connection.commit()

    database_module.init_db()

    backups = list(
        (isolated_database.parent / "backups").glob(
            "workflow-before-publish-active-unique-index-v2-*.sqlite3"
        )
    )
    assert len(backups) == 1


def test_legacy_nas_task_is_backed_up_and_normalized(isolated_database):
    database_module.init_db()
    legacy_path = r"E:\\历史原片\\source.mp4"
    with _connect(isolated_database) as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version = ?",
            (database_module.TASK_UPLOAD_ONLY_MIGRATION_VERSION,),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, source_type, nas_file_path, status, created_at, updated_at
            ) VALUES ('legacy-nas-task', '历史 NAS 任务', 'nas', ?, 'completed', 'now', 'now')
            """,
            (legacy_path,),
        )
        connection.commit()

    database_module.init_db()

    with _connect(isolated_database) as connection:
        task = connection.execute(
            "SELECT source_type, original_video_path, nas_file_path FROM tasks WHERE id = 'legacy-nas-task'"
        ).fetchone()
        ledger_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (database_module.TASK_UPLOAD_ONLY_MIGRATION_VERSION,),
        ).fetchone()[0]
    backups = list(
        (isolated_database.parent / "backups").glob("workflow-before-task-upload-only-*.sqlite3")
    )
    assert dict(task) == {
        "source_type": "upload",
        "original_video_path": legacy_path,
        "nas_file_path": None,
    }
    assert ledger_count == 1
    assert len(backups) == 1


def test_applied_migration_with_drifted_index_refuses_startup(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        connection.execute(
            f"DROP INDEX {database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_NAME}"
        )
        connection.execute(
            f"CREATE UNIQUE INDEX {database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_NAME} "
            "ON publish_jobs(id)"
        )
        connection.commit()

    with pytest.raises(database_module.SchemaMigrationError, match="定义发生漂移"):
        database_module.init_db()


def test_applied_migration_with_missing_index_refuses_startup(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        connection.execute(
            f"DROP INDEX {database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_NAME}"
        )
        connection.commit()

    with pytest.raises(database_module.SchemaMigrationError, match="不存在"):
        database_module.init_db()


def test_applied_migration_with_legacy_index_refuses_startup(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        connection.execute(
            f"""
            CREATE UNIQUE INDEX {database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_LEGACY_NAME}
            ON publish_jobs(output_clip_id, platform, publish_mode)
            WHERE status NOT IN ('PUBLISHED', 'EXPORTED', 'CANCELLED')
              AND output_clip_id IS NOT NULL AND output_clip_id <> ''
            """
        )
        connection.commit()

    with pytest.raises(database_module.SchemaMigrationError, match="仍存在"):
        database_module.init_db()


def test_malformed_ledger_is_backed_up_then_rejected(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        connection.execute("DROP TABLE schema_migrations")
        connection.execute("CREATE TABLE schema_migrations (version TEXT, name TEXT)")
        connection.commit()

    with pytest.raises(database_module.SchemaMigrationError, match="账本结构不完整"):
        database_module.init_db()

    backups = list(
        (isolated_database.parent / "backups").glob(
            "workflow-before-publish-active-unique-index-v2-*.sqlite3"
        )
    )
    assert len(backups) == 1


def test_unique_index_allows_failed_history_but_rejects_two_active_jobs(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        connection.executemany(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, platform, publish_mode,
                status, created_at, updated_at
            ) VALUES (?, 'task-1', 'clip-1', 'douyin', 'local_browser', ?, ?, ?)
            """,
            [
                ("failed-1", "FAILED", "2026-08-24T00:00:00+08:00", "2026-08-24T00:00:00+08:00"),
                ("failed-2", "FAILED", "2026-08-24T00:01:00+08:00", "2026-08-24T00:01:00+08:00"),
                ("active-1", "WAITING", "2026-08-24T00:02:00+08:00", "2026-08-24T00:02:00+08:00"),
            ],
        )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            connection.execute(
                """
                INSERT INTO publish_jobs (
                    id, task_id, output_clip_id, platform, publish_mode,
                    status, created_at, updated_at
                ) VALUES (
                    'active-2', 'task-1', 'clip-1', 'douyin', 'local_browser',
                    'NEED_REVIEW', '2026-08-24T00:03:00+08:00', '2026-08-24T00:03:00+08:00'
                )
                """
            )


def test_duplicate_active_jobs_fail_without_data_rewrite_or_ledger_entry(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version = ?",
            (database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION,),
        )
        connection.execute(
            f"DROP INDEX {database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_NAME}"
        )
        connection.executemany(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, platform, publish_mode,
                status, created_at, updated_at
            ) VALUES (?, 'task-1', 'clip-1', 'douyin', 'local_browser', ?, ?, ?)
            """,
            [
                ("job-waiting", "WAITING", "2026-08-24T00:00:00+08:00", "2026-08-24T00:00:00+08:00"),
                ("job-review", "NEED_REVIEW", "2026-08-24T00:01:00+08:00", "2026-08-24T00:01:00+08:00"),
            ],
        )
        connection.commit()

    with pytest.raises(database_module.SchemaMigrationError, match="重复的活动发布任务"):
        database_module.init_db()

    with _connect(isolated_database) as connection:
        statuses = dict(
            connection.execute(
                "SELECT id, status FROM publish_jobs WHERE id IN ('job-waiting', 'job-review')"
            ).fetchall()
        )
        ledger_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION,),
        ).fetchone()[0]
        indexes = _index_names(connection)

    assert statuses == {"job-waiting": "WAITING", "job-review": "NEED_REVIEW"}
    assert ledger_count == 0
    assert database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_NAME not in indexes


def test_new_index_verification_failure_preserves_legacy_index(
    isolated_database,
    monkeypatch,
):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version = ?",
            (database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION,),
        )
        connection.execute(
            f"DROP INDEX {database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_NAME}"
        )
        connection.execute(
            f"""
            CREATE UNIQUE INDEX {database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_LEGACY_NAME}
            ON publish_jobs(output_clip_id, platform, publish_mode)
            WHERE status NOT IN ('PUBLISHED', 'EXPORTED', 'CANCELLED')
              AND output_clip_id IS NOT NULL AND output_clip_id <> ''
            """
        )
        connection.commit()

    def fail_verification(_connection):
        raise database_module.SchemaMigrationError("simulated index verification failure")

    monkeypatch.setattr(
        database_module,
        "_verify_publish_active_unique_index",
        fail_verification,
    )

    with _connect(isolated_database) as connection:
        with pytest.raises(database_module.SchemaMigrationError, match="simulated"):
            database_module._run_schema_migrations(connection)

    with _connect(isolated_database) as connection:
        indexes = _index_names(connection)
        ledger_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION,),
        ).fetchone()[0]

    assert database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_LEGACY_NAME in indexes
    assert database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_NAME not in indexes
    assert ledger_count == 0


def test_concurrent_migration_runners_share_one_ledger_entry(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version = ?",
            (database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION,),
        )
        connection.execute(
            f"DROP INDEX {database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_NAME}"
        )
        connection.commit()

    def run_migration():
        with _connect(isolated_database) as connection:
            database_module._run_schema_migrations(connection)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: run_migration(), range(2)))

    with _connect(isolated_database) as connection:
        ledger_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION,),
        ).fetchone()[0]
        indexes = _index_names(connection)

    assert ledger_count == 1
    assert database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_NAME in indexes


def test_noncritical_index_errors_are_not_silenced():
    class FailingConnection:
        def execute(self, _sql):
            raise sqlite3.OperationalError("simulated index failure")

    with pytest.raises(sqlite3.OperationalError, match="simulated index failure"):
        database_module._create_indexes(FailingConnection())
