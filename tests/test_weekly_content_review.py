from __future__ import annotations

from datetime import datetime
import json
import hashlib
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
            "DELETE FROM adaptive_schedule_policies WHERE account_id=?", (account,)
        )
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
                "hypothesis": "片段开头可能影响留存，尚需原文核验。",
                "validation_needed": "核对原文、输出契约与缓存，不直接应用规则。",
            }
            for title, metric in [
                ("开头更直接", "two_second_bounce_rate"),
                ("保留优秀表达", "five_second_completion_rate"),
                ("选择完整紧凑片段", "completion_rate"),
            ]
        ],
    }


class FakeCodex:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    def generate_json_with_schema(self, prompt, schema):
        assert schema == weekly.REPORT_SCHEMA
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


def test_manual_report_never_changes_rules(sample):
    before = protected_state()
    report_id, provider = ready_report(sample)
    report = weekly.list_reports(sample["account"])["reports"][0]
    assert report["result"]["format"] == weekly.REPORT_FORMAT
    assert len(report["result"]["suggestions"]) == 3
    assert "changes" not in report["result"]
    assert report["evidence"]["scope"] == "all"
    assert weekly.enqueue(sample["account"])["report_id"] == report_id
    assert weekly.enqueue(sample["account"], refresh=True)["report_id"] == report_id
    assert provider.calls == 1
    assert protected_state() == before


def protected_state():
    with get_connection() as connection:
        return {
            table: [
                tuple(row)
                for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
            ]
            for table in (
                "ai_prompt_presets",
                "ai_prompt_versions",
                "content_rule_heads",
                "task_generation_rules",
                "publish_jobs",
                "content_rule_applications",
            )
        }


def legacy_application(sample, report_id):
    # Seed a pre-upgrade database record; production no longer has an apply path.
    from app.services.ai_prompt_preset_service import (
        ensure_ai_prompt_version_with_connection,
    )

    with get_connection() as connection:
        before = connection.execute(
            "SELECT * FROM ai_prompt_presets WHERE id=?", (sample["preset"],)
        ).fetchone()
        prompt = before["prompt_text"] + weekly.RULE_MARKER + "历史选片规则"
        version = ensure_ai_prompt_version_with_connection(
            connection,
            preset_id=sample["preset"],
            preset_name=before["name"],
            prompt_text=prompt,
        )
        change = {
            "preset_id": sample["preset"],
            "name": before["name"],
            "suggestion_indexes": [0],
            "before_prompt": before["prompt_text"],
            "after_prompt": prompt,
            "before_copy": "",
            "copy_rules": "历史文案要求",
            "analysis_rules": "历史选片规则",
            "explanation": "历史已确认",
            "after_version_id": version["id"],
            "primary_metrics": ["play_count"],
            "diff": "历史差异",
        }
        result = json.loads(
            connection.execute(
                "SELECT result_json FROM weekly_content_reports WHERE id=?",
                (report_id,),
            ).fetchone()[0]
        )
        result.pop("format", None)
        result["changes"] = [change]
        application_id = "historical-" + uuid4().hex[:12]
        connection.execute(
            "UPDATE weekly_content_reports SET result_json=? WHERE id=?",
            (weekly.dump(result), report_id),
        )
        connection.execute(
            "INSERT INTO content_rule_applications(id,report_id,state,changes_json,created_at) VALUES(?,?,'applied',?,'2026-08-01')",
            (application_id, report_id, weekly.dump([change])),
        )
        connection.execute(
            "UPDATE ai_prompt_presets SET prompt_text=? WHERE id=?",
            (prompt, sample["preset"]),
        )
        connection.execute(
            "INSERT INTO content_rule_heads(preset_id,copy_rules,application_id) VALUES(?,?,?)",
            (sample["preset"], change["copy_rules"], application_id),
        )
        connection.commit()
    return application_id


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
    "mutate", ["foreign_evidence", "missing_good", "duplicate", "rule_patch"]
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
        result["changes"] = [{"preset_id": sample["preset"], "analysis_rules": "patch"}]
    with pytest.raises(review.ContentReviewError):
        weekly._validated_result(result, evidence)


