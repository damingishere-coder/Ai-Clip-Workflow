from copy import deepcopy
import hashlib
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db.database import get_connection
from app.models.task import TaskCreate
from app.services import job_service, visual_evidence_service as visual
from app.services.ai.base import AIProviderError
from app.services.ai import unit_checkpoint
from app.services.storage_service import get_task_directory
from app.services.task_lifecycle_service import create_task_record


class Provider:
    name = "test-visual"
    config = SimpleNamespace(model="same-model")
    def __init__(self):
        self.calls = 0
        self.fail = False
    def generate_visual_json(self, prompt, schema, images, *, timeout_seconds):
        self.calls += 1
        if self.fail:
            raise AIProviderError("超时", category="timeout", billing_uncertain=True)
        return '{"observations":[{"image_indices":[0],"kind":"ocr","description":"清晰字幕","confidence":"high"}],"limitations":["离散帧不代表完整事件"]}'


@pytest.fixture
def visual_task():
    task_id = "visual-" + uuid4().hex[:12]
    task = create_task_record(TaskCreate(task_name="隔离视觉测试", selection_profile="general"), task_id=task_id)
    job, _ = job_service.create_or_get_active_job(task_id, job_service.JOB_TYPE_AI_ANALYSIS, {"provider":"remote"})
    claimed = job_service.claim_job(job["id"], "visual-owner")
    # 一些旧队列测试重载 DB 配置；用真实创建返回的映射，不走旧模块的缓存配置查询。
    directory = get_task_directory(task_id, task["task_dir_name"]) / "analysis" / "visual" / uuid4().hex
    directory.mkdir(parents=True)
    path = directory / f"{uuid4().hex}.jpg"
    path.write_bytes(b"\xff\xd8frame\xff\xd9")
    sampling = {"status":"partial","sampler_version":"test-v1","source_sha256":"a"*64,
        "plan":{"start_seconds":10,"end_seconds":20,"points":[{"timestamp_seconds":11,"role":"clip_start","source":"candidate_boundary"}]},
        "frames":[{"file_name":path.name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"size_bytes":path.stat().st_size,
                   "timestamp_seconds":11,"actual_timestamp_seconds":11.02,"role":"clip_start","source":"candidate_boundary"}],
        "skipped":[{"reason":"black_frame"}]}
    context = SimpleNamespace(task_id=task_id, job=claimed, directory=directory, path=path, sampling=sampling, provider=Provider())
    try:
        yield context
    finally:
        with get_connection() as connection:
            for table in ("candidate_visual_evidence","workflow_jobs","ai_analysis_runs","task_generation_rules"):
                connection.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id,))
            connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            connection.commit()


def lease(context):
    return job_service.job_lease_context(context.job["id"], context.job["lease_owner"], context.job["lease_token"])


def prepare(context, **kwargs):
    return visual.prepare_candidate_visual(task_id=context.task_id, candidate_key="candidate-1", input_fingerprint="b"*64,
        sampling=kwargs.get("sampling", context.sampling), cache_directory=kwargs.get("directory", context.directory), provider=context.provider)


def analyze(context, row):
    return visual.analyze_candidate_visual(task_id=context.task_id, evidence_id=row["id"], provider=context.provider)


