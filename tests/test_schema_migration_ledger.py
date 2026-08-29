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


def _prompt_version_foreign_keys(connection):
    return [
        row
        for row in connection.execute("PRAGMA foreign_key_list(ai_analysis_runs)").fetchall()
        if row["table"] == "ai_prompt_versions"
        and row["from"] == "prompt_version_id"
        and row["to"] == "id"
    ]


def _seed_analysis_run(connection, *, prompt_version_id: str) -> None:
    connection.execute(
        """
        INSERT INTO tasks (
            id, task_name, source_type, status, created_at, updated_at
        ) VALUES ('migration-task', '迁移测试任务', 'upload', 'completed', 'now', 'now')
        """
    )
    connection.execute(
        """
        INSERT INTO ai_analysis_runs (
            id, task_id, run_number, provider, provider_label, model,
            prompt_version_id, prompt_text_sha256, requested_clip_count,
            clip_count, analysis_payload_json, created_at, is_active
        ) VALUES (
            'migration-run', 'migration-task', 1, 'test', 'Test', 'test-model',
            ?, 'prompt-hash', 1, 1, '{}', 'now', 1
        )
        """,
        (prompt_version_id,),
    )
    connection.execute(
        """
        INSERT INTO clip_feedback (
            id, task_id, clip_candidate_id, analysis_run_id,
            selection_profile, decision, reason_code, created_at
        ) VALUES (
            'migration-feedback', 'migration-task', 'migration-clip',
            'migration-run', 'general', 'keep', 'worth_publishing', 'now'
        )
        """
    )


