from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.db import database as db, content_challenger_migration as migration
from app.services.content_intelligence_service import create_report
from tests.test_human_review import human_db as _human_db_fixture

human_db = _human_db_fixture


@pytest.fixture
def old_database(human_db, monkeypatch):
    path = human_db.parent / "old-challenger.sqlite3"
    db.settings.database_path = path
    registered = db._registered_schema_migrations
    with monkeypatch.context() as scope:
        scope.setattr(db, "_registered_schema_migrations", lambda: tuple(m for m in registered() if m.version < migration.VERSION))
        db.init_db()
    create_report("", 30, "migration-report")
    with db.get_connection() as c:
        before = dict(c.execute("SELECT * FROM content_intelligence_reports").fetchone())
    return path, before


def test_challenger_upgrade_preserves_reports_and_restorable_backup(old_database):
    path, before = old_database
    db.init_db()
    db.init_db()
    with db.get_connection() as c:
        assert dict(c.execute("SELECT * FROM content_intelligence_reports").fetchone()) == before
        assert c.execute("SELECT COUNT(*) FROM content_strategy_challengers").fetchone()[0] == 0
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    backups = list((path.parent / "backups").glob("*content-challengers-v1*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as original, sqlite3.connect(path.parent / "restored.sqlite3") as restored:
        original.backup(restored)
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not restored.execute("SELECT 1 FROM sqlite_master WHERE name='content_strategy_challengers'").fetchone()


def test_challenger_failed_migration_rolls_back_then_concurrent_retry(old_database, monkeypatch):
    with monkeypatch.context() as scope:
        scope.setattr(migration, "STATEMENTS", (migration.STATEMENTS[0], "SELECT * FROM missing_table"))
        with pytest.raises(db.SchemaMigrationError):
            db.init_db()
    with db.get_connection() as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='content_strategy_challengers'").fetchone()
        assert not c.execute("SELECT 1 FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: db.init_db(), range(2)))
    with db.get_connection() as c:
        migration.verify(c)
        assert c.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()[0] == 1
