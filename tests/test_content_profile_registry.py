"""Legacy database upgrade, immutable policy evidence, and real service paths."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db import database as db, content_profile_migration as migration
from app.models.task import TaskCreate
from app.services import content_profile_service as profiles, ai_prompt_preset_service as prompts
from app.services import task_lifecycle_service as lifecycle, ai_analysis_workflow_service as workflow
from app.services import job_service, weekly_review_service


@pytest.fixture
def old_database(monkeypatch, tmp_path):
    path = tmp_path / "data" / "old.sqlite3"
    monkeypatch.setattr(db, "settings", SimpleNamespace(
        data_dir=path.parent, database_path=path, tasks_dir=tmp_path / "tasks", publish_default_mode="local_browser",
    ))
    registered = db._registered_schema_migrations
    with monkeypatch.context() as scoped:
        scoped.setattr(db, "_registered_schema_migrations", lambda: tuple(m for m in registered() if m.version < migration.VERSION))
        db.init_db()
    with db.get_connection() as connection:
        connection.execute("INSERT INTO tasks(id,task_name,task_dir_name,selection_profile,ai_prompt_preset_id,created_at,updated_at) VALUES('old','历史','old','variety_comedy','preset_001','before','before')")
        weekly_review_service.freeze_task(connection, "old")
        connection.execute("INSERT INTO ai_analysis_runs(id,task_id,run_number,provider,provider_label,model,analysis_payload_json,created_at) VALUES('old-run','old',1,'codex','Codex','old-model','{}','before')")
        connection.execute("INSERT INTO workflow_jobs(id,task_id,job_type,status,payload_json,checkpoint_json,created_at,updated_at) VALUES('old-job','old','ai_analysis','failed','{\"provider\":\"codex\"}','{\"existing_success\":true}','before','before')")
        connection.commit()
    return path


def legacy_facts(connection):
    return {table: [dict(r) for r in connection.execute(f"SELECT * FROM {table}")] for table in (
        "tasks", "ai_prompt_presets", "ai_prompt_versions", "task_generation_rules", "ai_analysis_runs", "workflow_jobs",
    )}


def test_upgrade_preserves_all_old_facts_and_backs_up_once(old_database):
    with db.get_connection() as connection:
        before = legacy_facts(connection)
    db.init_db()
    db.init_db()
    with db.get_connection() as connection:
        after = legacy_facts(connection)
        for table, rows in before.items():
            old_ids = {row["id"] for row in rows} if rows and "id" in rows[0] else None
            current_rows = [row for row in after[table] if old_ids is None or row["id"] in old_ids]
            projected = [{key: row[key] for key in rows[0]} for row in current_rows] if rows else current_rows
            assert projected == rows, (table, projected, rows)
        assert profiles.read_task_profile(connection, "old") == {}
        assert connection.execute("SELECT content_profile_version_id FROM ai_analysis_runs WHERE id='old-run'").fetchone()[0] is None
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    backups = list((old_database.parent / "backups").glob("*content-profiles-v1*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not backup.execute("SELECT 1 FROM sqlite_master WHERE name='content_profiles'").fetchone()


def test_mid_migration_failure_rolls_back_tables_columns_and_ledger(old_database, monkeypatch):
    with monkeypatch.context() as scoped:
        scoped.setattr(migration, "STATEMENTS", (*migration.STATEMENTS[:4], "SELECT injected_failure FROM no_such_table"))
        with pytest.raises(db.SchemaMigrationError, match="no_such_table"):
            db.init_db()
    with db.get_connection() as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='content_profiles'").fetchone()
        assert "content_profile_version_id" not in {r[1] for r in connection.execute("PRAGMA table_info(task_generation_rules)")}
        assert not connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()
        assert connection.execute("SELECT checkpoint_json FROM workflow_jobs WHERE id='old-job'").fetchone()[0] == '{"existing_success":true}'
    db.init_db()
    with db.get_connection() as connection:
        migration.verify(connection)


def test_parallel_upgrade_uses_one_ledger_and_one_set_of_seeds(old_database):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: db.init_db(), range(2)))
    with db.get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM content_profiles").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM content_profile_versions").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=?", (migration.VERSION,)).fetchone()[0] == 1


@pytest.fixture
def new_task():
    task_id = "profile-" + uuid4().hex[:12]
    lifecycle.create_task_record(TaskCreate(task_name="Profile 隔离任务", selection_profile="variety_comedy"), task_id=task_id)
    yield task_id
    with db.get_connection() as connection:
        for table in ("workflow_jobs", "clip_candidates", "ai_analysis_runs", "task_generation_rules"):
            connection.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id,))
        connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        connection.commit()


def test_new_task_freezes_profile_and_prompt_rebinding_does_not_replace_it(new_task):
    before = prompts.get_task_ai_prompt_snapshot(new_task)
    assert before["content_profile_version_id"]
    assert json.loads(before["content_profile_json"])["id"] == "variety_comedy"
    prompts.update_task_ai_prompt_preset(new_task, "preset_002")
    after = prompts.get_task_ai_prompt_snapshot(new_task)
    for key in ("content_profile_version_id", "content_profile_sha256", "content_profile_json"):
        assert after[key] == before[key]
    assert after["id"] == "preset_002"


def test_explicit_profile_change_preserves_prompt_and_active_jobs_block_changes(new_task):
    before = prompts.get_task_ai_prompt_snapshot(new_task)
    lifecycle.update_task_selection_settings(new_task, "long_live_talk", 5, 4, 30)
    after = prompts.get_task_ai_prompt_snapshot(new_task)
    assert after["content_profile_version_id"] != before["content_profile_version_id"]
    assert after["prompt_sha256"] == before["prompt_sha256"]
    assert after["id"] == before["id"]
    job_service.create_job(new_task, job_service.JOB_TYPE_AI_ANALYSIS, {"provider": "codex"})
    with pytest.raises(ValueError, match="后台作业"):
        lifecycle.update_task_selection_settings(new_task, "general", 5)
    assert prompts.get_task_ai_prompt_snapshot(new_task)["content_profile_version_id"] == after["content_profile_version_id"]


def test_immutable_versions_corruption_and_unsupported_rules_fail_closed(new_task):
    snapshot = prompts.get_task_ai_prompt_snapshot(new_task)
    with db.get_connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE content_profile_versions SET rules_version='tampered' WHERE id=?", (snapshot["content_profile_version_id"],))
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM content_profile_versions WHERE id=?", (snapshot["content_profile_version_id"],))
        connection.rollback()
        connection.execute("UPDATE task_generation_rules SET content_profile_sha256='tampered' WHERE task_id=?", (new_task,))
        with pytest.raises(ValueError, match="不一致"):
            profiles.read_task_profile(connection, new_task)
        connection.rollback()
    changed = json.loads(snapshot["content_profile_json"])
    changed["scoring"]["audio_weight"] = .1
    unsupported = profiles.ContentProfile.model_validate(changed)
    with pytest.raises(ValueError, match="尚无匹配"):
        profiles.analyzer_key({"selection_profile": "variety_comedy"}, {
            **snapshot, "content_profile_json": unsupported.canonical_json(), "content_profile_sha256": unsupported.content_hash(),
        })


def test_run_persists_frozen_profile_and_effective_settings_without_rewriting_history(new_task):
    snapshot = prompts.get_task_ai_prompt_snapshot(new_task)
    task = {"selection_profile": "variety_comedy", "candidate_clip_count": 8, "final_clip_target": 3}
    payload = {"clips": [], "analysis_meta": profiles.analysis_profile_evidence(task, snapshot)}
    with db.get_connection() as connection:
        run_id = workflow._insert_ai_analysis_run_with_connection(connection, task_id=new_task,
            analysis_payload=payload, provider="test", provider_label="Test", model="fake", fallback_notice="",
            prompt_preset=snapshot, requested_clip_count=8, now="2026-09-14")
        connection.commit()
    run = workflow.get_ai_analysis_run(new_task, run_id)
    assert run["content_profile_version_id"] == snapshot["content_profile_version_id"]
    assert run["content_profile_sha256"] == snapshot["content_profile_sha256"]
    assert run["analysis_meta"]["effective_selection"]["candidate_clip_count"] == 8
    lifecycle.update_task_selection_settings(new_task, "general", 5)
    assert workflow.get_ai_analysis_run(new_task, run_id)["content_profile_version_id"] == snapshot["content_profile_version_id"]


@pytest.mark.parametrize("profile_id", ["general", "variety_comedy", "long_live_talk"])
def test_registry_legacy_routes_and_snapshot_routes_are_equivalent(profile_id):
    profile = profiles.registered_profile(profile_id)
    task = {"selection_profile": profile_id}
    assert profiles.analyzer_key(task, {}) == profile.analyzer_key
    assert profiles.analyzer_key(task, {"content_profile_json": profile.canonical_json(), "content_profile_sha256": profile.content_hash()}) == profile.analyzer_key
    with pytest.raises(ValueError, match="不支持"):
        profiles.analyzer_key({"selection_profile": "unknown"}, {})


def test_migration_seeds_do_not_follow_runtime_registry(monkeypatch):
    import importlib
    from app.services import content_profile_baselines
    before = migration.CHECKSUM
    monkeypatch.setattr(content_profile_baselines, "legacy_profile_baselines", lambda: pytest.fail("迁移不能读取可变运行配置"))
    importlib.reload(migration)
    assert migration.CHECKSUM == before


def test_new_job_of_legacy_task_gets_run_evidence_without_backfilling_task(old_database):
    db.init_db()
    before = job_service.get_job("old-job")
    job = job_service.create_job("old", job_service.JOB_TYPE_AI_ANALYSIS, {"provider": "codex"})
    snapshot = profiles.read_job_snapshot(job)
    assert snapshot["prompt"]["content_profile_version_id"]
    assert snapshot["selection"]["selection_profile"] == "variety_comedy"
    with db.get_connection() as connection:
        assert profiles.read_task_profile(connection, "old") == {}
    assert profiles.read_job_snapshot(before) is None
    assert job_service.get_job("old-job")["payload_json"] == before["payload_json"]
    assert job_service.get_job("old-job")["checkpoint_json"] == before["checkpoint_json"]


def test_job_snapshot_survives_task_edits_and_corruption_is_not_a_legacy_fallback(new_task):
    job = job_service.create_job(new_task, job_service.JOB_TYPE_AI_ANALYSIS, {"provider": "remote"})
    frozen = profiles.read_job_snapshot(job)
    prompts.update_task_ai_prompt_preset(new_task, "preset_002")
    lifecycle.update_task_candidate_clip_count(new_task, 8)
    assert profiles.read_job_snapshot(job_service.get_job(job["id"])) == frozen
    assert frozen["prompt"]["id"] == "preset_001"
    assert frozen["selection"]["candidate_clip_count"] == 12
    job["payload_json"][profiles.JOB_SNAPSHOT_KEY]["snapshot"]["selection"]["candidate_clip_count"] = 7
    with pytest.raises(ValueError, match="哈希"):
        profiles.read_job_snapshot(job)


def test_job_snapshot_rejects_wrong_profile_version_even_with_valid_envelope_hash(new_task):
    job = job_service.create_job(new_task, job_service.JOB_TYPE_AI_ANALYSIS)
    record = job["payload_json"][profiles.JOB_SNAPSHOT_KEY]
    with db.get_connection() as connection:
        other, _ = profiles.active_profile(connection, "general")
    record["snapshot"]["prompt"]["content_profile_version_id"] = other["id"]
    record["sha256"] = profiles._snapshot_hash(record["snapshot"])
    with pytest.raises(ValueError, match="版本引用"):
        profiles.read_job_snapshot(job)
