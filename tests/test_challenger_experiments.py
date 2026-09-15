from datetime import datetime, timedelta, timezone
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import database as db
from app.main import app
from app.models.content_challenger import ChallengerExperimentCreate, PolicyDecision
from app.models.task import TaskCreate
from app.services import challenger_experiment_service as service, content_review_service as review
from app.services import content_intelligence_service as reports, content_challenger_service as drafts
from app.services import content_profile_service as profiles, ai_prompt_preset_service as prompts, job_service
from app.services import task_lifecycle_service as lifecycle, ai_analysis_workflow_service as workflow
from app.services.review_observation_service import build_observations
from tests.test_human_review import human_db as _human_db_fixture
from tests.test_content_challengers import draft_input
from tests.test_challenger_trials import trial_input
from tests.test_content_review import _insert_account
from tests.test_content_intelligence import _snapshot

human_db = _human_db_fixture


def seed_work(draft, account, index, *, trial=False):
    payload = trial_input(draft) if trial else TaskCreate(task_name="隔离官方基线", selection_profile="variety_comedy", ai_prompt_preset_id="preset_001")
    task = lifecycle.create_task_record(payload)["id"]
    job = job_service.create_job(task, job_service.JOB_TYPE_AI_ANALYSIS)
    frozen = profiles.read_job_snapshot(job)
    prompt = frozen["prompt"]
    now = datetime.now(timezone.utc).isoformat()
    published = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    clip = {"clip_id": "clip_001", "title": "合成测试候选", "start_time": "00:00:00", "end_time": "00:01:00",
            "quality_tier": "A", "quality_score": 90, "selected_by_default": True}
    meta = {**profiles.analysis_profile_evidence(frozen["selection"], prompt),
            "workflow_job_id": job["id"], "prompt_version_id": prompt["prompt_version_id"], "prompt_sha256": prompt["prompt_sha256"],
            "provider_identity": frozen["provider_identity"], "feedback_context": frozen["feedback_context"],
            "visual_signal": {"status": "disabled", "policy": frozen["visual_policy"]},
            "review_observations": build_observations([clip], profile_id="variety_comedy"),
            "review_source_identity": {"kind": "original_video_sha256", "sha256": f"{index % 3:064x}"}}
    with db.get_connection() as c:
        run = workflow._insert_ai_analysis_run_with_connection(c, task_id=task, analysis_payload={"clips": [clip], "analysis_meta": meta},
            provider=frozen["provider"], provider_label="fixture", model=frozen["provider_identity"]["fields"]["model"],
            fallback_notice="", prompt_preset=prompt, requested_clip_count=12, now=now)
        candidate, output, publish = (uuid4().hex for _ in range(3))
        c.execute("""INSERT INTO clip_candidates(id,task_id,clip_key,title,start_time,end_time,duration_seconds,source_analysis_run_id,created_at,updated_at)
            VALUES(?,?,'clip_001','候选','00:00:00','00:01:00',60,?,?,?)""", (candidate, task, run, now, now))
        c.execute("""INSERT INTO output_clip(id,task_id,clip_candidate_id,output_file_path,output_file_name,status,
            source_start_ms,source_end_ms,source_duration_ms,snapshot_source,is_active,created_at,updated_at)
            VALUES(?,?,?,'','', 'completed',0,60000,60000,'cut_commit',1,?,?)""", (output, task, candidate, now, now))
        c.execute("""INSERT INTO publish_jobs(id,task_id,output_clip_id,account_id,platform,title,status,published_at,created_at,updated_at)
            VALUES(?,?,?,?,'douyin','隔离作品','DRAFT',?,?,?)""", (publish, task, output, account, published, now, now))
        c.commit()
    for age in (15, 8, 1):
        _snapshot(account, publish, published, days_ago=age, identity=publish, five_second_completion_rate=.7 if trial else .5)
    return {"job": publish, "task": task, "run": run, "output": output, "workflow_job": job["id"]}


