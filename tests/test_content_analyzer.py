"""Business boundaries and recovery; no network or actual model invocation."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest

from app.db.database import get_connection, init_db
from app.models.task import TaskCreate, TaskStatus
from app.services.ai import content_analyzer as content
from app.services.content_profile_definitions import interview_profile
from app.services import job_service, task_service, ai_analysis_workflow_service as workflow
from app.services.task_lifecycle_service import create_task_record, update_task_status
from app.services.ai_prompt_preset_service import get_task_ai_prompt_snapshot
from app.services.storage_service import get_artifact_paths


class FakeProvider:
    name = "remote"

    def __init__(self, fault=""):
        self.calls = []
        self.fault = fault

    def generate_json_with_schema(self, prompt, output_schema, **kwargs):
        stage = prompt.split("\n阶段：")[1].split("\n")[0]
        self.calls.append(stage)
        if self.fault == stage:
            return "invalid json"
        if stage == "recall":
            result = {"moments": [{"key_seconds": 40, "reason": "转折"}]}
        elif stage == "expansion":
            result = {"candidates": [{"start_seconds": 0, "end_seconds": 100, "title": "经历的转折",
                       "summary": "从困难到解决", "topic": "成长", "hook_type": "人物自述"}], "reason": "故事完整"}
            if self.fault == "boundary":
                result["candidates"][0]["end_seconds"] = 300
        else:
            dimensions = output_schema["$defs"]["Verdict"]["properties"]["scores"]
            assert dimensions["additionalProperties"] is False
            scores = {key: 90.0 for key in dimensions["required"]}
            if self.fault == "scores":
                scores.pop("story_value")
            result = {"verdicts": [{"source_id": "moment-0", "scores": scores, "reason": "保留完整经历"}]}
        return json.dumps(result, ensure_ascii=False)


@pytest.fixture
def request_and_provider(tmp_path, monkeypatch):
    path = tmp_path / "transcript.md"
    path.write_text("\n".join(f"| 00:00:{i:02d} | 00:{(i+20)//60:02d}:{(i+20)%60:02d} | 人物讲述真实经历 |" for i in range(0, 60, 20))
                    + "\n| 00:01:00 | 00:01:20 | 克服困难 |\n| 00:01:20 | 00:01:40 | 完整结果 |", encoding="utf-8")
    provider = FakeProvider()
    monkeypatch.setattr(content, "build_provider", lambda *_: provider)
    return content.ContentAnalysisRequest("isolated", path, interview_profile(), "remote", "访谈规则"), provider


def test_story_score_and_hard_gates():
    p = interview_profile()
    scores = dict(zip([d.id for d in p.scoring.dimensions], [90, 80, 70, 60, 50, 40], strict=True))
    assert content.score_candidate(p, scores) == (72.5, "B")
    assert content.score_candidate(p, {d.id: 78.0 for d in p.scoring.dimensions}) == (78, "A")
    scores = {d.id: 100.0 for d in p.scoring.dimensions}
    scores["story_value"] = 69
    assert content.score_candidate(p, scores)[1] == "B"
    assert p.scoring.audio_weight == p.scoring.visual_weight == 0


def test_shared_story_pipeline_has_profile_evidence_and_valid_cut_metadata(request_and_provider):
    request, provider = request_and_provider
    result = content.analyze_content(request)
    assert provider.calls == ["recall", "expansion", "global_judge"]
    assert len(result.clips) == 1
    clip = result.clips[0]
    assert clip.selected_by_default and clip.quality_tier == "A"
    assert clip.humor_score == 0
    assert clip.topic_key == "成长" and clip.key_moment_time == "00:00:40"
    assert clip.quality_evidence["score_breakdown"]["story_value"] == 90
    observation = result.analysis_meta["review_observations"]["items"][0]
    assert observation["key"] == "clip:clip_001" and observation["initial_recommended"] is True
    assert observation["score_dimensions"]["story_value"] == 90
    assert workflow.validate_ai_analysis_meta_for_cut(result.analysis_meta, "interview_story")["coverage_percent"] == 100


def test_content_c_diagnostic_is_kept_without_becoming_production_candidate(request_and_provider, monkeypatch):
    request, provider = request_and_provider
    monkeypatch.setattr(content, "score_candidate", lambda *_: (50, "C"))
    result = content.analyze_content(request)
    assert not result.clips
    row = result.analysis_meta["review_observations"]["items"][0]
    assert row["quality_tier"] == "C" and row["in_review_pool"] is False
    assert row["initial_recommended"] is False
    assert provider.calls == ["recall", "expansion", "global_judge"]


@pytest.mark.parametrize("fault", ["recall", "expansion", "boundary", "global_judge", "scores"])
def test_invalid_required_units_are_incomplete_and_not_selected(request_and_provider, fault):
    request, provider = request_and_provider
    provider.fault = fault
    result = content.analyze_content(request)
    assert result.analysis_meta["analysis_incomplete"]
    assert result.analysis_meta["failed_units"] == 1
    assert not result.clips
    assert result.analysis_meta["failed_stages"][0]["status"] == "uncertain"


def test_window_budget_covers_every_row(request_and_provider):
    request, _ = request_and_provider
    rows = content._extract_transcript_rows(content._read_transcript(request.transcript_path))
    policy = request.profile.recall.windows[0].model_copy(update={"seconds": 40, "overlap_seconds": 20})
    windows = content.build_content_windows(rows, policy)
    assert {r for window in windows for r in window} == set(rows)
    assert len(windows) == 4
    with pytest.raises(content.AIAnalysisError, match="预算"):
        content.build_content_windows(rows, policy.model_copy(update={"char_budget": 1}))


@pytest.fixture
def story_task(request):
    task_id = "story-" + uuid4().hex[:12]
    create_task_record(TaskCreate(task_name="内容隔离", selection_profile=getattr(request, "param", "interview_story")), task_id=task_id)
    yield task_id
    with get_connection() as c:
        for table in ("workflow_jobs", "clip_candidates", "ai_analysis_runs", "task_generation_rules"):
            c.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id,))
        c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        c.commit()


@pytest.mark.parametrize("story_task,profile_id,preset,dimension,label,rules", [
    ("interview_story", "interview_story", "profile_interview_v1", "story_value", "故事价值", "interview-v1"),
    ("knowledge_opinion", "knowledge_opinion", "profile_knowledge_v1", "knowledge_value", "知识与观点价值", "knowledge-v1"),
], indirect=["story_task"])
def test_story_task_restart_and_real_run_round_trip(story_task, request_and_provider, profile_id, preset, dimension, label, rules):
    request, provider = request_and_provider
    init_db()
    assert task_service.get_task(story_task, include_video_probe=False)["selection_profile"] == profile_id
    prompt = get_task_ai_prompt_snapshot(story_task)
    assert prompt["id"] == preset and prompt["prompt_version_id"]
    paths = get_artifact_paths(story_task)
    paths["transcript_path"].write_text(request.transcript_path.read_text(encoding="utf-8"), encoding="utf-8")
    update_task_status(story_task, TaskStatus.pending_ai)
    job, _ = workflow.queue_task_ai_analysis(story_task, provider="remote")
    claimed = job_service.claim_job(job["id"], "story-worker")
    with job_service.job_lease_context(job["id"], "story-worker", claimed["lease_token"]):
        workflow.process_task_ai_analysis(story_task, provider="remote")
    clips = task_service.list_clip_candidates(story_task)
    assert clips[0]["quality_evidence"]["score_breakdown"][dimension] == 90
    run = task_service.get_latest_ai_analysis_run(story_task)
    assert run["content_profile_version_id"] == prompt["content_profile_version_id"]
    assert run["clips"][0]["quality_evidence"]["rules_version"] == rules
    from fastapi.testclient import TestClient
    from app.main import app
    response = TestClient(app).get(f"/tasks/{story_task}/clips/review")
    assert response.status_code == 200
    assert label in response.text and "标题适配" in response.text
    # Historical comedy also has score_breakdown but no dimension-name map.
    # Keep its existing three labels instead of displaying internal English keys.
    with get_connection() as c:
        c.execute("UPDATE clip_candidates SET quality_evidence_json=? WHERE task_id=?",
                  (json.dumps({"score_breakdown": {"humor": 90, "text_quality": 85}}), story_task))
        c.commit()
    response = TestClient(app).get(f"/tasks/{story_task}/clips/review")
    assert "笑点闭环" in response.text and "音频反应" in response.text
    assert "</strong>humor</span>" not in response.text


def test_checkpoint_reuses_success_and_never_reissues_uncertain(story_task, request_and_provider):
    request, provider = request_and_provider
    request = replace(request, task_id=story_task)
    job = job_service.create_job(story_task, job_service.JOB_TYPE_AI_ANALYSIS, {"provider": "remote"})
    claimed = job_service.claim_job(job["id"], "story-worker")
    provider.fault = "global_judge"
    with job_service.job_lease_context(job["id"], "story-worker", claimed["lease_token"]):
        result = content.analyze_content(request)
        assert result.analysis_meta["quality_degraded"]
        first_calls = list(provider.calls)
        content.analyze_content(request)
    assert provider.calls == first_calls == ["recall", "expansion", "global_judge"]


def test_story_uses_final_target_without_changing_general():
    from app.services.pipeline_engine import PipelineEngine
    engine = PipelineEngine()
    assert engine._resolve_target_count({"selection_profile": "interview_story", "final_clip_target": 2, "candidate_clip_count": 12}, {}) == 2
    assert engine._resolve_target_count({"selection_profile": "general", "final_clip_target": 2, "candidate_clip_count": 12}, {}) == 12


def test_story_candidate_limit_rejected_before_creation_and_update(story_task):
    from app.services.task_lifecycle_service import update_task_candidate_clip_count
    with pytest.raises(ValueError, match="候选池"):
        create_task_record(TaskCreate(task_name="超限", selection_profile="interview_story", candidate_clip_count=50))
    with pytest.raises(ValueError, match="候选池"):
        update_task_candidate_clip_count(story_task, 50)
    assert task_service.get_task(story_task, include_video_probe=False)["candidate_clip_count"] == 12


def test_explicit_profile_change_preserves_users_prompt(story_task):
    from app.services.ai_prompt_preset_service import update_task_ai_prompt_preset
    from app.services.task_lifecycle_service import update_task_selection_settings
    update_task_ai_prompt_preset(story_task, "preset_002")
    update_task_selection_settings(story_task, "general", 5)
    update_task_selection_settings(story_task, "interview_story", 5)
    assert get_task_ai_prompt_snapshot(story_task)["id"] == "preset_002"


def test_knowledge_uses_same_pipeline_with_own_dimensions(request_and_provider):
    from app.services.content_profile_definitions import knowledge_profile
    request, provider = request_and_provider
    result = content.analyze_content(replace(request, profile=knowledge_profile()))
    assert provider.calls == ["recall", "expansion", "global_judge"]
    scores = result.clips[0].quality_evidence["score_breakdown"]
    assert set(scores) == {"knowledge_value", "completeness", "evidence", "hook", "contrast", "title_fit"}
    assert result.clips[0].selected_by_default
    assert result.clips[0].humor_score == 0
    assert result.analysis_meta["effective_limits"]["max_duration_seconds"] == 180
    assert workflow.validate_ai_analysis_meta_for_cut(result.analysis_meta, "knowledge_opinion")["coverage_percent"] == 100
