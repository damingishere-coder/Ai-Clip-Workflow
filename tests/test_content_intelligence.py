from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.db import database as db
from app.main import app
from app.services import content_intelligence_service as intelligence, content_review_service as old
from app.services.content_profile_service import active_profile
from app.services.review_observation_service import build_observations
from tests.test_human_review import human_db as _human_db_fixture, seed_run, decide
from tests.test_content_review import _insert_account, _insert_publish_job, _attach_prompt_chain

human_db = _human_db_fixture


def _chain(account, *, index=0):
    published = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    job = _insert_publish_job(account, title=f"作品 {index}", published_at=published)
    version = _attach_prompt_chain(job, f"intelligence-{index}")
    clip = {"clip_id": "clip_001", "title": "原始 AI 标题", "start_time": "00:00:00", "end_time": "00:01:00",
            "quality_tier": "A", "quality_score": 90, "topic_key": "原始话题", "quality_evidence": {"hook_type": "反转"}}
    with db.get_connection() as c:
        profile_version, _ = active_profile(c, "variety_comedy")
        payload = {"clips": [clip], "analysis_meta": {"selection_profile": "variety_comedy",
            "review_observations": build_observations([clip], profile_id="variety_comedy"),
            "review_source_identity": {"kind": "original_video_sha256", "sha256": f"{index % 3:064x}"}}}
        c.execute("UPDATE ai_analysis_runs SET analysis_payload_json=?,content_profile_version_id=?,content_profile_sha256=? WHERE prompt_version_id=?",
            (json.dumps(payload), profile_version["id"], profile_version["config_sha256"], version))
        c.execute("UPDATE output_clip SET snapshot_source='cut_commit',source_start_ms=0,source_end_ms=75000,source_duration_ms=75000 WHERE id=(SELECT output_clip_id FROM publish_jobs WHERE id=?)", (job,))
        c.commit()
    return job, version, published


def _snapshot(account, job, published, *, days_ago=1, identity="export:one", batch=None, **extra):
    captured = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    batch = batch or uuid4().hex
    values = dict(id=uuid4().hex, batch_id=batch, account_id=account, aweme_id=identity,
        publish_job_id=job, title="官方标题", published_at=published, captured_at=captured, created_at=captured,
        match_status="matched_unique", play_count=100, five_second_completion_rate=.5,
        completion_rate=.4, average_watch_seconds=20, two_second_bounce_rate=.2)
    values.update(extra)
    with db.get_connection() as c:
        c.execute("""INSERT OR IGNORE INTO content_metric_import_batches
            (id,account_id,source_kind,source_filename,source_sha256,status,created_at,committed_at)
            VALUES(?,?,'douyin_item_export','fixture.xlsx',?,'committed',?,?)""", (batch, account, batch, captured, captured))
        c.execute(f"INSERT INTO douyin_item_metric_snapshots({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))
        c.commit()
    return values


def test_no_account_frozen_report_is_idempotent_traceable_and_has_no_production_side_effects(human_db):
    seed_run()
    decide("human-task", "run-1")
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(lambda _: intelligence.create_report("", 30, key), range(2)))
    assert reports[0] == reports[1]
    report = reports[0]["report"]
    assert report["performance"]["status"] == "no_account" and not report["performance"]["recommendations"]
    assert report["human_review"]["summary"]["accepted"] == 1
    assert report["human_review"]["evidence_refs"][0]["decision_id"]
    decide("human-task", "run-1", decision="reject", reason="low_value")
    assert intelligence.get_report(reports[0]["id"]) == reports[0]
    assert intelligence.create_report("", 30, key) == reports[0]
    with pytest.raises(old.ContentReviewError, match="配置不一致"):
        intelligence.create_report("", 90, key)
    with db.get_connection() as c:
        for table in ("workflow_jobs", "publish_jobs", "content_improvement_experiments"):
            assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM content_intelligence_reports").fetchone()[0] == 1


