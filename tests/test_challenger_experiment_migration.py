from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.db import database as db, challenger_experiment_migration as migration
from tests.test_human_review import human_db as _human_db_fixture
from tests.test_content_review import _insert_account

human_db = _human_db_fixture


@pytest.fixture
def old_database(human_db, monkeypatch):
    path = human_db.parent / "old-experiment.sqlite3"
    db.settings.database_path = path
    registered = db._registered_schema_migrations
    with monkeypatch.context() as scope:
        scope.setattr(db, "_registered_schema_migrations", lambda: tuple(m for m in registered() if m.version < migration.VERSION))
        db.init_db()
    account = _insert_account()
    with db.get_connection() as c:
        c.execute("""INSERT INTO content_metric_import_batches(id,account_id,source_kind,source_filename,source_sha256,status,created_at)
            VALUES('old-batch',?,'douyin_item_export','fixture.xlsx','fixture','committed','2026-09-01')""", (account,))
        c.execute("""INSERT INTO content_improvement_experiments(id,account_id,recommendation_id,diagnosis_code,title,hypothesis,
            action_text,primary_metric,primary_direction,guardrail_metrics_json,baseline_batch_id,baseline_json,created_at,updated_at)
            VALUES('legacy',?,'legacy','legacy','旧实验','旧假设','旧操作','five_second_completion_rate','higher','[]','old-batch','{}','2026-09-01','2026-09-01')""", (account,))
        c.commit()
        before = dict(c.execute("SELECT * FROM content_improvement_experiments WHERE id='legacy'").fetchone())
    return path, before


def test_experiment_upgrade_preserves_legacy_values_and_backup_restore(old_database):
    path, before = old_database
    db.init_db()
    db.init_db()
    with db.get_connection() as c:
        after = dict(c.execute("SELECT * FROM content_improvement_experiments WHERE id='legacy'").fetchone())
        assert {key: after[key] for key in before} == before
        assert all(after[column.split()[0]] is None for column in migration.COLUMNS)
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        migration.verify(c)
    backups = list((path.parent / "backups").glob("*challenger-experiments-v1*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as original, sqlite3.connect(path.parent / "restore.sqlite3") as restored:
        original.backup(restored)
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert "challenger_id" not in {r[1] for r in restored.execute("PRAGMA table_info(content_improvement_experiments)")}
        assert restored.execute("SELECT title FROM content_improvement_experiments WHERE id='legacy'").fetchone()[0] == "旧实验"


def test_experiment_failed_migration_rolls_back_and_concurrent_retry_is_once(old_database, monkeypatch):
    with monkeypatch.context() as scope:
        scope.setattr(migration, "STATEMENTS", (migration.STATEMENTS[0], "SELECT * FROM missing_table"))
        with pytest.raises(db.SchemaMigrationError):
            db.init_db()
    with db.get_connection() as c:
        assert "challenger_id" not in {r[1] for r in c.execute("PRAGMA table_info(content_improvement_experiments)")}
        assert not c.execute("SELECT 1 FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: db.init_db(), range(2)))
    with db.get_connection() as c:
        migration.verify(c)
        assert c.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()[0] == 1