def test_legacy_rules_are_readable_but_all_write_paths_are_disabled(sample):
    report_id, _ = ready_report(sample)
    application_id = legacy_application(sample, report_id)
    before = protected_state()
    client = TestClient(app)
    for path in (
        f"weekly-reports/{report_id}/apply",
        f"rule-applications/{application_id}/revert",
        f"rule-applications/{application_id}/keep",
    ):
        response = client.post("/api/content-review/" + path)
        assert response.status_code == 410
        assert "停用" in response.json()["detail"]
    report = weekly.list_reports(sample["account"])["reports"][0]
    assert report["application"]["id"] == application_id
    assert report["result"]["changes"]
    assert protected_state() == before
    task_id = new_task(sample["preset"])
    assert "历史选片规则" in get_task_ai_prompt_snapshot(task_id)["prompt_text"]
    assert "历史文案要求" in _metadata_prompt({"task_id": task_id}, "douyin")


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
        == 410
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
    application_id = legacy_application(sample, report_id)
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


def test_existing_metadata_cache_contract_is_preserved(monkeypatch):
    from app.services.pipeline_engine import PipelineEngine

    monkeypatch.setattr(weekly, "task_rules", lambda _task_id: {})
    # Persisted pre-upgrade checkpoints contain this v1 wire shape. Adding empty
    # weekly fields would invalidate them and unnecessarily repeat AI generation.
    legacy_payload = {
        "fingerprint_version": 1,
        "output_clip_id": "existing-clip",
        "clip_candidate_id": "",
        "task_name": "",
        "clip_title": "",
        "clip_summary": "",
        "highlight_reason": "",
        "spread_value": "",
        "suggested_editing": "",
        "platform": "douyin",
        "use_ai": False,
        "provider": "rule",
        "model": "",
        "protocol": "",
    }
    expected = hashlib.sha256(
        json.dumps(
            legacy_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    actual = PipelineEngine._metadata_request_fingerprint(
        {"id": "existing-clip"}, "douyin", use_ai=False
    )
    assert actual == expected
    monkeypatch.setattr(
        weekly,
        "task_rules",
        lambda _task_id: {"application_id": "applied", "copy_rules": "new"},
    )
    assert (
        PipelineEngine._metadata_request_fingerprint(
            {"id": "existing-clip"}, "douyin", use_ai=False
        )
        != expected
    )


def test_report_freezes_actual_prompt_version_and_request(sample):
    _, _ = ready_report(sample)
    report = weekly.list_reports(sample["account"])["reports"][0]
    sources = [w["source"] for w in report["evidence"]["works"] if w["source"]]
    assert sources and all(s["prompt_version_id"] and s["prompt_text"] for s in sources)
    assert (
        report["result"]["evidence_sha256"]
        == hashlib.sha256(weekly.dump(report["evidence"]).encode()).hexdigest()
    )
    prompt = weekly._report_prompt(report["evidence"])
    assert (
        report["result"]["request_sha256"]
        == hashlib.sha256(weekly._build_prompt(prompt, None).encode()).hexdigest()
    )
    assert report["result"]["schema_sha256"]


def test_legacy_queued_report_is_cancelled_without_ai(sample):
    report_id = weekly.enqueue(sample["account"])["report_id"]
    with get_connection() as c:
        evidence = json.loads(
            c.execute(
                "SELECT evidence_json FROM weekly_content_reports WHERE id=?",
                (report_id,),
            ).fetchone()[0]
        )
        evidence.pop("report_format")
        c.execute(
            "UPDATE weekly_content_reports SET evidence_json=? WHERE id=?",
            (weekly.dump(evidence), report_id),
        )
        c.commit()
    provider = FakeCodex({})
    before = protected_state()
    assert not weekly.generate_next(provider)
    assert provider.calls == 0 and protected_state() == before
    assert weekly.list_reports(sample["account"])["reports"][0]["status"] == "cancelled"
    assert weekly.enqueue(sample["account"], refresh=True)["report_id"] != report_id


@pytest.mark.parametrize("failure", ["timeout", "invalid_json", "invalid_schema"])
def test_failure_diagnostics_and_no_automatic_replay(sample, failure):
    from app.services.ai.base import AIProviderError

    report_id = weekly.enqueue(sample["account"])["report_id"]

    class BrokenCodex:
        calls = 0

        def generate_json_with_schema(self, prompt, schema):
            self.calls += 1
            if failure == "timeout":
                raise AIProviderError(
                    "timeout", category="timeout", billing_uncertain=True
                )
            return "not json" if failure == "invalid_json" else '{"summary":"bad"}'

    provider = BrokenCodex()
    before = protected_state()
    weekly.generate_next(provider)
    assert not weekly.generate_next(provider)
    assert provider.calls == 1 and protected_state() == before
    report = weekly.list_reports(sample["account"])["reports"][0]
    assert report["id"] == report_id and report["status"] == "failed"
    assert report["result"]["billing_uncertain"] and report["result"]["request_sha256"]
    assert "计费" in report["error"] and "未自动重试" in report["error"]
    if failure != "timeout":
        assert (
            report["result"]["invalid_output"] and report["result"]["response_sha256"]
        )


def test_transcript_boundaries_and_truncation_are_explicit(sample, monkeypatch):
    from app.services import transcript_service

    rows = [{"start_time": "00:00:09", "end_time": "00:00:11", "text": "跨切点"}] + [
        {"start_time": "00:00:12", "end_time": "00:00:13", "text": "区间内"}
        for _ in range(80)
    ]
    monkeypatch.setattr(
        transcript_service,
        "read_transcript_range",
        lambda *a, **kw: rows[: kw["max_rows"]],
    )
    evidence = {
        "works": [
            {
                "context": {
                    "task_id": sample["task"],
                    "start_time": "00:00:10",
                    "end_time": "00:01:00",
                }
            }
        ]
    }
    weekly._add_transcripts(evidence)
    context = evidence["works"][0]["context"]
    assert context["transcript_truncated"] and len(context["transcript"]) == 80
    assert context["transcript"][0]["crosses_clip_boundary"]
    assert not context["transcript"][1]["crosses_clip_boundary"]
    monkeypatch.setattr(
        transcript_service, "read_transcript_range", lambda *a, **kw: []
    )
    weekly._add_transcripts(evidence)
    assert context["transcript_status"] == "missing"


def test_no_valid_evidence_does_not_invoke_ai(sample, monkeypatch):
    weekly.enqueue(sample["account"])
    with get_connection() as c:
        row = c.execute(
            "SELECT id,evidence_json FROM weekly_content_reports WHERE account_id=?",
            (sample["account"],),
        ).fetchone()
        evidence = json.loads(row["evidence_json"])
        evidence["works"] = []
        c.execute(
            "UPDATE weekly_content_reports SET evidence_json=? WHERE id=?",
            (weekly.dump(evidence), row["id"]),
        )
        c.commit()
    provider = FakeCodex({})
    weekly.generate_next(provider)
    report = weekly.list_reports(sample["account"])["reports"][0]
    assert report["status"] == "ready" and not report["result"]["ai_called"]
    assert provider.calls == 0


@pytest.mark.parametrize("mode", ["sync", "manual_import"])
def test_import_apis_only_update_data_even_after_background_ticks(
    sample, monkeypatch, mode
):
    from app.services.publishers.worker_client import PublishWorkerClient
    from app.services import adaptive_schedule
    from tests.test_content_review import _work_export_xlsx, _work_export_row

    with get_connection() as c:
        c.execute(
            "INSERT INTO adaptive_schedule_policies(account_id,enabled,daily_limit,min_gap_minutes,daily_start_time,daily_end_time,version,updated_at) VALUES(?,1,8,90,'07:00','23:59',1,'now')",
            (sample["account"],),
        )
        c.commit()
    monkeypatch.setattr(
        weekly, "enqueue", lambda *a, **kw: pytest.fail("import must not queue AI")
    )
    monkeypatch.setattr(
        adaptive_schedule,
        "enqueue",
        lambda *a, **kw: pytest.fail("import must not queue replan"),
    )
    before = protected_state()
    client = TestClient(app)
    if mode == "sync":
        monkeypatch.setattr(
            PublishWorkerClient,
            "analytics_export_sync",
            lambda *a, **kw: {
                "items": [
                    {
                        "title": "new metric only",
                        "published_at": "2026-08-28T10:00:00+08:00",
                        "play_count": 42,
                        "content_genre": "视频",
                    }
                ],
                "row_count": 1,
                "captured_at": "2026-09-11T10:00:00+08:00",
                "source_filename": "test.xlsx",
            },
        )
        response = client.post(
            "/api/content-review/douyin/export-sync",
            json={"account_id": sample["account"]},
        )
    else:
        preview = review.preview_metric_import(
            account_id=sample["account"],
            filename="test.xlsx",
            content=_work_export_xlsx(rows=[_work_export_row(88)]),
        )
        response = client.post(
            f"/api/content-review/imports/{preview['batch_id']}/commit",
            json={"confirm": True},
        )
    assert response.status_code == 200, response.text
    provider = FakeCodex({})
    for _ in range(3):
        assert not weekly.generate_next(provider)
        assert adaptive_schedule.process_pending() == []
    assert provider.calls == 0
    assert not weekly.list_reports(sample["account"])["reports"]
    assert protected_state() == before


def test_concurrent_manual_clicks_share_one_request(sample):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda _: weekly.enqueue(sample["account"], refresh=True), range(2)
            )
        )
    assert len({r["report_id"] for r in results}) == 1
    assert {r["status"] for r in results} == {"queued", "already_queued"}