def _rebuild_ai_runs_without_prompt_fk(connection) -> None:
    schema_objects = connection.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE tbl_name = 'ai_analysis_runs'
          AND type IN ('index', 'trigger')
          AND sql IS NOT NULL
        ORDER BY type, name
        """
    ).fetchall()
    connection.execute(
        """
        CREATE TABLE ai_analysis_runs_without_prompt_fk (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            run_number INTEGER NOT NULL,
            provider TEXT NOT NULL,
            provider_label TEXT NOT NULL,
            model TEXT NOT NULL,
            ai_prompt_preset_id TEXT,
            ai_prompt_preset_name TEXT,
            prompt_version_id TEXT,
            prompt_text_sha256 TEXT,
            requested_clip_count INTEGER NOT NULL DEFAULT 5,
            clip_count INTEGER NOT NULL DEFAULT 0,
            analysis_summary TEXT,
            fallback_notice TEXT,
            analysis_payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        )
        """
    )
    columns_sql = ", ".join(database_module.AI_ANALYSIS_RUN_COLUMNS)
    connection.execute(
        f"INSERT INTO ai_analysis_runs_without_prompt_fk ({columns_sql}) "
        f"SELECT {columns_sql} FROM ai_analysis_runs"
    )
    connection.execute("DROP TABLE ai_analysis_runs")
    connection.execute(
        "ALTER TABLE ai_analysis_runs_without_prompt_fk RENAME TO ai_analysis_runs"
    )
    for schema_object in schema_objects:
        connection.execute(schema_object["sql"])
    connection.execute(
        "DELETE FROM schema_migrations WHERE version = ?",
        (database_module.AI_PROMPT_VERSION_FK_MIGRATION_VERSION,),
    )


def test_init_records_migration_once_and_switches_unique_index(isolated_database):
    database_module.init_db()
    database_module.init_db()

    with _connect(isolated_database) as connection:
        migrations = connection.execute(
            "SELECT version, name, checksum, applied_at FROM schema_migrations"
        ).fetchall()
        indexes = _index_names(connection)

    assert len(migrations) == 6
    migrations_by_version = {row["version"]: row for row in migrations}
    publish_migration = migrations_by_version[database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION]
    assert publish_migration["name"] == database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_NAME
    assert publish_migration["checksum"] == database_module.PUBLISH_ACTIVE_INDEX_MIGRATION_CHECKSUM
    assert publish_migration["applied_at"]
    upload_migration = migrations_by_version[database_module.TASK_UPLOAD_ONLY_MIGRATION_VERSION]
    assert upload_migration["name"] == database_module.TASK_UPLOAD_ONLY_MIGRATION_NAME
    assert upload_migration["checksum"] == database_module.TASK_UPLOAD_ONLY_MIGRATION_CHECKSUM
    assert upload_migration["applied_at"]
    content_review_migration = migrations_by_version[database_module.CONTENT_REVIEW_MIGRATION_VERSION]
    assert content_review_migration["name"] == database_module.CONTENT_REVIEW_MIGRATION_NAME
    assert content_review_migration["checksum"] == database_module.CONTENT_REVIEW_MIGRATION_CHECKSUM
    assert content_review_migration["applied_at"]
    export_migration = migrations_by_version[database_module.DOUYIN_ITEM_EXPORT_MIGRATION_VERSION]
    assert export_migration["name"] == database_module.DOUYIN_ITEM_EXPORT_MIGRATION_NAME
    assert export_migration["checksum"] == database_module.DOUYIN_ITEM_EXPORT_MIGRATION_CHECKSUM
    assert export_migration["applied_at"]
    feedback_migration = migrations_by_version[database_module.CONTENT_FEEDBACK_LOOP_MIGRATION_VERSION]
    assert feedback_migration["name"] == database_module.CONTENT_FEEDBACK_LOOP_MIGRATION_NAME
    assert feedback_migration["checksum"] == database_module.CONTENT_FEEDBACK_LOOP_MIGRATION_CHECKSUM
    assert feedback_migration["applied_at"]
    prompt_fk_migration = migrations_by_version[
        database_module.AI_PROMPT_VERSION_FK_MIGRATION_VERSION
    ]
    assert prompt_fk_migration["name"] == database_module.AI_PROMPT_VERSION_FK_MIGRATION_NAME
    assert (
        prompt_fk_migration["checksum"]
        == database_module.AI_PROMPT_VERSION_FK_MIGRATION_CHECKSUM
    )
    assert prompt_fk_migration["applied_at"]
    assert database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_NAME in indexes
    assert database_module.PUBLISH_ACTIVE_UNIQUE_INDEX_LEGACY_NAME not in indexes
    assert set(database_module.CONTENT_REVIEW_REQUIRED_INDEXES) <= indexes
    assert set(database_module.CONTENT_FEEDBACK_LOOP_REQUIRED_INDEXES) <= indexes
    with _connect(isolated_database) as connection:
        item_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(douyin_item_metric_snapshots)")
        }
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert set(database_module.DOUYIN_ITEM_EXPORT_COLUMNS) <= item_columns
    assert set(database_module.CONTENT_FEEDBACK_LOOP_REQUIRED_TABLES) <= tables
    with _connect(isolated_database) as connection:
        prompt_fks = _prompt_version_foreign_keys(connection)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    assert len(prompt_fks) == 1
    assert prompt_fks[0]["on_update"] == "NO ACTION"
    assert prompt_fks[0]["on_delete"] == "NO ACTION"
    assert violations == []


def test_content_feedback_migration_failure_rolls_back_and_can_retry(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version = ?",
            (database_module.CONTENT_FEEDBACK_LOOP_MIGRATION_VERSION,),
        )
        connection.execute("DROP TABLE content_improvement_experiment_items")
        connection.execute("DROP TABLE content_improvement_experiments")
        connection.commit()

        def deny_second_table(action, object_name, _arg2, _db_name, _source):
            if (
                action == sqlite3.SQLITE_CREATE_TABLE
                and object_name == "content_improvement_experiment_items"
            ):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(deny_second_table)
        with pytest.raises(database_module.SchemaMigrationError, match="not authorized"):
            database_module._run_schema_migrations(connection)
        connection.set_authorizer(None)

        tables_after_failure = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes_after_failure = _index_names(connection)
        ledger_after_failure = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (database_module.CONTENT_FEEDBACK_LOOP_MIGRATION_VERSION,),
        ).fetchone()[0]
        assert "content_improvement_experiments" not in tables_after_failure
        assert "content_improvement_experiment_items" not in tables_after_failure
        assert not set(database_module.CONTENT_FEEDBACK_LOOP_REQUIRED_INDEXES) <= indexes_after_failure
        assert ledger_after_failure == 0

        database_module._run_schema_migrations(connection)
        tables_after_retry = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        ledger_after_retry = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (database_module.CONTENT_FEEDBACK_LOOP_MIGRATION_VERSION,),
        ).fetchone()[0]
        assert set(database_module.CONTENT_FEEDBACK_LOOP_REQUIRED_TABLES) <= tables_after_retry
        assert set(database_module.CONTENT_FEEDBACK_LOOP_REQUIRED_INDEXES) <= _index_names(
            connection
        )
        assert ledger_after_retry == 1


def test_legacy_ai_runs_gain_prompt_fk_without_losing_data(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        prompt_version_id = connection.execute(
            "SELECT id FROM ai_prompt_versions ORDER BY created_at, id LIMIT 1"
        ).fetchone()[0]
        connection.execute("CREATE TABLE migration_run_audit (run_id TEXT NOT NULL)")
        connection.execute(
            """
            CREATE TRIGGER migration_run_audit_trigger
            AFTER INSERT ON ai_analysis_runs
            BEGIN
                INSERT INTO migration_run_audit (run_id) VALUES (NEW.id);
            END
            """
        )
        _seed_analysis_run(connection, prompt_version_id=prompt_version_id)
        _rebuild_ai_runs_without_prompt_fk(connection)
        connection.commit()
        assert _prompt_version_foreign_keys(connection) == []

    database_module.init_db()

    with _connect(isolated_database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        run = connection.execute(
            "SELECT id, task_id, prompt_version_id, is_active FROM ai_analysis_runs "
            "WHERE id = 'migration-run'"
        ).fetchone()
        feedback_run_id = connection.execute(
            "SELECT analysis_run_id FROM clip_feedback WHERE id = 'migration-feedback'"
        ).fetchone()[0]
        ledger_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (database_module.AI_PROMPT_VERSION_FK_MIGRATION_VERSION,),
        ).fetchone()[0]
        prompt_fks = _prompt_version_foreign_keys(connection)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        run_indexes = _index_names(connection)
        trigger_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND name='migration_run_audit_trigger'"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            connection.execute(
                "DELETE FROM ai_prompt_versions WHERE id = ?",
                (prompt_version_id,),
            )

    backups = list(
        (isolated_database.parent / "backups").glob(
            "workflow-before-ai-prompt-version-fk-*.sqlite3"
        )
    )
    assert dict(run) == {
        "id": "migration-run",
        "task_id": "migration-task",
        "prompt_version_id": prompt_version_id,
        "is_active": 1,
    }
    assert feedback_run_id == "migration-run"
    assert ledger_count == 1
    assert len(prompt_fks) == 1
    assert prompt_fks[0]["on_update"] == "NO ACTION"
    assert prompt_fks[0]["on_delete"] == "NO ACTION"
    assert violations == []
    assert {
        "idx_ai_analysis_runs_task_created",
        "idx_ai_analysis_runs_prompt_version",
    } <= run_indexes
    assert trigger_exists is not None
    assert len(backups) == 1


def test_prompt_fk_rebuild_failure_rolls_back_and_restores_foreign_keys(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        prompt_version_id = connection.execute(
            "SELECT id FROM ai_prompt_versions ORDER BY created_at, id LIMIT 1"
        ).fetchone()[0]
        _seed_analysis_run(connection, prompt_version_id=prompt_version_id)
        _rebuild_ai_runs_without_prompt_fk(connection)
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")

        def deny_recreated_index(action, object_name, _arg2, _db_name, _source):
            if (
                action == sqlite3.SQLITE_CREATE_INDEX
                and object_name == "idx_ai_analysis_runs_prompt_version"
            ):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(deny_recreated_index)
        with pytest.raises(database_module.SchemaMigrationError, match="not authorized"):
            database_module._run_schema_migrations(connection)
        connection.set_authorizer(None)

        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert _prompt_version_foreign_keys(connection) == []
        assert connection.execute(
            "SELECT prompt_version_id FROM ai_analysis_runs WHERE id = 'migration-run'"
        ).fetchone()[0] == prompt_version_id
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (database_module.AI_PROMPT_VERSION_FK_MIGRATION_VERSION,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='ai_analysis_runs_prompt_fk_new'"
        ).fetchone() is None

        database_module._run_schema_migrations(connection)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert len(_prompt_version_foreign_keys(connection)) == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (database_module.AI_PROMPT_VERSION_FK_MIGRATION_VERSION,),
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_prompt_fk_migration_rejects_orphan_reference_without_rewrite(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        prompt_version_id = connection.execute(
            "SELECT id FROM ai_prompt_versions ORDER BY created_at, id LIMIT 1"
        ).fetchone()[0]
        _seed_analysis_run(connection, prompt_version_id=prompt_version_id)
        _rebuild_ai_runs_without_prompt_fk(connection)
        connection.execute(
            "UPDATE ai_analysis_runs SET prompt_version_id = 'missing-prompt-version' "
            "WHERE id = 'migration-run'"
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")

        with pytest.raises(
            database_module.SchemaMigrationError,
            match="无法验证的 Prompt 版本引用",
        ):
            database_module._run_schema_migrations(connection)

        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert _prompt_version_foreign_keys(connection) == []
        assert connection.execute(
            "SELECT prompt_version_id FROM ai_analysis_runs WHERE id = 'migration-run'"
        ).fetchone()[0] == "missing-prompt-version"
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
            (database_module.AI_PROMPT_VERSION_FK_MIGRATION_VERSION,),
        ).fetchone()[0] == 0


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


def test_official_item_export_migration_is_backed_up_and_recorded(isolated_database):
    database_module.init_db()
    with _connect(isolated_database) as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version = ?",
            (database_module.DOUYIN_ITEM_EXPORT_MIGRATION_VERSION,),
        )
        connection.commit()

    database_module.init_db()

    backups = list(
        (isolated_database.parent / "backups").glob(
            "workflow-before-douyin-official-item-export-*.sqlite3"
        )
    )
    with _connect(isolated_database) as connection:
        ledger = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE version = ?",
            (database_module.DOUYIN_ITEM_EXPORT_MIGRATION_VERSION,),
        ).fetchone()
    assert len(backups) == 1
    assert ledger["checksum"] == database_module.DOUYIN_ITEM_EXPORT_MIGRATION_CHECKSUM


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
