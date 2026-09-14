from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.db import database as db, human_review_migration as migration
from tests.test_human_review import human_db as _human_db_fixture, seed_run, event

human_db = _human_db_fixture


@pytest.fixture
def old_review_db(human_db, monkeypatch):
    # Create a genuine v2.5 database in a new isolated path, with an old event.
    path = human_db.parent / "old.sqlite3"
    db.settings.database_path = path
    registered = db._registered_schema_migrations
    with monkeypatch.context() as scope:
        scope.setattr(db, "_registered_schema_migrations", lambda: tuple(m for m in registered() if m.version < migration.VERSION))
        db.init_db()
    seed_run()
    event("human-task", "run-1")
    with db.get_connection() as c:
        before = dict(c.execute("SELECT * FROM clip_feedback").fetchone())
    return path, before


def test_review_migration_preserves_old_feedback_and_restorable_backup(old_review_db):
    path, before = old_review_db
    db.init_db()
    db.init_db()
    with db.get_connection() as c:
        row = dict(c.execute("SELECT * FROM clip_feedback").fetchone())
        assert row.pop("analysis_candidate_key") is None
        assert row.pop("observation_sha256") is None
        assert row == before
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    backups = list((path.parent/"backups").glob("*human-review-v1*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as original, sqlite3.connect(path.parent/"restored.sqlite3") as restored:
        original.backup(restored)
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert "analysis_candidate_key" not in {r[1] for r in restored.execute("PRAGMA table_info(clip_feedback)")}


def test_review_migration_failed_ddl_rolls_back_and_concurrent_retry(old_review_db, monkeypatch):
    with monkeypatch.context() as scope:
        scope.setattr(migration, "STATEMENTS", (migration.STATEMENTS[0], "SELECT missing FROM missing_table"))
        with pytest.raises(db.SchemaMigrationError):
            db.init_db()
    with db.get_connection() as c:
        assert "analysis_candidate_key" not in {r[1] for r in c.execute("PRAGMA table_info(clip_feedback)")}
        assert not c.execute("SELECT 1 FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: db.init_db(), range(2)))
    with db.get_connection() as c:
        migration.verify(c)
        assert c.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()[0] == 1
