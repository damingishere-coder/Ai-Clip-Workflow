from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.db.database import get_connection
from app.services import job_service, visual_analysis_service as visual
from app.services.visual_policy_service import visual_policy, update_task_visual, task_visual_policy
from app.services.task_service import get_task
from tests.test_visual_evidence import visual_task as _visual_task_fixture, lease, takeover  # noqa: F401

visual_task = _visual_task_fixture


@pytest.fixture
def round_context(visual_task, monkeypatch):
    c = visual_task
    c.samples = 0
    c.judge_calls = 0
    c.judge_fault = ""
    def judge(prompt, schema, *, timeout_seconds):
        c.judge_calls += 1
        if c.judge_fault == "json":
            return "invalid JSON"
        material = json.loads(prompt.split("\n", 1)[1])
        return json.dumps({"verdicts": [{"source_id": row["candidate"]["source_id"], "support": 1.0,
            "observation_indices": [99] if c.judge_fault == "citation" else [0], "reason": "清晰字幕支持内容"} for row in material]})
    c.provider.generate_visual_judgment_json = judge
    class Sampler:
        def __init__(self, **kwargs):
            self.directory = c.directory
            self.source_duration = 120
            self.source_sha256 = "a"*64
            self.budget = kwargs
        def sample(self, plan, *, candidate_source_id):
            c.samples += 1
            result = deepcopy(c.sampling)
            result["candidate_source_id"] = candidate_source_id
            result["plan"]["start_seconds"] = plan.start_seconds
            result["plan"]["end_seconds"] = plan.end_seconds
            return result
    monkeypatch.setattr(visual, "CandidateFrameSampler", Sampler)
    monkeypatch.setattr(visual, "_duration", lambda *args: 120)
    monkeypatch.setattr(visual, "_current_source_hash", lambda task: "a"*64)
    monkeypatch.setattr(visual, "settings", SimpleNamespace(ai_visual_enabled=True))
    c.candidates = [{"source_id": "candidate-1", "start_seconds": 10, "end_seconds": 20, "key_seconds": 11, "title": "明确观点", "summary": "完整观点"}]
    c.task = get_task(c.task_id, include_video_probe=False)
    c.session = lambda: visual.VisualAnalysisSession(c.task, visual_policy(True), {"profile": "test", "rules": "v1"})
    c.scored = lambda: [{**candidate, "quality_score": 80.0, "quality_tier": "A", "selected_by_default": True} for candidate in c.candidates]
    return c


def test_optional_verify_judge_evidence_and_takeover_reuse_after_cache_cleanup(round_context):
    c = round_context
    with lease(c):
        session = c.session()
        session.verify(c.candidates, c.provider)
        scored = c.scored()
        session.judge(scored, c.provider)
        assert scored[0]["quality_score"] == 88
        assert scored[0]["quality_tier"] == "A"
        assert session.metadata()["verified_count"] == 1
        assert session.global_judge["status"] == "completed"
        assert "quality_evidence" not in c.candidates[0]
    c.path.unlink()
    takeover(c)
    with lease(c):
        resumed = c.session()
        resumed.verify(c.candidates, c.provider)
        resumed.judge(c.scored(), c.provider)
        assert resumed.global_judge["status"] == "completed"
    assert (c.provider.calls, c.judge_calls, c.samples) == (1, 1, 1)


def test_visual_failure_preserves_text_scores_and_does_not_call_judge(round_context):
    c = round_context
    c.provider.fail = True
    with lease(c):
        session = c.session()
        session.verify(c.candidates, c.provider)
        scored = c.scored()
        session.judge(scored, c.provider)
        assert scored[0]["quality_score"] == 80 and scored[0]["selected_by_default"]
        assert session.metadata()["status"] == "unavailable"
        assert "timeout" in scored[0]["quality_evidence"]["visual"]["failure_reason"]
        resumed = c.session()
        resumed.verify(c.candidates, c.provider)
    assert c.provider.calls == 1 and c.judge_calls == 0


@pytest.mark.parametrize("fault", ["json", "citation"])
def test_invalid_visual_global_judge_is_optional_uncertain_and_never_retried(round_context, fault):
    c = round_context
    c.judge_fault = fault
    with lease(c):
        session = c.session()
        session.verify(c.candidates, c.provider)
        scored = c.scored()
        session.judge(scored, c.provider)
        assert session.global_judge["status"] == "uncertain" and scored[0]["quality_score"] == 80
        c.judge_fault = ""
        session.judge(c.scored(), c.provider)
        assert c.judge_calls == 1


def test_visual_bonus_cannot_promote_text_tier_or_override_c_rejection(round_context):
    c = round_context
    c.candidates *= 3
    c.candidates = [{**item, "source_id": f"candidate-{i}"} for i, item in enumerate(c.candidates)]
    with lease(c):
        session = c.session()
        session.verify(c.candidates, c.provider)
        scored = c.scored()
        for row, tier in zip(scored, ("A", "B", "C"), strict=True):
            row.update(quality_tier=tier, selected_by_default=tier == "A")
        session.judge(scored, c.provider)
        assert [r["quality_score"] for r in scored] == [88, 88, 80]
        assert [r["quality_tier"] for r in scored] == ["A", "B", "C"]
        assert [r["selected_by_default"] for r in scored] == [True, False, False]