def experiment_input(draft, report):
    context = service.experiment_context(draft["id"], report["id"])
    cohort = next(c for c in context["cohorts"] if c["ready"])
    return ChallengerExperimentCreate(report_id=report["id"], report_sha256=report["payload_sha256"],
        challenger_sha256=draft["evidence_sha256"], cohort_sha256=cohort["sha256"], confirm=True)


def seed_experiment(*, treatment=0):
    draft, account = drafts.create_draft(draft_input()), _insert_account()
    baseline = [seed_work(draft, account, i) for i in range(20)]
    report = reports.create_report(account, 30, str(uuid4()))
    payload = experiment_input(draft, report)
    experiment = service.create_experiment(draft["id"], payload)["experiment_id"]
    trials = [seed_work(draft, account, i, trial=True) for i in range(treatment)]
    for work in trials:
        review.assign_publish_job_to_experiment(experiment, work["job"])
    return draft, account, baseline, report, payload, experiment, trials


def test_no_official_baseline_or_unconfirmed_input_cannot_create_experiment(human_db):
    draft = drafts.create_draft(draft_input())
    context = service.experiment_context(draft["id"], draft["report_id"])
    assert context["account_id"] is None and context["cohorts"] == []
    payload = ChallengerExperimentCreate(report_id=draft["report_id"], report_sha256=draft["report_sha256"],
        challenger_sha256=draft["evidence_sha256"], cohort_sha256="0"*64, confirm=True)
    with pytest.raises(ValueError, match="样本不足"):
        service.create_experiment(draft["id"], payload)
    with pytest.raises(ValueError, match="明确确认"):
        service.create_experiment(draft["id"], payload.model_copy(update={"confirm": False}))
    assert TestClient(app).post(f"/api/content-review/challengers/{draft['id']}/experiments", json=payload.model_dump(mode="json")).status_code == 409
    with db.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM content_improvement_experiments").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM content_policy_events").fetchone()[0] == 0


def test_actual_official_baseline_freezes_versions_and_both_assignment_paths_reject_wrong_run(human_db):
    draft, account, baseline, report, payload, experiment, _ = seed_experiment()
    assert service.create_experiment(draft["id"], payload)["experiment_id"] == experiment
    before = review.list_content_experiments(account)[0]
    assert before["progress"]["baseline_count"] == 20 and not before["progress"]["decision_ready"]
    with pytest.raises(ValueError, match="策略不匹配"):
        review.assign_publish_job_to_experiment(experiment, baseline[0]["job"])
    with pytest.raises(ValueError, match="策略不匹配"):
        review.set_publish_job_experiment(baseline[0]["job"], experiment)
    trial = seed_work(draft, account, 21, trial=True)
    review.set_publish_job_experiment(trial["job"], experiment)
    with db.get_connection() as c:
        row = c.execute("SELECT analysis_payload_json FROM ai_analysis_runs WHERE id=?", (trial["run"],)).fetchone()
        value = json.loads(row[0])
        value["analysis_meta"]["effective_selection"]["candidate_clip_count"] = 99
        c.execute("UPDATE ai_analysis_runs SET analysis_payload_json=? WHERE id=?", (json.dumps(value), trial["run"]))
        c.commit()
    with pytest.raises(ValueError, match="实际 Provider"):
        review.assign_publish_job_to_experiment(experiment, trial["job"])
    current = review.list_content_experiments(account)[0]["progress"]
    assert current["treatment_count"] == 0 and len(current["invalid_assignments"]) == 1
    assert reports.get_report(report["id"]) == report
    assert prompts.get_ai_prompt_preset("preset_001")["prompt_text"].strip() == draft["evidence"]["baseline"]["prompt_text"]


