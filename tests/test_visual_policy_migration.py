from concurrent.futures import ThreadPoolExecutor
import sqlite3
from types import SimpleNamespace

import pytest

from app.db import database as db, visual_policy_migration as migration


@pytest.fixture
def old_policy_db(monkeypatch, tmp_path):
    path = tmp_path / "data" / "old.sqlite3"
    monkeypatch.setattr(db, "settings", SimpleNamespace(data_dir=path.parent, database_path=path, tasks_dir=tmp_path/"tasks", publish_default_mode="local_browser"))
    registered = db._registered_schema_migrations
    with monkeypatch.context() as scope:
        scope.setattr(db, "_registered_schema_migrations", lambda: tuple(m for m in registered() if m.version < migration.VERSION))
        db.init_db()
    with db.get_connection() as connection:
        connection.execute("INSERT INTO tasks(id,task_name,task_dir_name,selection_profile,created_at,updated_at) VALUES('old','旧任务','old','general','before','before')")
        connection.execute("INSERT INTO task_generation_rules(task_id,preset_id,prompt_text,copy_rules,frozen_at) VALUES('old','preset_001','old prompt','','before')")
        connection.commit()
        before = {"task":dict(connection.execute("SELECT * FROM tasks WHERE id='old'").fetchone()), "rules":dict(connection.execute("SELECT * FROM task_generation_rules WHERE task_id='old'").fetchone())}
    return path, before


def test_old_policy_upgrade_keeps_unknown_versions_and_all_old_fields(old_policy_db, tmp_path):
    path, before = old_policy_db
    db.init_db()
    db.init_db()
    with db.get_connection() as connection:
        task = dict(connection.execute("SELECT * FROM tasks WHERE id='old'").fetchone())
        rules = dict(connection.execute("SELECT * FROM task_generation_rules WHERE task_id='old'").fetchone())
        assert task.pop("visual_enabled") == 0 and rules.pop("visual_policy_json") is None
        assert task == before["task"] and rules == before["rules"]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    backups = list((path.parent/"backups").glob("*visual-policy-v1*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as source, sqlite3.connect(tmp_path/"restored.sqlite3") as restored:
        source.backup(restored)
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert "visual_enabled" not in {r[1] for r in restored.execute("PRAGMA table_info(tasks)")}


def test_visual_policy_failed_ddl_rolls_back_then_retries(old_policy_db, monkeypatch):
    with monkeypatch.context() as scope:
        scope.setattr(migration, "STATEMENTS", (migration.STATEMENTS[0], "SELECT broken FROM missing_table"))
        with pytest.raises(db.SchemaMigrationError):
            db.init_db()
    with db.get_connection() as connection:
        assert "visual_enabled" not in {r[1] for r in connection.execute("PRAGMA table_info(tasks)")}
        assert not connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()
    db.init_db()


def test_visual_policy_concurrent_upgrade_has_one_ledger(old_policy_db):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: db.init_db(), range(2)))
    with db.get_connection() as connection:
        migration.verify(connection)
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()[0] == 1
