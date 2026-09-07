from __future__ import annotations

from datetime import datetime
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_connection, init_db
from app.main import app
from app.services import content_review_service as review
from app.services import weekly_review_service as weekly
from app.services.ai_prompt_preset_service import get_task_ai_prompt_snapshot
from app.services.publish_service import _metadata_prompt, _publish_provider_payload
from tests.test_content_review import (
    _insert_account,
    _seed_diagnosis_baseline,
    _attach_prompt_chain,
    _cleanup,
)


@pytest.fixture
def sample():
    init_db()
    account = _insert_account()
    batch = _seed_diagnosis_baseline(account)
    with get_connection() as connection:
        job = connection.execute(
            "SELECT id,task_id FROM publish_jobs WHERE account_id=? ORDER BY id LIMIT 1",
            (account,),
        ).fetchone()
    version = _attach_prompt_chain(job["id"], "周复盘测试")
    with get_connection() as connection:
        preset = connection.execute(
            "SELECT preset_id FROM ai_prompt_versions WHERE id=?", (version,)
        ).fetchone()["preset_id"]
        connection.execute(
            "UPDATE tasks SET ai_prompt_preset_id=? WHERE id=?",
            (preset, job["task_id"]),
        )
        weekly.freeze_task(connection, job["task_id"])
        connection.commit()
    yield {
        "account": account,
        "batch": batch,
        "preset": preset,
        "task": job["task_id"],
        "job": job["id"],
    }
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM task_generation_rules WHERE preset_id=?", (preset,)
        )
        connection.execute(
            "DELETE FROM content_rule_heads WHERE preset_id=?", (preset,)
        )
        connection.execute(
            "DELETE FROM content_rule_applications WHERE report_id IN (SELECT id FROM weekly_content_reports WHERE account_id=?)",
            (account,),
        )
        connection.execute(
            "DELETE FROM weekly_content_reports WHERE account_id=?", (account,)
        )
        connection.commit()
    _cleanup()


def output_for(evidence, preset):
    ids = [w["id"] for w in evidence["works"] if w["group"] != "insufficient"]
    return {
        "summary": "对全部作品进行好坏对照后，优先改善开头，同时保留优秀作品的表达。",
        "suggestions": [
            {
                "title": title,
                "finding": "结合好坏作品分析，有待验证的结构差异。",
                "action": "选择更紧凑的连续片段",
                "expected_effect": "改善留存",
                "insufficient": False,
                "evidence_ids": ids,
                "primary_metric": metric,
            }
            for title, metric in [
                ("开头更直接", "two_second_bounce_rate"),
                ("保留优秀表达", "five_second_completion_rate"),
                ("选择完整紧凑片段", "completion_rate"),
            ]
        ],
        "changes": [
            {
                "preset_id": preset,
                "suggestion_indexes": [0, 1, 2],
                "analysis_rules": "优先选择直接进入核心观点且语义完整的连续片段。",
                "copy_rules": "标题优先使用片段中明确的冲突。",
                "explanation": "根据好坏作品共同证据调整。",
            }
        ],
    }


class FakeCodex:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    def generate_json(self, prompt):
        self.calls += 1
        assert "context" in prompt and "suggestions" in prompt
        return json.dumps(self.output, ensure_ascii=False)


def ready_report(sample):
    report_id = weekly.enqueue(sample["account"])["report_id"]
    report = weekly.list_reports(sample["account"])["reports"][0]
    provider = FakeCodex(output_for(report["evidence"], sample["preset"]))
    assert weekly.generate_next(provider)
    report = weekly.list_reports(sample["account"])["reports"][0]
    assert report["status"] == "ready", report["error"]
    return report_id, provider


def new_task(preset):
    task_id = "test-content-review-new-" + uuid4().hex[:8]
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO tasks(id,task_name,task_dir_name,ai_prompt_preset_id,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (task_id, task_id, task_id, preset, "now", "now"),
        )
        weekly.freeze_task(connection, task_id)
        connection.commit()
    return task_id