def test_text_incomplete_never_receives_visual_bonus(round_context):
    c = round_context
    with lease(c):
        session = c.session()
        session.verify(c.candidates, c.provider)
        scored = c.scored()
        session.judge(scored, c.provider, text_complete=False)
        assert scored[0]["quality_score"] == 80 and c.judge_calls == 0


def test_operator_disabled_has_no_sampling_or_ai_calls(round_context, monkeypatch):
    c = round_context
    monkeypatch.setattr(visual, "settings", SimpleNamespace(ai_visual_enabled=False))
    with lease(c):
        session = c.session()
        session.verify(c.candidates, c.provider)
        session.judge(c.scored(), c.provider)
        assert session.metadata()["candidates"]["candidate-1"]["failure_reason"] == "operator_disabled"
    assert (c.provider.calls, c.judge_calls, c.samples) == (0, 0, 0)


def test_round_budget_survives_restart_and_only_reuses_completed_units(round_context, monkeypatch):
    c = round_context
    with lease(c):
        session = c.session()
        session.verify(c.candidates, c.provider)
        monkeypatch.setattr(visual.time, "time", lambda: session.deadline + 100)
        resumed = c.session()
        resumed.verify(c.candidates, c.provider)
        resumed.judge(c.scored(), c.provider)
        assert resumed.global_judge["status"] == "retryable_failed"
        assert "budget" in resumed.global_judge["reason"]
    assert (c.provider.calls, c.judge_calls, c.samples) == (1, 0, 1)


def test_candidate_budget_does_not_drop_unverified_text_candidates(round_context):
    c = round_context
    c.candidates = [{**c.candidates[0], "source_id": f"candidate-{i}"} for i in range(25)]
    with lease(c):
        session = c.session()
        session.verify(c.candidates, c.provider)
        assert len(session.results) == 25 and c.provider.calls == 20
        assert session.results["candidate-20"]["failure_reason"] == "candidate_budget_exhausted"


def test_changed_candidate_freeze_cannot_reuse_or_resend_visual(round_context):
    c = round_context
    with lease(c):
        session = c.session()
        session.verify(c.candidates, c.provider)
        c.candidates[0]["title"] = "其他输入"
        resumed = c.session()
        resumed.verify(c.candidates, c.provider)
        assert resumed.results["candidate-1"]["status"] == "unavailable"
    assert c.provider.calls == 1


def test_task_policy_update_is_explicit_and_does_not_rewrite_queued_job(round_context):
    c = round_context
    before = job_service.get_job(c.job["id"])["payload_json"]
    with pytest.raises(ValueError, match="后台作业"):
        update_task_visual(c.task_id, True)
    assert job_service.get_job(c.job["id"])["payload_json"] == before

    with lease(c):
        job_service.mark_job_completed(c.job["id"], {})
    update_task_visual(c.task_id, True)
    with get_connection() as connection:
        assert task_visual_policy(connection, c.task_id)["enabled"]
    new, _ = job_service.create_or_get_active_job(c.task_id, job_service.JOB_TYPE_AI_ANALYSIS, {"provider":"remote"})
    assert new["payload_json"]["generation_snapshot_v1"]["snapshot"]["visual_policy"]["enabled"]
    assert job_service.get_job(c.job["id"])["payload_json"] == before


@pytest.mark.parametrize("profile_id", ["interview_story", "knowledge_opinion"])
def test_shared_pipeline_commits_visual_evidence_with_run_and_resumes_without_ai(round_context, monkeypatch, tmp_path, profile_id):
    from app.services.ai import content_analyzer as content
    from app.services import ai_analysis_workflow_service as workflow
    from app.services.task_lifecycle_service import update_task_selection_settings
    from app.services.storage_service import get_artifact_paths
    from tests.test_content_analyzer import FakeProvider
    c = round_context
    with lease(c):
        job_service.mark_job_completed(c.job["id"], {})
    update_task_selection_settings(c.task_id, profile_id, 5)
    update_task_visual(c.task_id, True)
    with get_connection() as connection:
        connection.execute("UPDATE tasks SET status='pending_ai' WHERE id=?", (c.task_id,))
        connection.commit()
    paths = get_artifact_paths(c.task_id, c.task["task_dir_name"])
    # 旧 Job 测试 reload 配置；隔离夹具始终使用明确的真实任务目录映射。
    monkeypatch.setattr(workflow, "get_artifact_paths", lambda task_id: get_artifact_paths(task_id, c.task["task_dir_name"]))
    paths["transcript_path"].write_text("\n".join(f"| 00:{s//60:02d}:{s%60:02d} | 00:{(s+20)//60:02d}:{(s+20)%60:02d} | 完整经历和论据 |" for s in range(0,100,20)), encoding="utf-8")
    provider = FakeProvider()
    provider.generate_visual_json = c.provider.generate_visual_json
    provider.generate_visual_judgment_json = c.provider.generate_visual_judgment_json
    monkeypatch.setattr(content, "build_provider", lambda *_: provider)
    job, _ = workflow.queue_task_ai_analysis(c.task_id, provider="remote")
    c.job = job_service.claim_job(job["id"], "visual-integration")
    with lease(c):
        first = workflow.process_task_ai_analysis(c.task_id, provider="remote")
        run = first["analysis_run"]
        assert not run["analysis_incomplete"] and run["coverage_ratio"] == 1
        assert run["analysis_meta"]["visual_signal"]["verified_count"] == 1
        assert first["clips"][0]["quality_score"] == 99 and first["clips"][0]["text_quality_score"] == 90
        with get_connection() as connection:
            assert connection.execute("SELECT analysis_run_id FROM candidate_visual_evidence WHERE task_id=?", (c.task_id,)).fetchone()[0] == run["id"]
        c.path.unlink()
        assert workflow.process_task_ai_analysis(c.task_id, provider="remote")["analysis_run_id"] == run["id"]
    assert provider.calls == ["recall", "expansion", "global_judge"]
    assert c.provider.calls == 1 and c.judge_calls == 1
    with get_connection() as connection:
        connection.execute("DELETE FROM clip_candidates WHERE task_id=?", (c.task_id,))
        connection.commit()


