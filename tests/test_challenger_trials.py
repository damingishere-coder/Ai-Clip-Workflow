import copy
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import database as db
from app.main import app
from app.models.task import TaskCreate, TaskStatus, AIPromptPresetUpdate
from app.services import ai_prompt_preset_service as prompts, content_profile_service as profiles
from app.services import content_challenger_service as drafts, job_service, task_lifecycle_service as lifecycle
from app.services import ai_analysis_workflow_service as workflow
from tests.test_content_challengers import draft_input
from tests.test_human_review import human_db as _human_db_fixture

human_db = _human_db_fixture


def trial_input(draft):
    return TaskCreate(task_name="隔离 Challenger 试验", selection_profile="variety_comedy",
        ai_prompt_preset_id=draft["challenger_preset_id"], challenger_id=draft["id"],
        challenger_sha256=draft["evidence_sha256"], confirm_challenger=True)


def create_trial():
    draft = drafts.create_draft(draft_input())
    task = lifecycle.create_task_record(trial_input(draft))
    return draft, task["id"]


def test_explicit_trial_binds_archived_prompt_and_run_without_changing_champion(human_db):
    with db.get_connection() as c:
        champion = tuple(c.execute("SELECT * FROM ai_prompt_presets WHERE id='preset_001'").fetchone())
    draft, task = create_trial()
    snapshot = prompts.get_task_ai_prompt_snapshot(task)
    assert snapshot["challenger"] == {"id": draft["id"], "sha256": draft["evidence_sha256"]}
    assert snapshot["prompt_version_id"] == draft["challenger_prompt_version_id"]
    job = job_service.create_job(task, job_service.JOB_TYPE_AI_ANALYSIS)
    frozen = profiles.read_job_snapshot(job)
    assert frozen["prompt"] == snapshot
    with db.get_connection() as c:
        payload = {"clips": [], "analysis_meta": profiles.analysis_profile_evidence(frozen["selection"], snapshot)}
        run = workflow._insert_ai_analysis_run_with_connection(c, task_id=task, analysis_payload=payload,
            provider="test", provider_label="Test", model="fake", fallback_notice="", prompt_preset=snapshot,
            requested_clip_count=12, now="2026-09-15")
        c.commit()
        assert tuple(c.execute("SELECT * FROM ai_prompt_presets WHERE id='preset_001'").fetchone()) == champion
        assert c.execute("SELECT is_archived,is_default FROM ai_prompt_presets WHERE id=?", (draft["challenger_preset_id"],)).fetchone()[:] == (1, 0)
        assert c.execute("SELECT COUNT(*) FROM publish_jobs").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM content_improvement_experiments").fetchone()[0] == 0
    assert workflow.get_ai_analysis_run(task, run)["analysis_meta"]["challenger"] == snapshot["challenger"]
    assert draft["challenger_preset_id"] not in {p["id"] for p in prompts.list_ai_prompt_presets()}


@pytest.mark.parametrize("changes,message", [
    ({"confirm_challenger": False}, "明确确认"), ({"challenger_sha256": "0" * 64}, "预览证据"),
    ({"selection_profile": "general"}, "Content Profile"), ({"ai_prompt_preset_id": "preset_001"}, "冻结的 Prompt"),
    ({"auto_mode": True}, "全自动"), ({"challenger_id": None, "challenger_sha256": None, "confirm_challenger": False}, "已归档"),
])
def test_trial_admission_rejects_unconfirmed_wrong_strategy_and_automatic_tasks(human_db, changes, message):
    draft = drafts.create_draft(draft_input())
    with pytest.raises(ValueError, match=message):
        lifecycle.create_task_record(trial_input(draft).model_copy(update=changes))
    with db.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == 0
    assert drafts.get_challenger(draft["id"])["status"] == "draft"


