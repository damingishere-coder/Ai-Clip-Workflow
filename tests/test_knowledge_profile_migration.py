from types import SimpleNamespace

import pytest

from app.db import database as db, knowledge_profile_migration as migration
from app.services.content_profile_definitions import knowledge_profile


@pytest.fixture
def previous_schema(monkeypatch, tmp_path):
    path = tmp_path / "data/workflow.sqlite3"
    monkeypatch.setattr(db, "settings", SimpleNamespace(data_dir=path.parent, database_path=path,
                       tasks_dir=tmp_path / "tasks", publish_default_mode="local_browser"))
    registered = db._registered_schema_migrations
    with monkeypatch.context() as scoped:
        scoped.setattr(db, "_registered_schema_migrations", lambda: tuple(m for m in registered() if m.version < migration.VERSION))
        db.init_db()
    return path


def test_upgrade_adds_knowledge_and_nullable_provider_evidence(previous_schema):
    with db.get_connection() as c:
        old = [tuple(r) for r in c.execute("SELECT * FROM content_profile_versions ORDER BY id")]
        c.execute("INSERT INTO tasks(id,task_name,selection_profile,created_at,updated_at) VALUES('old','历史','general','before','before')")
        c.execute("INSERT INTO task_generation_rules(task_id,preset_id,prompt_text,frozen_at) VALUES('old','preset_001','历史正文','before')")
        c.commit()
    db.init_db()
    db.init_db()
    with db.get_connection() as c:
        assert old == [tuple(r) for r in c.execute("SELECT * FROM content_profile_versions WHERE profile_id!='knowledge_opinion' ORDER BY id")]
        assert c.execute("SELECT provider_snapshot_json FROM task_generation_rules WHERE task_id='old'").fetchone()[0] is None
        assert c.execute("SELECT config_sha256 FROM content_profile_versions WHERE profile_id='knowledge_opinion'").fetchone()[0] == knowledge_profile().content_hash()


def test_knowledge_collision_rolls_back_new_column(previous_schema):
    with db.get_connection() as c:
        c.execute("INSERT INTO ai_prompt_presets(id,slot,name,prompt_text,created_at,updated_at) VALUES('profile_knowledge_v1',6,'用户','保留','before','before')")
        c.commit()
    with pytest.raises(db.SchemaMigrationError):
        db.init_db()
    with db.get_connection() as c:
        assert "provider_snapshot_json" not in {r[1] for r in c.execute("PRAGMA table_info(task_generation_rules)")}
        assert not c.execute("SELECT 1 FROM content_profiles WHERE id='knowledge_opinion'").fetchone()
        assert not c.execute("SELECT 1 FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()
        assert c.execute("SELECT prompt_text FROM ai_prompt_presets WHERE id='profile_knowledge_v1'").fetchone()[0] == "保留"
