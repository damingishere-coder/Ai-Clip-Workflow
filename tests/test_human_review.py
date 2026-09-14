from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.db import database as db
from app.main import app
from app.models.human_review import ObservationDecision
from app.services import human_review_service as review
from app.services.clip_feedback_service import list_recent_feedback_context
from app.services.review_observation_service import build_observations, read_observations, existing_source_identity


@pytest.fixture
def human_db(monkeypatch, tmp_path):
    path = tmp_path / "data" / "review.sqlite3"
    monkeypatch.setattr(db, "settings", SimpleNamespace(data_dir=path.parent, database_path=path,
        tasks_dir=tmp_path/"tasks", publish_default_mode="local_browser"))
    db.init_db()
    return path


def seed_run(task="human-task", run="run-1", *, number=1, profile="variety_comedy", source=None, incomplete=False, count=2):
    created = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    clips = [{"clip_id": f"clip_{i:03d}", "title": f"初始标题 {i}", "summary": "初始内容",
              "start_time": f"00:{i:02d}:00", "end_time": f"00:{i+1:02d}:00", "quality_tier": "A" if i == 1 else "B",
              "quality_score": 90 if i == 1 else 70, "selected_by_default": i == 1} for i in range(1, count + 1)]
    diagnostics = [{"source_id": "c0", "title": "C级诊断", "summary": "未入池",
                    "start_time": "00:20:00", "end_time": "00:21:00", "quality_tier": "C", "quality_score": 50}]
    payload = {"clips": clips, "analysis_meta": {"selection_profile": profile, "analysis_incomplete": incomplete,
        "review_observations": build_observations(clips, scored=diagnostics, profile_id=profile),
        "review_source_identity": {"kind": "original_video_sha256", "sha256": source} if source else {"kind": "unknown"}}}
    with db.get_connection() as c:
        c.execute("INSERT OR IGNORE INTO tasks(id,task_name,task_dir_name,selection_profile,created_at,updated_at) VALUES(?,?,?,'knowledge_opinion',?,?)", (task, task, task, created, created))
        c.execute("INSERT INTO ai_analysis_runs(id,task_id,run_number,provider,provider_label,model,requested_clip_count,clip_count,analysis_payload_json,created_at) VALUES(?,?,?,'test','test','test',?,?,?,?)",
                  (run, task, number, count, count, json.dumps(payload), created))
        c.commit()
    return payload


def decide(task, run, key="clip:clip_001", decision="keep", reason="worth_publishing"):
    rows = review.get_review_observations(task, run)["observations"]
    row = next(r for r in rows if r["key"] == key)
    return review.save_observation_decision(task, run, ObservationDecision(observation_key=key,
        observation_sha256=row["sha256"], decision=decision, reason_code=reason))