def test_twenty_twenty_three_week_human_decision_then_separate_activation_and_safe_rollback(human_db):
    draft, account, baseline, _, _, experiment, trials = seed_experiment(treatment=20)
    old_task = prompts.get_task_ai_prompt_snapshot(baseline[0]["task"])
    before = prompts.get_ai_prompt_preset("preset_001")["prompt_text"]
    with db.get_connection() as c:
        c.execute("UPDATE output_clip SET is_active=0 WHERE id=?", (trials[0]["output"],))
        c.commit()  # Published old cut evidence remains valid after later recut.
    progress = review.list_content_experiments(account)[0]["progress"]
    assert progress["decision_ready"] and progress["treatment_count"] == 20 and progress["official_export_weeks"] >= 3
    assert progress["primary_delta"] == .2
    with pytest.raises(ValueError, match="结论证据不足"):
        service.policy_preview(experiment, "activate")
    review.update_content_experiment(experiment, "keep")
    assert prompts.get_ai_prompt_preset("preset_001")["prompt_text"] == before
    preview = service.policy_preview(experiment, "activate")
    payload = PolicyDecision(action="activate", preview_sha256=preview["sha256"], confirm=True, request_key=uuid4())
    with pytest.raises(ValueError, match="明确确认"):
        service.apply_policy(experiment, payload.model_copy(update={"confirm": False}))
    event = service.apply_policy(experiment, payload)
    assert service.apply_policy(experiment, payload) == event
    assert prompts.get_ai_prompt_preset("preset_001")["prompt_text"] == draft["evidence"]["challenger_prompt_text"]
    unchanged = prompts.get_task_ai_prompt_snapshot(baseline[0]["task"])
    assert {k: v for k, v in unchanged.items() if k != "updated_at"} == {k: v for k, v in old_task.items() if k != "updated_at"}
    new_task = lifecycle.create_task_record(TaskCreate(task_name="新正式任务", selection_profile="variety_comedy"))["id"]
    assert prompts.get_task_ai_prompt_snapshot(new_task)["prompt_text"] == draft["evidence"]["challenger_prompt_text"]
    reverse = service.policy_preview(experiment, "rollback")
    rollback = PolicyDecision(action="rollback", preview_sha256=reverse["sha256"], confirm=True, request_key=uuid4())
    service.apply_policy(experiment, rollback)
    assert prompts.get_ai_prompt_preset("preset_001")["prompt_text"] == before
    assert prompts.get_task_ai_prompt_snapshot(new_task)["prompt_text"] == draft["evidence"]["challenger_prompt_text"]
    assert service.apply_policy(experiment, payload) == event  # Late retry does not reactivate.
    assert len(service.policy_events(experiment)) == 2
    assert review.list_content_experiments(account)[0]["progress"] == progress or review.list_content_experiments(account)[0]["progress"]["works"] == progress["works"]


def test_missing_metrics_source_and_execution_evidence_do_not_count_as_trusted_baseline(human_db):
    draft, account = drafts.create_draft(draft_input()), _insert_account()
    works = [seed_work(draft, account, i) for i in range(20)]
    with db.get_connection() as c:
        c.execute("UPDATE douyin_item_metric_snapshots SET five_second_completion_rate=NULL WHERE publish_job_id=?", (works[0]["job"],))
        c.commit()
    saved = reports.create_report(account, 30, str(uuid4()))
    cohorts = service.experiment_context(draft["id"], saved["id"])["cohorts"]
    assert len(cohorts) == 1 and not cohorts[0]["ready"] and cohorts[0]["metrics"][service.PRIMARY]["missing"] == 1
    with db.get_connection() as c:
        row = c.execute("SELECT analysis_payload_json FROM ai_analysis_runs WHERE id=?", (works[1]["run"],)).fetchone()
        value = json.loads(row[0])
        value["analysis_meta"].pop("provider_identity")
        c.execute("UPDATE ai_analysis_runs SET analysis_payload_json=? WHERE id=?", (json.dumps(value), works[1]["run"]))
        c.commit()
    updated = reports.create_report(account, 30, str(uuid4()))
    cohorts = service.experiment_context(draft["id"], updated["id"])["cohorts"]
    assert cohorts[0]["work_count"] == 19 and not cohorts[0]["ready"]