def test_trial_cannot_rebind_or_hide_marker_and_retry_keeps_original_snapshot(human_db):
    draft, task = create_trial()
    with pytest.raises(ValueError, match="已冻结"):
        prompts.update_task_ai_prompt_preset(task, "preset_001")
    with pytest.raises(ValueError, match="已冻结"):
        lifecycle.update_task_selection_settings(task, "general", 5)
    lifecycle.update_task_selection_settings(task, "variety_comedy", 3)
    with pytest.raises(ValueError, match="全自动"):
        job_service.create_job(task, job_service.JOB_TYPE_AUTO_PIPELINE)
    job = job_service.create_job(task, job_service.JOB_TYPE_AI_ANALYSIS)
    before = profiles.read_job_snapshot(job)
    lifecycle.update_task_candidate_clip_count(task, 8)
    assert profiles.read_job_snapshot(job_service.get_job(job["id"])) == before
    changed = copy.deepcopy(job)
    changed["payload_json"] = {}
    with pytest.raises(ValueError, match="缺少策略快照"):
        profiles.read_job_snapshot(changed)
    changed = copy.deepcopy(job)
    record = changed["payload_json"][profiles.JOB_SNAPSHOT_KEY]
    del record["snapshot"]["prompt"]["challenger"]
    record["sha256"] = profiles._snapshot_hash(record["snapshot"])
    with pytest.raises(ValueError, match="证据缺失"):
        profiles.read_job_snapshot(changed)
    with db.get_connection() as c:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute("UPDATE task_generation_rules SET challenger_id=NULL,challenger_sha256=NULL WHERE task_id=?", (task,))
        c.execute("UPDATE task_generation_rules SET prompt_text='tampered' WHERE task_id=?", (task,))
        c.commit()
    with pytest.raises(ValueError, match="实际 Prompt/Profile"):
        prompts.get_task_ai_prompt_snapshot(task)
    assert profiles.read_job_snapshot(job_service.get_job(job["id"])) == before


def test_trial_pages_keep_one_readonly_prompt_and_legacy_creation_stays_normal(human_db):
    draft, task = create_trial()
    client = TestClient(app)
    ordinary = client.get("/tasks/new").text
    assert draft["challenger_preset_id"] not in ordinary
    page = client.get("/tasks/new", params={"challenger_id": draft["id"]})
    assert page.status_code == 200 and 'name="confirm_challenger"' in page.text
    assert 'name="auto_mode"' not in page.text
    detail = client.get(f"/tasks/{task}")
    assert detail.status_code == 200 and 'data-challenger-id=' in detail.text
    assert detail.text.count('name="ai_prompt_preset_id"') == 1
    assert 'readonly' in detail.text and '试验不会自动修改正式方案' in detail.text
    response = client.patch(f"/api/tasks/{task}/ai-prompt-preset", json={"ai_prompt_preset_id": "preset_001"})
    assert response.status_code == 409 and "已冻结" in response.json()["detail"]
    ordinary_task = lifecycle.create_task_record(TaskCreate(task_name="普通任务", selection_profile="general"))["id"]
    job = job_service.create_job(ordinary_task, job_service.JOB_TYPE_AI_ANALYSIS)
    assert "challenger" not in profiles.read_job_snapshot(job)["prompt"]
    with db.get_connection() as c:
        row = c.execute("SELECT challenger_id,challenger_sha256 FROM task_generation_rules WHERE task_id=?", (ordinary_task,)).fetchone()
        assert row[:] == (None, None)
    assert json.dumps(profiles.read_job_snapshot(job))