def event(task, run, *, source="review_toggle", decision="keep", candidate="clip_001", **extra):
    values = dict(id=f"event-{source}-{datetime.now().timestamp()}", task_id=task, clip_candidate_id=f"{task}_{candidate}",
                  analysis_run_id=run, selection_profile="knowledge_opinion", decision=decision,
                  reason_code="worth_publishing" if decision == "keep" else "other", decision_source=source,
                  note="", title_snapshot="初始标题 1", summary_snapshot="初始内容", start_time="00:01:00", end_time="00:02:00",
                  created_at=datetime.now(timezone.utc).isoformat())
    values.update(extra)
    with db.get_connection() as c:
        c.execute(f"INSERT INTO clip_feedback({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))
        c.commit()


def test_observations_keep_c_unknowns_and_integrity_without_private_transcript():
    data = build_observations([{"clip_id": "clip_001"}], scored=[{"source_id": "c", "tier": "C", "score": 0, "transcript": "PRIVATE"}])
    assert "PRIVATE" not in json.dumps(data)
    assert data["items"][0]["initial_recommended"] is None
    assert data["items"][0]["quality_score"] is None
    assert data["items"][1]["quality_score"] == 0
    payload = {"analysis_meta": {"review_observations": data}}
    assert len(read_observations(payload)[0]) == 2
    data["items"][0]["title"] = "tampered"
    assert len(read_observations(payload)[0]) == 1
    assert existing_source_identity({})["kind"] == "unknown"
    visual = {"visual_signal": {"candidates": {"c": {"status": "partial", "source_sha256": "a" * 64}}}}
    assert existing_source_identity(visual)["sha256"] == "a" * 64


def test_legacy_shared_c_observations_are_read_without_backfilling_or_duplicate_pool():
    payload = {"clips": [{"clip_id": "clip_001", "quality_tier": "A", "quality_evidence": {"source_id": "a"}}],
               "analysis_meta": {"schema_version": 2, "selection_profile": "interview_story", "observations": [
                   {"source_id": "a", "start_seconds": 0, "end_seconds": 60, "tier": "A", "score": 90},
                   {"source_id": "c", "start_seconds": 60, "end_seconds": 120, "tier": "C", "score": 50},
                   {"source_id": "rejected_expansion", "rejection_reason": "无法展开"}]}}
    before = json.dumps(payload)
    rows, basis = read_observations(payload)
    assert basis == "legacy_payload" and [r["key"] for r in rows] == ["clip:clip_001", "source:c"]
    assert rows[1]["quality_tier"] == "C" and not rows[1]["in_review_pool"]
    assert json.dumps(payload) == before


def test_incomplete_or_unknown_extra_decision_cannot_inherit_clean_subset_confidence():
    clean = [dict(human_decision="keep", initial_recommended=True, source_sha256=str(i % 3), incomplete=False,
                  profile="general", in_review_pool=True, ambiguous=False, task_id=str(i % 3), run_id=str(i), decision_reason="worth_publishing") for i in range(20)]
    assert review._summary(clean)["confidence"] == "exploratory"
    mixed = clean + [{**clean[0], "human_decision": "reject", "incomplete": True}]
    assert review._summary(mixed)["confidence"] == "insufficient"
    assert review._summary(mixed)["recommendation_confidence_label"] == "数据不足"
    unknown = clean + [{**clean[0], "source_sha256": None}]
    assert review._summary(unknown)["confidence"] == "insufficient"


def test_initial_default_and_auto_review_bits_do_not_mean_manual_acceptance(human_db):
    seed_run()
    event("human-task", "run-1")
    report = review.get_human_review_summary()
    s = report["summary"]
    assert s["recommended"] == 1 and s["reviewed"] == 0 and s["ambiguous"] == 1
    assert s["recommendation_acceptance_rate"] is None and s["review_coverage"] == 0
    assert s["diagnostic_count"] == 1 and s["rejected"] == 0
    assert report["groups"]["profile"][0]["key"] == "variety_comedy"  # Not current task's profile.
    assert report["profile_version_unknown"] == 3


def test_explicit_last_decision_cutoff_and_reused_candidate_ids(human_db):
    seed_run()
    before = datetime.now(timezone.utc) - timedelta(seconds=30)
    event("human-task", "run-1", source="explicit_feedback", created_at=(before - timedelta(seconds=1)).isoformat())
    event("human-task", "run-1", source="explicit_feedback", decision="reject", created_at=(before + timedelta(seconds=1)).isoformat())
    seed_run(run="run-2", number=2)
    assert review.get_human_review_summary(cutoff=before.isoformat())["summary"]["accepted"] == 1
    s = review.get_human_review_summary()["summary"]
    assert s["rejected"] == 1 and s["accepted"] == 0 and s["unreviewed"] == 5
    assert review.get_review_observations("human-task", "run-2")["observations"][0]["human_decision"] is None
    event("human-task", "run-1", source="explicit_feedback", title_snapshot="人工已改标题")
    assert review.get_human_review_summary()["summary"]["reviewed"] == 0


def test_diagnostic_decision_idempotency_and_no_production_or_prompt_change(human_db):
    seed_run()
    with db.get_connection() as c:
        before = {name: [tuple(r) for r in c.execute(f"SELECT * FROM {name}")] for name in
                  ("tasks", "clip_candidates", "ai_analysis_runs", "workflow_jobs", "publish_jobs", "content_profiles", "ai_prompt_presets")}
    first = decide("human-task", "run-1", "source:c0", "reject", "low_value")
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: decide("human-task", "run-1", "source:c0", "reject", "low_value"), range(3)))
    assert all(r["id"] == first["id"] and r["deduplicated"] for r in results)
    with db.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM clip_feedback").fetchone()[0] == 1
        assert before == {name: [tuple(r) for r in c.execute(f"SELECT * FROM {name}")] for name in before}
    assert list_recent_feedback_context("variety_comedy") == []
    s = review.get_human_review_summary()["summary"]
    assert s["rejected"] == 1 and s["reviewed_recommended"] == 0 and s["confidence"] == "insufficient"


def test_reconfirmation_after_legacy_explicit_change_is_not_swallowed(human_db):
    seed_run()
    first = decide("human-task", "run-1")
    event("human-task", "run-1", source="explicit_feedback", decision="reject")
    assert review.get_human_review_summary()["summary"]["rejected"] == 1
    again = decide("human-task", "run-1")
    assert again["id"] != first["id"] and not again["deduplicated"]
    assert review.get_human_review_summary()["summary"]["accepted"] == 1


