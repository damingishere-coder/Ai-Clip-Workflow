"""Upgrade the actual prior schema; preserve custom Prompt slots and history."""

from types import SimpleNamespace

import pytest

from app.db import database as db, interview_profile_migration as migration
from app.services.content_profile_definitions import interview_profile


@pytest.fixture
def prior_database(monkeypatch, tmp_path):
    path = tmp_path / "data/workflow.sqlite3"
    monkeypatch.setattr(db, "settings", SimpleNamespace(data_dir=path.parent, database_path=path,
                       tasks_dir=tmp_path / "tasks", publish_default_mode="local_browser"))
    registered = db._registered_schema_migrations
    with monkeypatch.context() as scoped:
        scoped.setattr(db, "_registered_schema_migrations", lambda: tuple(m for m in registered() if m.version < migration.VERSION))
        db.init_db()
    with db.get_connection() as c:
        c.execute("INSERT INTO ai_prompt_presets(id,slot,name,prompt_text,created_at,updated_at) VALUES('custom',5,'私人方案','不覆盖','before','before')")
        c.commit()
    return path


def test_additive_seed_preserves_custom_slot_and_old_versions(prior_database):
    with db.get_connection() as c:
        before = [tuple(r) for r in c.execute("SELECT * FROM content_profile_versions ORDER BY id")]
        preset = tuple(c.execute("SELECT * FROM ai_prompt_presets WHERE id='custom'").fetchone())
    db.init_db()
    db.init_db()
    with db.get_connection() as c:
        assert before == [tuple(r) for r in c.execute("SELECT * FROM content_profile_versions WHERE profile_id IN ('general','variety_comedy','long_live_talk') ORDER BY id")]
        assert preset == tuple(c.execute("SELECT * FROM ai_prompt_presets WHERE id='custom'").fetchone())
        assert c.execute("SELECT slot FROM ai_prompt_presets WHERE id='profile_interview_v1'").fetchone()[0] == 6
        assert c.execute("SELECT config_sha256 FROM content_profile_versions WHERE profile_id='interview_story'").fetchone()[0] == interview_profile().content_hash()
        assert c.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()[0] == 1


def test_seed_collision_rolls_back_profile_insert(prior_database):
    with db.get_connection() as c:
        c.execute("INSERT INTO ai_prompt_presets(id,slot,name,prompt_text,created_at,updated_at) VALUES('profile_interview_v1',6,'用户数据','保留','before','before')")
        c.commit()
    with pytest.raises(db.SchemaMigrationError):
        db.init_db()
    with db.get_connection() as c:
        assert not c.execute("SELECT 1 FROM content_profiles WHERE id='interview_story'").fetchone()
        assert not c.execute("SELECT 1 FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()
        assert c.execute("SELECT prompt_text FROM ai_prompt_presets WHERE id='profile_interview_v1'").fetchone()[0] == "保留"