def test_official_features_use_cut_snapshot_and_initial_run_not_edited_candidate(human_db):
    account = _insert_account()
    job, version, published = _chain(account)
    original = _snapshot(account, job, published)
    with db.get_connection() as c:
        c.execute("UPDATE clip_candidates SET duration_seconds=240,topic_key='新话题',quality_score=1,title='新标题'")
        c.commit()
    saved = intelligence.create_report(account, 30, str(uuid4()))
    work = saved["report"]["performance"]["works"][0]
    assert work["duration_seconds"] == 75 and work["duration_basis"] == "cut_snapshot"
    assert work["quality_score"] == 90 and work["topic"] == "原始话题" and work["hook_type"] == "反转"
    assert work["prompt_version_id"] == version and work["strategy_evidence_valid"]
    assert work["initial_bounds_match"] is False  # Original AI range 60s, actual cut 75s.
    assert work["snapshot_id"] == original["id"]
    assert saved["report"]["performance"]["groups"]["profile"][0]["confidence"] == "insufficient"
    assert work["metrics"]["like_count"] is None
    _snapshot(account, job, published, days_ago=0, duration_seconds=90, play_count=900)
    assert intelligence.get_report(saved["id"]) == saved
    updated = intelligence.create_report(account, 30, str(uuid4()))["report"]["performance"]
    assert updated["work_count"] == 1 and updated["works"][0]["duration_seconds"] == 90


def test_latest_unmatched_never_resurrects_old_match_and_accounts_are_isolated(human_db):
    account, other = _insert_account(), _insert_account()
    job, _, published = _chain(account)
    _snapshot(account, job, published, days_ago=2)
    assert old.get_prompt_comparison(account)["versions"][0]["accurate_published_count"] == 1
    _snapshot(account, None, published, days_ago=1, match_status="unmatched")
    assert old.get_prompt_comparison(account)["versions"][0]["accurate_published_count"] == 0
    _snapshot(other, job, published, identity="foreign")  # Deliberately corrupt cross-account chain.
    report = intelligence.create_report(account, 30, str(uuid4()))["report"]["performance"]
    assert report["work_count"] == 1 and report["attributed_count"] == 0
    assert report["works"][0]["run_id"] is None
    foreign = intelligence.create_report(other, 30, str(uuid4()))["report"]["performance"]
    assert foreign["attributed_count"] == 0 and foreign["works"][0]["profile"] == "unknown"


def test_changed_export_title_dedupes_job_but_same_batch_collision_is_ambiguous(human_db):
    account = _insert_account()
    job, _, published = _chain(account)
    _snapshot(account, job, published, days_ago=2, identity="old-title")
    latest = _snapshot(account, job, published, days_ago=1, identity="new-title", play_count=999)
    report = intelligence.create_report(account, 30, str(uuid4()))["report"]["performance"]
    assert report["work_count"] == 1 and report["attributed_count"] == 1
    assert report["works"][0]["metrics"]["play_count"] == 999
    _snapshot(account, job, published, days_ago=1, identity="collision", batch=latest["batch_id"])
    ambiguous = intelligence.create_report(account, 30, str(uuid4()))["report"]["performance"]
    assert ambiguous["work_count"] == 1 and ambiguous["attributed_count"] == 0
    assert ambiguous["works"][0]["attribution_issue"] == "multiple_export_identities_for_job"


def test_report_thresholds_count_works_not_snapshots_and_preserve_missing_metrics():
    row = dict(profile="variety_comedy", age_band="8–30d", strategy_evidence_valid=True, initial_bounds_match=True,
        source_sha256="a", published_at="2026-09-01", metrics={key: .5 for key in intelligence.METRICS})
    rows = [{**row, "source_sha256": str(i % 3)} for i in range(30)]
    weeks = {"2026-W35", "2026-W36", "2026-W37"}
    assert intelligence._group(rows, "prompt_version_id", "p1", weeks, False)["confidence"] == "comparison_ready"
    assert intelligence._group(rows[:29], "prompt_version_id", "p1", weeks, False)["confidence"] == "insufficient"
    assert intelligence._group(rows, "profile", "general", {"2026-W35"}, False)["confidence"] == "insufficient"
    dirty = [*rows, {**row, "strategy_evidence_valid": False}]
    assert intelligence._group(dirty, "profile", "general", weeks, False)["confidence"] == "insufficient"
    altered = [*rows[:-1], {**rows[-1], "initial_bounds_match": False}]
    assert intelligence._group(altered, "profile", "general", weeks, False)["confidence"] == "insufficient"
    rows[0] = {**rows[0], "metrics": {**rows[0]["metrics"], "play_count": None}}
    group = intelligence._group(rows, "profile", "general", weeks, False)
    assert group["metrics"]["play_count"]["count"] == 29
    assert group["metrics"]["play_count"]["confidence"] == "insufficient"
    assert intelligence._group(rows, "profile", "general", weeks, True)["confidence"] == "insufficient"