def test_weekly_synthesis_application_and_real_rollback(sample):
    before = get_task_ai_prompt_snapshot(sample["task"])
    report_id, provider = ready_report(sample)
    report = weekly.list_reports(sample["account"])["reports"][0]
    assert len(report["result"]["suggestions"]) == 3
    assert report["evidence"]["scope"] == "all"
    assert report["evidence"]["counts"]["good"] and report["evidence"]["counts"]["weak"]
    assert (
        get_task_ai_prompt_snapshot(sample["task"])["prompt_text"]
        == before["prompt_text"]
    )
    assert weekly.enqueue(sample["account"])["report_id"] == report_id
    assert weekly.enqueue(sample["account"], refresh=True)["report_id"] == report_id
    assert provider.calls == 1
    result = weekly.apply_report(report_id)
    assert weekly.apply_report(report_id) == result
    created = new_task(sample["preset"])
    after = get_task_ai_prompt_snapshot(created)
    assert "优先选择直接进入" in after["prompt_text"]
    assert after["prompt_version_id"] != before["prompt_version_id"]
    assert (
        get_task_ai_prompt_snapshot(sample["task"])["prompt_version_id"]
        == before["prompt_version_id"]
    )
    assert "标题优先使用" in _metadata_prompt({"task_id": created}, "douyin")
    assert "标题优先使用" not in _metadata_prompt({"task_id": sample["task"]}, "douyin")
    assert "标题优先使用" not in _metadata_prompt({"task_id": created}, "bilibili")
    assert (
        weekly.list_reports(sample["account"])["reports"][0]["application"]["progress"][
            "treatments"
        ]
        == 0
    )
    weekly.revert_application(result["application_id"])
    reverted = get_task_ai_prompt_snapshot(new_task(sample["preset"]))
    assert reverted["prompt_version_id"] == before["prompt_version_id"]
    assert (
        get_task_ai_prompt_snapshot(created)["prompt_version_id"]
        == after["prompt_version_id"]
    )
    assert weekly.revert_application(result["application_id"])["status"] == "reverted"


def test_first_full_next_week_seven_days_and_history_frozen(sample):
    report_id, _ = ready_report(sample)
    first = weekly.list_reports(sample["account"])["reports"][0]
    with get_connection() as connection:
        items = json.loads(
            connection.execute(
                "SELECT normalized_payload_json FROM content_metric_import_batches WHERE id=?",
                (sample["batch"]["batch_id"],),
            ).fetchone()[0]
        )
    # A new export is a new snapshot even when it contains older works.
    items[0]["play_count"] += 100
    review.commit_douyin_item_export(
        account_id=sample["account"],
        items=items,
        captured_at="2026-08-31T12:00:00+08:00",
        source_filename="next-week.xlsx",
    )
    second_id = weekly.enqueue(sample["account"])["report_id"]
    assert second_id != report_id
    reports = weekly.list_reports(sample["account"])["reports"]
    second = next(r for r in reports if r["id"] == second_id)
    assert second["evidence"]["scope"] == "seven_days"
    assert second["evidence"]["period_start"] == "2026-08-24T12:00:00+08:00"
    assert len(second["evidence"]["works"]) < len(first["evidence"]["works"])
    assert (
        next(r for r in reports if r["id"] == report_id)["evidence"]
        == first["evidence"]
    )


def test_failed_output_never_changes_rules_and_manual_retry(sample):
    before = get_task_ai_prompt_snapshot(sample["task"])
    first = weekly.enqueue(sample["account"])["report_id"]
    provider = FakeCodex({"summary": "bad", "suggestions": []})
    weekly.generate_next(provider)
    assert weekly.list_reports(sample["account"])["reports"][0]["status"] == "failed"
    assert not weekly.generate_next(provider)
    assert provider.calls == 1
    with pytest.raises(review.ContentReviewError):
        weekly.apply_report(first)
    assert (
        get_task_ai_prompt_snapshot(sample["task"])["prompt_text"]
        == before["prompt_text"]
    )
    assert weekly.enqueue(sample["account"])["report_id"] == first
    assert weekly.enqueue(sample["account"], refresh=True)["report_id"] != first


@pytest.mark.parametrize(
    "mutate", ["foreign_evidence", "missing_good", "duplicate", "unsupported_target"]
)
def test_rejects_unverifiable_suggestions(sample, mutate):
    weekly.enqueue(sample["account"])
    evidence = weekly.list_reports(sample["account"])["reports"][0]["evidence"]
    result = output_for(evidence, sample["preset"])
    if mutate == "foreign_evidence":
        result["suggestions"][0]["evidence_ids"] = ["not-a-snapshot"]
    elif mutate == "missing_good":
        ids = [w["id"] for w in evidence["works"] if w["group"] == "weak"]
        for suggestion in result["suggestions"]:
            suggestion["evidence_ids"] = ids
    elif mutate == "duplicate":
        result["suggestions"][1]["title"] = result["suggestions"][0]["title"]
    else:
        result["changes"][0]["preset_id"] = "not-used-by-this-account"
    with pytest.raises(review.ContentReviewError):
        weekly._validated_result(result, evidence)


def test_conflicting_manual_edit_blocks_apply_and_rollback(sample):
    report_id, _ = ready_report(sample)
    with get_connection() as connection:
        original = connection.execute(
            "SELECT prompt_text FROM ai_prompt_presets WHERE id=?", (sample["preset"],)
        ).fetchone()[0]
        connection.execute(
            "UPDATE ai_prompt_presets SET prompt_text=? WHERE id=?",
            ("人工修改", sample["preset"]),
        )
        connection.commit()
    with pytest.raises(review.ContentReviewError, match="已经改变"):
        weekly.apply_report(report_id)
    with get_connection() as connection:
        connection.execute(
            "UPDATE ai_prompt_presets SET prompt_text=? WHERE id=?",
            (original, sample["preset"]),
        )
        connection.commit()
    application = weekly.apply_report(report_id)
    with get_connection() as connection:
        connection.execute(
            "UPDATE ai_prompt_presets SET prompt_text=? WHERE id=?",
            ("后续人工修改", sample["preset"]),
        )
        connection.commit()
    with pytest.raises(review.ContentReviewError, match="再次改变"):
        weekly.revert_application(application["application_id"])


