from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.db import database as db, challenger_trial_migration as migration
from tests.test_human_review import human_db as _human_db_fixture

human_db = _human_db_fixture


@pytest.fixture
def old_database(human_db, monkeypatch):
    path = human_db.parent / "old-trial.sqlite3"
    db.settings.database_path = path
    registered = db._registered_schema_migrations
    with monkeypatch.context() as scope:
        scope.setattr(db, "_registered_schema_migrations", lambda: tuple(m for m in registered() if m.version < migration.VERSION))
        db.init_db()
    with db.get_connection() as c:
        c.execute("INSERT INTO tasks(id,task_name,task_dir_name,created_at,updated_at) VALUES('old','旧任务','old','2026-09-01','2026-09-01')")
        c.execute("INSERT INTO task_generation_rules(task_id,preset_id,prompt_text,copy_rules,frozen_at) VALUES('old','preset_001','旧冻结正文','','2026-09-01')")
        c.commit()
        before = dict(c.execute("SELECT * FROM task_generation_rules WHERE task_id='old'").fetchone())
    return path, before


def test_trial_upgrade_preserves_legacy_binding_and_backup_restore(old_database):
    path, before = old_database
    db.init_db()
    db.init_db()
    with db.get_connection() as c:
        after = dict(c.execute("SELECT * FROM task_generation_rules WHERE task_id='old'").fetchone())
        assert {key: after[key] for key in before} == before
        assert after["challenger_id"] is None and after["challenger_sha256"] is None
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        migration.verify(c)
    backups = list((path.parent / "backups").glob("*challenger-trials-v1*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as original, sqlite3.connect(path.parent / "restored.sqlite3") as restored:
        original.backup(restored)
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert "challenger_id" not in {r[1] for r in restored.execute("PRAGMA table_info(task_generation_rules)")}
        assert restored.execute("SELECT prompt_text FROM task_generation_rules WHERE task_id='old'").fetchone()[0] == "旧冻结正文"


def test_trial_partial_ddl_rolls_back_then_concurrent_retry(old_database, monkeypatch):
    with monkeypatch.context() as scope:
        scope.setattr(migration, "STATEMENTS", (migration.STATEMENTS[0], "SELECT * FROM missing_table"))
        with pytest.raises(db.SchemaMigrationError):
            db.init_db()
    with db.get_connection() as c:
        assert "challenger_id" not in {r[1] for r in c.execute("PRAGMA table_info(task_generation_rules)")}
        assert not c.execute("SELECT 1 FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: db.init_db(), range(2)))
    with db.get_connection() as c:
        migration.verify(c)
        assert c.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()[0] == 1