def takeover(context):
    with get_connection() as connection:
        connection.execute("UPDATE workflow_jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (context.job["id"],))
        connection.commit()
    context.job = job_service.claim_job(context.job["id"], "visual-new-owner")
    assert context.job


def test_no_job_lease_cannot_prepare_or_call(visual_task):
    with pytest.raises(job_service.JobLeaseLostError):
        prepare(visual_task)
    assert visual_task.provider.calls == 0


def test_completed_evidence_reuses_checkpoint_after_takeover_and_cache_removal(visual_task):
    c = visual_task
    with lease(c):
        row = prepare(c)
        assert analyze(c, row)["status"] == "partial"
    c.path.unlink()
    takeover(c)
    with lease(c):
        result = analyze(c, row)
    assert result["call_status"] == "completed" and c.provider.calls == 1
    assert json.loads(result["evidence_json"])["response"]["observations"][0]["image_indices"] == [0]


def test_uncertain_call_is_not_rebilled_and_does_not_change_text_units(visual_task):
    c = visual_task
    c.provider.fail = True
    with lease(c):
        unit_checkpoint.execute_checkpointed_ai_unit(task_id=c.task_id,namespace="text",input_fingerprint="text-input",unit_id="text1",operation=lambda:{"ok":True})
        before = deepcopy(job_service.get_job(c.job["id"])["checkpoint_json"]["_ai_analysis_units_v1"]["namespaces"]["text"])
        row = prepare(c)
        result = analyze(c, row)
        assert result["status"] == "unavailable" and result["call_status"] == "uncertain"
    takeover(c)
    with lease(c):
        assert analyze(c, row)["status"] == "unavailable"
    assert c.provider.calls == 1
    assert job_service.get_job(c.job["id"])["checkpoint_json"]["_ai_analysis_units_v1"]["namespaces"]["text"] == before


def test_crash_after_provider_before_checkpoint_commit_does_not_retry(visual_task, monkeypatch):
    c = visual_task
    with lease(c):
        row = prepare(c)
        with monkeypatch.context() as scope:
            scope.setattr(unit_checkpoint, "_finish_unit_success", lambda **kw: (_ for _ in ()).throw(SystemExit("simulated crash")))
            with pytest.raises(SystemExit):
                analyze(c, row)
    takeover(c)
    with lease(c):
        result = analyze(c, row)
    assert result["call_status"] == "uncertain" and result["status"] == "unavailable" and c.provider.calls == 1


def test_saved_result_without_checkpoint_cannot_be_rebilled(visual_task):
    c = visual_task
    with lease(c):
        row = prepare(c)
        analyze(c, row)
        with get_connection() as connection:
            connection.execute("UPDATE workflow_jobs SET checkpoint_json='{}' WHERE id=?", (c.job["id"],))
            connection.commit()
        result = analyze(c, row)
    assert result["status"] == "unavailable" and result["failure_reason"] == "visual_checkpoint_missing_after_call"
    assert c.provider.calls == 1


def test_stale_worker_cannot_write_result_after_provider(visual_task, monkeypatch):
    c = visual_task
    original = c.provider.generate_visual_json
    def steal(*a, **kw):
        value = original(*a, **kw)
        takeover(c)
        return value
    monkeypatch.setattr(c.provider, "generate_visual_json", steal)
    with lease(c):
        row = prepare(c)
        with pytest.raises(job_service.JobLeaseLostError):
            analyze(c, row)
    with lease(c):
        current = visual.get_candidate_visual(c.task_id, "candidate-1")
        assert current["status"] == "pending" and current["evidence_json"] is None
        assert analyze(c, row)["call_status"] == "uncertain"
    assert c.provider.calls == 1


@pytest.mark.parametrize("change", ["frame", "timestamp", "model"])
def test_frozen_request_cannot_be_silently_replaced(visual_task, change):
    c = visual_task
    with lease(c):
        row = prepare(c)
        modified = deepcopy(c.sampling)
        if change == "frame":
            modified["frames"][0]["sha256"] = "c"*64
        elif change == "timestamp":
            modified["frames"][0]["actual_timestamp_seconds"] = 12
        else:
            c.provider.config = SimpleNamespace(model="new-model")
        with pytest.raises(ValueError, match="已冻结"):
            prepare(c, sampling=modified)
        assert visual.get_candidate_visual(c.task_id, "candidate-1")["request_json"] == row["request_json"]
    assert c.provider.calls == 0


def test_random_cache_names_do_not_change_request_identity(visual_task):
    c = visual_task
    modified = deepcopy(c.sampling)
    modified["frames"][0]["file_name"] = f"{uuid4().hex}.jpg"
    with lease(c):
        first = prepare(c)
        second = prepare(c, sampling=modified, directory=c.directory.parent/uuid4().hex)
        assert second["id"] == first["id"] and second["cache_relative_dir"] == first["cache_relative_dir"]
    assert c.provider.calls == 0


def test_missing_and_changed_cache_fails_without_call(visual_task):
    c = visual_task
    with lease(c):
        row = prepare(c)
        c.path.write_bytes(b"different")
        result = analyze(c, row)
    assert result["status"] == "unavailable" and c.provider.calls == 0
    assert result["call_status"] == "retryable_failed"


def test_unbilled_failure_requires_explicit_retry_and_keeps_same_request(visual_task, monkeypatch):
    c = visual_task
    original = c.provider.generate_visual_json
    def busy(*a, **kw):
        raise AIProviderError("尚未调用", category="visual_slot_timeout", safe_to_retry=True)
    with lease(c):
        row = prepare(c)
        monkeypatch.setattr(c.provider, "generate_visual_json", busy)
        assert analyze(c, row)["call_status"] == "retryable_failed"
        monkeypatch.setattr(c.provider, "generate_visual_json", original)
        assert analyze(c, row)["status"] == "unavailable" and c.provider.calls == 0
        retried = visual.analyze_candidate_visual(task_id=c.task_id, evidence_id=row["id"], provider=c.provider, retry_unbilled=True)
        assert retried["call_status"] == "completed" and c.provider.calls == 1


def test_completed_result_cannot_be_rebilled_with_contradictory_retryable_checkpoint(visual_task):
    c = visual_task
    with lease(c):
        row = prepare(c)
        analyze(c, row)
        checkpoint = job_service.get_job(c.job["id"])["checkpoint_json"]
        checkpoint["_ai_analysis_units_v1"]["namespaces"][visual.NAMESPACE]["units"]["candidate-1"] = {"status":"retryable_failed"}
        with get_connection() as connection:
            connection.execute("UPDATE workflow_jobs SET checkpoint_json=? WHERE id=?", (json.dumps(checkpoint), c.job["id"]))
            connection.commit()
        assert analyze(c, row)["failure_reason"] == "visual_checkpoint_conflicts_with_result"
    assert c.provider.calls == 1


def test_schema_invalid_visual_reply_is_not_cached_as_success(visual_task, monkeypatch):
    c = visual_task
    monkeypatch.setattr(c.provider, "generate_visual_json", lambda *a, **kw: '{"observations":[{"image_indices":[9],"kind":"ocr","description":"幻觉","confidence":"high"}],"limitations":["离散帧"]}')
    with lease(c):
        row = prepare(c)
        result = analyze(c, row)
    assert result["status"] == "unavailable" and result["call_status"] == "uncertain"
    assert "不存在" in result["failure_reason"]
    diagnostic = json.loads(result["evidence_json"])
    assert diagnostic["invalid_response"] and len(diagnostic["raw_response_sha256"]) == 64


def test_unsupported_provider_is_explicitly_unavailable_without_ai(visual_task):
    c = visual_task
    c.provider = SimpleNamespace(name="text-only", config=SimpleNamespace(model="old"))
    with lease(c):
        row = prepare(c)
        assert row["status"] == "unavailable" and row["failure_reason"] == "provider_visual_unsupported"
        assert analyze(c, row)["call_status"] == "not_called"


def test_visual_result_corruption_never_rebills(visual_task):
    c = visual_task
    with lease(c):
        row = prepare(c)
        analyze(c, row)
        with get_connection() as connection:
            connection.execute("UPDATE candidate_visual_evidence SET evidence_json='{}' WHERE id=?", (row["id"],))
            connection.commit()
        assert analyze(c, row)["failure_reason"] == "visual_evidence_mismatch"
    assert c.provider.calls == 1


def test_request_sql_is_immutable_and_run_attachment_is_transactional(visual_task):
    c = visual_task
    with lease(c):
        row = prepare(c)
        with get_connection() as connection:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute("UPDATE candidate_visual_evidence SET request_json='{}' WHERE id=?", (row["id"],))
            connection.rollback()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO ai_analysis_runs(id,task_id,run_number,provider,provider_label,model,analysis_payload_json,created_at) VALUES(?,?,1,'test','Test','model','{}','now')", ("run-"+c.task_id,c.task_id))
            visual.attach_visual_evidence_with_connection(connection, task_id=c.task_id, job_id=c.job["id"], run_id="run-"+c.task_id)
            assert connection.execute("SELECT analysis_run_id FROM candidate_visual_evidence WHERE id=?", (row["id"],)).fetchone()[0] == "run-"+c.task_id
            connection.rollback()
        assert visual.get_candidate_visual(c.task_id, "candidate-1")["analysis_run_id"] is None