def test_read_endpoints_do_not_generate_and_apply_is_explicit(sample):
    client = TestClient(app)
    response = client.get(
        "/api/content-review/weekly-reports", params={"account_id": sample["account"]}
    )
    assert response.status_code == 200 and response.json()["reports"] == []
    response = client.post(
        "/api/content-review/weekly-reports", json={"account_id": sample["account"]}
    )
    report_id = response.json()["report_id"]
    assert (
        client.post(f"/api/content-review/weekly-reports/{report_id}/apply").status_code
        == 409
    )
    assert (
        client.get(
            "/api/content-review/weekly-reports",
            params={"account_id": sample["account"]},
        ).json()["reports"][0]["status"]
        == "queued"
    )


def test_expired_run_is_held_and_not_replayed(sample, monkeypatch):
    report_id = weekly.enqueue(sample["account"])["report_id"]
    with get_connection() as connection:
        connection.execute(
            "UPDATE weekly_content_reports SET status='running',expires_at='2026-01-01T00:00:00+08:00' WHERE id=?",
            (report_id,),
        )
        connection.commit()
    monkeypatch.setattr(
        weekly, "now", lambda: datetime.fromisoformat("2026-09-07T00:00:00+08:00")
    )
    provider = FakeCodex({})
    assert not weekly.generate_next(provider)
    assert provider.calls == 0
    assert weekly.list_reports(sample["account"])["reports"][0]["status"] == "failed"


def test_rule_fallback_clears_ai_application_evidence():
    payload = json.loads(
        _publish_provider_payload(
            {"source": "rule"},
            existing={
                "weekly_rule_application_id": "old",
                "weekly_content_signature": "old",
            },
        )
    )
    assert payload["weekly_rule_application_id"] is None
    assert payload["weekly_content_signature"] is None


def test_effect_comparison_matches_cohorts_and_respects_primary_metric():
    base = {key: 0.5 for key in weekly.METRICS}
    base.update(
        play_count=100,
        genre_bucket="视频",
        duration_bucket="short",
        age_bucket="mature",
    )
    baseline = [{**base, "id": str(index)} for index in range(20)]
    treatments = [{**base, "play_count": 125} for _ in range(20)]
    metrics, count, controls = weekly._comparable_metrics(baseline, treatments)
    assert (count, controls) == (20, 20)
    assert "建议保留" in weekly._assess_metrics(metrics, {"play_count"})
    metrics["play_count"]["after"] = 75
    assert "建议回退" in weekly._assess_metrics(metrics, {"play_count"})
    treatments[0]["age_bucket"] = "newly_published"
    assert weekly._comparable_metrics(baseline, treatments)[1] == 19
    assert weekly._comparable_metrics(baseline[:4], treatments) == ({}, 0, 0)


def test_actual_prompt_and_copy_evidence_required_for_assignment(sample):
    from tests.test_content_review import _insert_publish_job

    report_id, _ = ready_report(sample)
    application_id = weekly.apply_report(report_id)["application_id"]
    job_id = _insert_publish_job(
        sample["account"],
        title="采用新规则的视频",
        published_at="2026-09-07T12:00:00+08:00",
    )
    _attach_prompt_chain(job_id, "实际采用")
    with get_connection() as connection:
        job = dict(
            connection.execute(
                "SELECT * FROM publish_jobs WHERE id=?", (job_id,)
            ).fetchone()
        )
        connection.execute(
            "UPDATE tasks SET ai_prompt_preset_id=? WHERE id=?",
            (sample["preset"], job["task_id"]),
        )
        weekly.freeze_task(connection, job["task_id"], replace=True)
        connection.commit()
    snapshot = get_task_ai_prompt_snapshot(job["task_id"])
    with get_connection() as connection:
        connection.execute(
            "UPDATE ai_analysis_runs SET prompt_version_id=? WHERE task_id=?",
            (snapshot["prompt_version_id"], job["task_id"]),
        )
        connection.execute(
            "UPDATE publish_jobs SET provider_response=? WHERE id=?",
            (
                json.dumps(
                    {
                        "weekly_rule_application_id": application_id,
                        "weekly_content_signature": weekly.content_signature(
                            job["title"], job["description"], job["tags"]
                        ),
                    }
                ),
                job_id,
            ),
        )
        connection.commit()
    assert (
        weekly.list_reports(sample["account"])["reports"][0]["application"]["progress"][
            "assigned"
        ]
        == 1
    )
    with get_connection() as connection:
        connection.execute(
            "UPDATE publish_jobs SET title='人工另写的标题' WHERE id=?", (job_id,)
        )
        connection.commit()
    assert (
        weekly.list_reports(sample["account"])["reports"][0]["application"]["progress"][
            "assigned"
        ]
        == 0
    )