def test_changed_champion_blocks_new_trial_but_existing_task_keeps_its_versions(human_db):
    draft, task = create_trial()
    before = prompts.get_task_ai_prompt_snapshot(task)
    prompts.update_ai_prompt_preset("preset_001", AIPromptPresetUpdate(name="人工改动", prompt_text="全新正式规则"))
    with pytest.raises(ValueError, match="基线已过期"):
        lifecycle.create_task_record(trial_input(draft))
    assert prompts.get_task_ai_prompt_snapshot(task) == before
    with db.get_connection() as c:
        version = dict(c.execute("SELECT * FROM content_profile_versions WHERE id=?", (draft["profile_version_id"],)).fetchone())
        # A later head must not rewrite an already-bound trial, even when the
        # current executable does not support that new policy yet.
        version["id"] = "new-head"
        version["version_number"] += 1
        config = profiles.ContentProfile.model_validate_json(version["config_json"])
        config = config.model_copy(update={"description": "后续版本描述"})
        version["config_json"] = config.canonical_json()
        version["config_sha256"] = config.content_hash()
        c.execute(f"INSERT INTO content_profile_versions({','.join(version)}) VALUES({','.join('?' for _ in version)})", tuple(version.values()))
        c.execute("UPDATE content_profiles SET current_version_id='new-head' WHERE id='variety_comedy'")
        c.commit()
    lifecycle.update_task_selection_settings(task, "variety_comedy", 3)
    assert prompts.get_task_ai_prompt_snapshot(task) == before


def test_trial_pipeline_commits_and_reuses_run_but_rejects_corrupt_resume_and_restore(human_db, monkeypatch):
    from tests.test_ai_job_consistency import _analysis
    from app.services import storage_service
    from dataclasses import replace
    monkeypatch.setattr(storage_service, "settings", replace(storage_service.settings, database_path=human_db))
    draft, task = create_trial()
    lifecycle.update_task_status(task, TaskStatus.pending_ai)
    paths = storage_service.get_artifact_paths(task)
    paths["transcript_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["transcript_path"].write_text("00:00:00 - 00:02:00 隔离转写", encoding="utf-8")
    calls = []

    def analyze(task_id, _task, _paths, _provider, prompt):
        calls.append(prompt["challenger"])
        return _analysis(task_id)
    monkeypatch.setattr(workflow, "_analyze_with_provider", analyze)
    job = job_service.create_job(task, job_service.JOB_TYPE_AI_ANALYSIS, {"provider": "remote"})
    claimed = job_service.claim_job(job["id"], "trial-owner")
    with job_service.job_lease_context(job["id"], "trial-owner", claimed["lease_token"]):
        result = workflow.process_task_ai_analysis(task)
        again = workflow.process_task_ai_analysis(task)
        assert result["analysis_run_id"] == again["analysis_run_id"] and len(calls) == 1
        run_id = result["analysis_run_id"]
        assert result["analysis_run"]["challenger"]["id"] == draft["id"]
        with db.get_connection() as c:
            payload = json.loads(c.execute("SELECT analysis_payload_json FROM ai_analysis_runs WHERE id=?", (run_id,)).fetchone()[0])
            payload["analysis_meta"].pop("challenger")
            c.execute("UPDATE ai_analysis_runs SET analysis_payload_json=? WHERE id=?", (json.dumps(payload), run_id))
            c.commit()
        with pytest.raises(ValueError, match="Challenger 标记"):
            workflow.process_task_ai_analysis(task)
    with pytest.raises(ValueError, match="Challenger 标记"):
        workflow.restore_ai_analysis_run(task, run_id)
    assert len(calls) == 1
    with db.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM ai_analysis_runs WHERE task_id=?", (task,)).fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM clip_candidates WHERE task_id=?", (task,)).fetchone()[0] == 1


def test_trial_run_insert_rejects_missing_marker_before_changing_active_run(human_db):
    _, task = create_trial()
    snapshot = prompts.get_task_ai_prompt_snapshot(task)
    with db.get_connection() as c:
        with pytest.raises(ValueError, match="Challenger 标记"):
            workflow._insert_ai_analysis_run_with_connection(c, task_id=task, analysis_payload={"clips": []},
                provider="test", provider_label="Test", model="fake", fallback_notice="", prompt_preset=snapshot,
                requested_clip_count=12, now="2026-09-15")
        assert c.execute("SELECT COUNT(*) FROM ai_analysis_runs WHERE task_id=?", (task,)).fetchone()[0] == 0