def test_resume_rejects_changed_source_without_resending(round_context, monkeypatch):
    c = round_context
    with lease(c):
        c.session().verify(c.candidates, c.provider)
    takeover(c)
    monkeypatch.setattr(visual, "_current_source_hash", lambda task: "b"*64)
    with lease(c):
        resumed = c.session()
        resumed.verify(c.candidates, c.provider)
        assert resumed.metadata()["verified_count"] == 0
        assert "visual_source_changed" in resumed.results["candidate-1"]["failure_reason"]
    assert c.provider.calls == 1


def test_unconfirmed_global_process_pauses_and_persists_circuit(round_context):
    from app.services.ai.base import AIProviderError
    from app.services.managed_process_service import ProcessTerminationError
    c = round_context
    def unconfirmed(*args, **kwargs):
        raise AIProviderError("cannot stop", category="visual_process_unconfirmed", billing_uncertain=True)
    c.provider.generate_visual_judgment_json = unconfirmed
    with lease(c):
        session = c.session()
        session.verify(c.candidates, c.provider)
        scored = c.scored()
        with pytest.raises(ProcessTerminationError):
            session.judge(scored, c.provider)
        assert scored[0]["quality_score"] == 80
    with pytest.raises(ProcessTerminationError, match="visual_process_review_required"):
        visual.assert_visual_process_safe()
    takeover(c)
    with lease(c):
        with pytest.raises(ProcessTerminationError, match="visual_process_review_required"):
            c.session().verify(c.candidates, c.provider)
    from app.services import ai_analysis_workflow_service as workflow
    # 即使下一次关闭视觉，也不能绕过未确认停止的本地进程。
    with pytest.raises(ProcessTerminationError, match="visual_process_review_required"):
        workflow._analyze_with_provider(c.task_id, {"_visual_policy":{"enabled":False}}, {}, "remote", {})
    assert c.provider.calls == 1


def test_duplicate_visual_ids_degrade_without_breaking_text(round_context):
    c = round_context
    with lease(c):
        session = c.session()
        session.verify(c.candidates * 2, c.provider)
        scored = c.scored()
        session.judge(scored, c.provider)
    assert session.metadata()["status"] == "unavailable"
    assert scored[0]["quality_score"] == 80 and c.provider.calls == 0


def test_comedy_visual_judge_cannot_bonus_incomplete_text(monkeypatch, tmp_path):
    from app.services.ai import variety_comedy_analyzer as comedy
    from tests.test_variety_comedy_selection import _FakeComedyProvider, _time
    transcript = tmp_path / "transcript.md"
    transcript.write_text("\n".join(f"| {_time(s)} | {_time(s+10)} | 第 {s//10} 句对话 |" for s in range(0,240,10)), encoding="utf-8")
    monkeypatch.setattr(comedy, "build_provider", lambda _: _FakeComedyProvider())
    monkeypatch.setattr(comedy, "analyze_audio_reaction", lambda *a: {"available": False, "score":0})
    original = comedy._recall_moments
    def partially_invalid(*args, **kwargs):
        moments, failures, stats = original(*args, **kwargs)
        return moments, failures, {**stats, "invalid_item_count":1}
    monkeypatch.setattr(comedy, "_recall_moments", partially_invalid)
    calls = []
    class Session:
        def verify(self, candidates, provider):
            calls.append("verify")
        def judge(self, scored, provider, *, text_complete):
            assert not text_complete
            calls.append("incomplete")
    result = comedy.analyze_variety_comedy(comedy.ComedyAnalysisRequest(task_id="visual-comedy-invalid", transcript_path=transcript,
        audio_path=tmp_path/"missing.wav", candidate_pool_limit=12, final_clip_target=5,
        ai_preference="",provider_name="remote",feedback_context=[],visual_session=Session()))
    assert result.analysis_meta["analysis_incomplete"] and result.clips
    assert calls == ["verify", "incomplete"]
