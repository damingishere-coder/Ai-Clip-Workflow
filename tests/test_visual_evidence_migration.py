from concurrent.futures import ThreadPoolExecutor
import sqlite3
from types import SimpleNamespace

import pytest

from app.db import database as db, visual_evidence_migration as migration


@pytest.fixture
def old_database(monkeypatch, tmp_path):
    path = tmp_path / "data" / "old.sqlite3"
    monkeypatch.setattr(db, "settings", SimpleNamespace(data_dir=path.parent, database_path=path,
        tasks_dir=tmp_path/"tasks", publish_default_mode="local_browser"))
    registered = db._registered_schema_migrations
    with monkeypatch.context() as scope:
        scope.setattr(db, "_registered_schema_migrations", lambda: tuple(m for m in registered() if m.version < migration.VERSION))
        db.init_db()
    # 本文件独立验证第 13 项迁移；后续新增字段由其自己的兼容测试验证。
    monkeypatch.setattr(db, "_registered_schema_migrations", lambda: tuple(m for m in registered() if m.version <= migration.VERSION))
    with db.get_connection() as connection:
        connection.execute("INSERT INTO tasks(id,task_name,task_dir_name,selection_profile,created_at,updated_at) VALUES('old','历史','old','variety_comedy','before','before')")
        connection.execute("INSERT INTO ai_analysis_runs(id,task_id,run_number,provider,provider_label,model,analysis_payload_json,created_at) VALUES('old-run','old',1,'codex','Codex','old-model','{}','before')")
        connection.execute("INSERT INTO workflow_jobs(id,task_id,job_type,status,payload_json,checkpoint_json,created_at,updated_at) VALUES('old-job','old','ai_analysis','failed','{}','{\"existing_success\":true}','before','before')")
        connection.commit()
    return path


def facts():
    with db.get_connection() as connection:
        return {table:[tuple(r) for r in connection.execute(f"SELECT * FROM {table} ORDER BY id")] for table in ("tasks","workflow_jobs","ai_analysis_runs","ai_prompt_presets","ai_prompt_versions","content_profiles","content_profile_versions")}


def test_visual_upgrade_is_additive_idempotent_and_backup_is_restorable(old_database, tmp_path):
    before = facts()
    db.init_db()
    db.init_db()
    assert facts() == before
    with db.get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidate_visual_evidence").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    backups = list((old_database.parent/"backups").glob("*visual-evidence-v1*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup, sqlite3.connect(tmp_path/"restored.sqlite3") as restored:
        backup.backup(restored)
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT checkpoint_json FROM workflow_jobs WHERE id='old-job'").fetchone()[0] == '{"existing_success":true}'
        assert not restored.execute("SELECT 1 FROM sqlite_master WHERE name='candidate_visual_evidence'").fetchone()


def test_visual_migration_failure_rolls_back_schema_and_ledger(old_database, monkeypatch):
    before = facts()
    with monkeypatch.context() as scope:
        scope.setattr(migration, "STATEMENTS", (*migration.STATEMENTS[:2], "SELECT broken FROM missing_table"))
        with pytest.raises(db.SchemaMigrationError, match="missing_table"):
            db.init_db()
    assert facts() == before
    with db.get_connection() as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='candidate_visual_evidence'").fetchone()
        assert not connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()
    db.init_db()


def test_concurrent_visual_migrations_have_one_ledger(old_database):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: db.init_db(), range(2)))
    with db.get_connection() as connection:
        migration.verify(connection)
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()[0] == 1


def test_same_named_trigger_cannot_hide_weakened_immutable_contract(old_database):
    db.init_db()
    with db.get_connection() as connection:
        connection.execute("DROP TRIGGER immutable_visual_request")
        connection.execute("CREATE TRIGGER immutable_visual_request BEFORE UPDATE ON candidate_visual_evidence BEGIN SELECT 1; END")
        connection.commit()
    with pytest.raises(db.SchemaMigrationError, match="已改变"):
        db.init_db()