def test_unattributed_and_corrupt_evidence_stays_unknown(human_db):
    payload = seed_run()
    event("human-task", "run-1", analysis_run_id=None)
    assert review.get_human_review_summary()["recent_unattributed_feedback"] == 1
    payload["analysis_meta"]["review_observations"]["items"][0]["quality_score"] = 99
    with db.get_connection() as c:
        c.execute("UPDATE ai_analysis_runs SET analysis_payload_json=? WHERE id='run-1'", (json.dumps(payload),))
        c.commit()
    report = review.get_human_review_summary()
    assert report["invalid_runs"] == 1 and report["summary"]["reviewed"] == 0
    assert report["summary"]["confidence"] == "insufficient"


def test_new_diagnostic_reason_cannot_break_existing_selection_form(human_db):
    from app.services import task_service
    from app.services.clip_feedback_service import record_review_toggle_feedback_with_connection
    seed_run()
    event("human-task", "run-1", decision="reject", reason_code="not_funny")
    with db.get_connection() as c:
        c.execute("""INSERT INTO clip_candidates(id,task_id,clip_key,title,start_time,end_time,duration_seconds,summary,enabled,created_at,updated_at)
            VALUES('human-task_clip_001','human-task','clip_001','初始标题 1','00:01:00','00:02:00',60,'初始内容',0,'before','before')""")
        c.commit()
    decide("human-task", "run-1", decision="reject", reason="low_value")
    clip = task_service.list_clip_candidates("human-task")[0]
    assert clip["feedback_reason_code"] == "not_funny"
    with db.get_connection() as c:
        assert not record_review_toggle_feedback_with_connection(c, task_id="human-task", clip=clip,
            selection_profile="variety_comedy", enabled=False, reason_code="not_funny", now=datetime.now(timezone.utc).isoformat())
    assert all(row["reason_code"] != "low_value" for row in list_recent_feedback_context("variety_comedy"))


def test_unknown_or_duplicate_sources_and_incomplete_runs_cannot_meet_threshold(human_db):
    for i in range(3):
        task, run = f"task-{i}", f"run-{i}"
        seed_run(task, run, source="a" * 64, count=7)
        for j in range(1, 8):
            decide(task, run, f"clip:clip_{j:03d}")
    s = review.get_human_review_summary()["summary"]
    assert s["reviewed"] == 21 and s["task_count"] == 3 and s["verified_original_count"] == 1
    assert s["confidence"] == "insufficient"
    with db.get_connection() as c:
        for i in (1, 2):
            payload = json.loads(c.execute("SELECT analysis_payload_json FROM ai_analysis_runs WHERE id=?", (f"run-{i}",)).fetchone()[0])
            payload["analysis_meta"]["review_source_identity"]["sha256"] = str(i) * 64
            c.execute("UPDATE ai_analysis_runs SET analysis_payload_json=? WHERE id=?", (json.dumps(payload), f"run-{i}"))
        c.commit()
    assert review.get_human_review_summary()["summary"]["confidence"] == "exploratory"
    with db.get_connection() as c:
        payload["analysis_meta"]["analysis_incomplete"] = True
        c.execute("UPDATE ai_analysis_runs SET analysis_payload_json=? WHERE id='run-2'", (json.dumps(payload),))
        c.commit()
    assert review.get_human_review_summary()["summary"]["confidence"] == "insufficient"


def test_api_unknown_account_and_hash_validation(human_db):
    seed_run()
    client = TestClient(app)
    response = client.get("/api/content-review/human-review")
    assert response.status_code == 200 and response.json()["summary"]["reviewed"] == 0
    data = client.get("/api/tasks/human-task/ai-analysis-runs/run-1/review-observations").json()
    row = data["observations"][0]
    payload = {"observation_key": row["key"], "observation_sha256": "f" * 64, "decision": "keep", "reason_code": "worth_publishing"}
    url = "/api/tasks/human-task/ai-analysis-runs/run-1/review-observations"
    assert client.post(url, json=payload).status_code == 409
    payload["observation_sha256"] = row["sha256"]
    payload["decision"] = "reject"
    assert client.post(url, json=payload).status_code == 422
    payload["reason_code"] = "fragmented"
    assert client.post(url, json=payload).status_code == 200
    assert client.get("/api/tasks/wrong-task/ai-analysis-runs/run-1/review-observations").status_code == 404