def test_report_api_validation_and_tamper_detection(human_db):
    client = TestClient(app)
    url = "/api/content-review/intelligence/reports"
    assert client.post(url, json={"days": 0, "request_key": str(uuid4())}).status_code == 422
    assert client.post(url, json={"account_id": "missing", "request_key": str(uuid4())}).status_code == 404
    saved = client.post(url, json={"request_key": str(uuid4())}).json()
    assert client.get(url).json()["reports"][0]["id"] == saved["id"]
    assert client.get(f"{url}/{saved['id']}").json() == saved
    with db.get_connection() as c:
        c.execute("UPDATE content_intelligence_reports SET payload_json='{}'")
        c.commit()
    assert client.get(f"{url}/{saved['id']}").status_code == 409


def test_legacy_small_cohort_and_weekly_fallback_are_insufficient_and_cannot_start_experiment(human_db):
    from app.services.weekly_review_service import _evidence
    account = _insert_account()
    job, _, published = _chain(account)
    first = _snapshot(account, job, published)
    assert old.get_content_review_insights(account)["recommendations"] == []  # No self comparison.
    second, _, second_published = _chain(account, index=1)
    _snapshot(account, second, second_published, identity="second", batch=first["batch_id"])
    recommendations = old.get_content_review_insights(account)["recommendations"]
    assert recommendations and all(r["data_sufficiency"] == "insufficient" for r in recommendations)
    with pytest.raises(old.ContentReviewError, match="样本不足"):
        old.create_content_experiment(account, recommendations[0]["recommendation_id"])
    with db.get_connection() as c:
        evidence = _evidence(c, account)
        assert all(work["group"] == "insufficient" for work in evidence["works"])
        assert c.execute("SELECT COUNT(*) FROM content_improvement_experiments").fetchone()[0] == 0


def test_report_does_not_use_future_exports_or_unofficial_rows(human_db):
    account = _insert_account()
    job, _, published = _chain(account)
    _snapshot(account, job, published, days_ago=-1)
    unofficial = _snapshot(account, job, published, days_ago=1, identity="unofficial")
    with db.get_connection() as c:
        c.execute("UPDATE content_metric_import_batches SET source_kind='manual_csv' WHERE id=?", (unofficial["batch_id"],))
        c.commit()
    performance = intelligence.create_report(account, 30, str(uuid4()))["report"]["performance"]
    assert performance["status"] == "no_official_data" and not performance["works"] and not performance["recommendations"]


def test_group_comparisons_never_cross_profile_or_age_and_require_both_sides():
    row = dict(profile="variety_comedy", age_band="8–30d", strategy_evidence_valid=True, initial_bounds_match=True,
        source_sha256="a", published_at="2026-09-01", metrics={key: .5 for key in intelligence.METRICS})
    rows = [{**row, "source_sha256": str(i % 3)} for i in range(30)]
    weeks = {"2026-W35", "2026-W36", "2026-W37"}
    low = intelligence._group(rows, "duration_band", "60–90s", weeks, False)
    higher = [{**r, "metrics": {**r["metrics"], "five_second_completion_rate": .8}} for r in rows]
    high = intelligence._group(higher, "duration_band", "91–150s", weeks, False)
    groups = {key: [] for key in intelligence.DIMENSIONS}
    groups["duration_band"] = [low, high]
    assert len(intelligence._comparisons(groups)) == 1
    high["profile"] = "interview_story"
    assert not intelligence._comparisons(groups)
    high["profile"] = low["profile"]
    high["age_band"] = "0–7d"
    assert not intelligence._comparisons(groups)