def test_explicit_enable_conflicts_preserve_manual_edits_and_decision_evidence(human_db):
    _, account, _, _, _, experiment, _ = seed_experiment(treatment=20)
    review.update_content_experiment(experiment, "keep")
    frozen_progress = review.list_content_experiments(account)[0]["progress"]
    preview = service.policy_preview(experiment, "activate")
    payload = PolicyDecision(action="activate", preview_sha256=preview["sha256"], confirm=True, request_key=uuid4())
    with db.get_connection() as c:
        c.execute("UPDATE ai_prompt_presets SET prompt_text='用户后来编辑的正文' WHERE id='preset_001'")
        c.execute("UPDATE douyin_item_metric_snapshots SET five_second_completion_rate=.1")
        c.commit()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute("UPDATE content_improvement_experiments SET decision='revert' WHERE id=?", (experiment,))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute("UPDATE content_improvement_experiments SET minimum_weeks=1 WHERE id=?", (experiment,))
    assert review.list_content_experiments(account)[0]["progress"] == frozen_progress
    response = TestClient(app).post(f"/api/content-review/experiments/{experiment}/policy-events", json=payload.model_dump(mode="json"))
    assert response.status_code == 409 and "不能覆盖" in response.json()["detail"]
    assert prompts.get_ai_prompt_preset("preset_001")["prompt_text"] == "用户后来编辑的正文"
    with db.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM content_policy_events").fetchone()[0] == 0


def test_latest_unmatched_and_same_batch_duplicates_do_not_help_trial_reach_threshold(human_db):
    _, account, _, _, _, experiment, trials = seed_experiment(treatment=20)
    with db.get_connection() as c:
        row = dict(c.execute("SELECT * FROM douyin_item_metric_snapshots WHERE publish_job_id=? ORDER BY captured_at DESC LIMIT 1", (trials[0]["job"],)).fetchone())
    _snapshot(account, trials[0]["job"], row["published_at"], identity="duplicate", batch=row["batch_id"])
    _snapshot(account, None, row["published_at"], days_ago=0, identity=trials[1]["job"], match_status="unmatched")
    progress = review.list_content_experiments(account)[0]["progress"]
    assert progress["treatment_count"] == 18 and not progress["decision_ready"]
    with pytest.raises(ValueError, match="样本"):
        review.update_content_experiment(experiment, "keep")
    assert review.update_content_experiment(experiment, "cancel")["status"] == "cancelled"
    with pytest.raises(ValueError):
        service.policy_preview(experiment, "activate")


@pytest.mark.parametrize("change", ["bounds", "incomplete"])
def test_assignment_rejects_changed_boundaries_or_incomplete_run_before_publish(human_db, change):
    draft, account, _, _, _, experiment, _ = seed_experiment()
    work = seed_work(draft, account, 21, trial=True)
    with db.get_connection() as c:
        if change == "bounds":
            c.execute("UPDATE output_clip SET source_end_ms=59000,source_duration_ms=59000 WHERE id=?", (work["output"],))
        else:
            value = json.loads(c.execute("SELECT analysis_payload_json FROM ai_analysis_runs WHERE id=?", (work["run"],)).fetchone()[0])
            value["analysis_meta"]["analysis_incomplete"] = True
            c.execute("UPDATE ai_analysis_runs SET analysis_payload_json=? WHERE id=?", (json.dumps(value), work["run"]))
        c.commit()
    message = "边界不一致" if change == "bounds" else "不完整"
    for assign in (lambda: review.assign_publish_job_to_experiment(experiment, work["job"]),
                   lambda: review.set_publish_job_experiment(work["job"], experiment)):
        with pytest.raises(ValueError, match=message):
            assign()
    with db.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM content_improvement_experiment_items").fetchone()[0] == 0
