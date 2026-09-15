from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.db import database as db, production_review_migration as migration
from tests.test_human_review import human_db as _human_db_fixture

human_db = _human_db_fixture


@pytest.fixture
def old_review_database(human_db, monkeypatch):
    path = human_db.parent/'old-review.sqlite3'
    db.settings.database_path = path
    registered = db._registered_schema_migrations
    with monkeypatch.context() as scope:
        scope.setattr(db, '_registered_schema_migrations', lambda:tuple(m for m in registered() if m.version < migration.VERSION))
        db.init_db()
    with db.get_connection() as c:
        c.execute("INSERT INTO tasks(id,task_name,task_dir_name,selection_profile,created_at,updated_at) VALUES('legacy','旧康熙任务','legacy','variety_comedy','old','old')")
        c.commit()
        before = dict(c.execute("SELECT * FROM tasks WHERE id='legacy'").fetchone())
    return path, before


def test_production_review_upgrade_preserves_old_tasks_and_restore_point(old_review_database):
    path,before = old_review_database
    db.init_db()
    db.init_db()
    with db.get_connection() as c:
        assert dict(c.execute("SELECT * FROM tasks WHERE id='legacy'").fetchone()) == before
        migration.verify(c)
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert c.execute('SELECT count(*) FROM production_reviews').fetchone()[0] == 0
    backups = list((path.parent/'backups').glob('*production-review-v1*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as original, sqlite3.connect(path.parent/'restored.sqlite3') as restored:
        original.backup(restored)
        assert not restored.execute("SELECT 1 FROM sqlite_master WHERE name='production_reviews'").fetchone()
        assert restored.execute("SELECT task_name FROM tasks WHERE id='legacy'").fetchone()[0] == '旧康熙任务'


def test_production_review_failed_migration_is_atomic_then_concurrent_retry_once(old_review_database, monkeypatch):
    original_apply = migration.apply
    def fail(c):
        original_apply(c)
        raise RuntimeError('review-injected-failure')
    with monkeypatch.context() as scope:
        scope.setattr(migration, 'apply', fail)
        with pytest.raises(db.SchemaMigrationError, match='review-injected-failure'):
            db.init_db()
    with db.get_connection() as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='production_reviews'").fetchone()
        assert not c.execute('SELECT 1 FROM schema_migrations WHERE version=?',(migration.VERSION,)).fetchone()
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _:db.init_db(),range(3)))
    with db.get_connection() as c:
        assert c.execute('SELECT count(*) FROM schema_migrations WHERE version=?',(migration.VERSION,)).fetchone()[0] == 1
        migration.verify(c)
