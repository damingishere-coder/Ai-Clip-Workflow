import json
from types import SimpleNamespace

import pytest

from app.db.database import get_connection
from app.models.task import TaskCreate
from app.services import playback_activation_service as activation
from app.services import content_profile_service as profiles
from app.services.ai_prompt_preset_service import get_task_ai_prompt_snapshot_with_connection
from app.services.task_lifecycle_service import insert_task_record_with_connection
from app.services.ai import variety_comedy_analyzer as analyzer
from app.services.ai.playback_comedy_policy import GATES, SCORE_KEYS, playback_rules
from app.services.content_profile_definitions import playback_comedy_profile


@pytest.fixture
def transaction():
    # Roll back all rows, including immutable versions, without disabling triggers.
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        finally:
            connection.rollback()


def add_task(c, key):
    insert_task_record_with_connection(c, TaskCreate(task_name=key, selection_profile="variety_comedy"), task_id=key, task_dir_name=key)
    return get_task_ai_prompt_snapshot_with_connection(c, key)


def activate(c):
    return activation.apply(c, expected_sha256=activation.preview(c)["current_sha256"])


def test_activation_freezes_new_tasks_keeps_old_tasks_and_rolls_back(transaction):
    c = transaction
    old = add_task(c, "playback-old")
    old_tables = {table: [tuple(r) for r in c.execute(f'SELECT * FROM "{table}" ORDER BY rowid')] for table in
                  ("tasks", "task_generation_rules", "workflow_jobs", "ai_analysis_runs", "publish_jobs")}
    result = activate(c)
    assert result["after"]["already_active"] is True
    for table, values in old_tables.items():
        assert values == [tuple(r) for r in c.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
    assert get_task_ai_prompt_snapshot_with_connection(c, "playback-old") == old
    new = add_task(c, "playback-new")
    assert new["content_profile_sha256"] == playback_comedy_profile().content_hash()
    assert new["id"] == playback_comedy_profile().prompt_preset_id
    assert new["prompt_text"] == playback_rules()
    count = c.execute("SELECT COUNT(*) FROM content_profile_versions").fetchone()[0]
    activate(c)
    assert c.execute("SELECT COUNT(*) FROM content_profile_versions").fetchone()[0] == count
    activation.apply(c, expected_sha256=new["content_profile_sha256"], target="legacy")
    assert get_task_ai_prompt_snapshot_with_connection(c, "playback-new") == new
    assert add_task(c, "playback-after-rollback")["content_profile_sha256"] == old["content_profile_sha256"]


def test_stale_activation_and_edited_dedicated_prompt_are_not_overwritten(transaction):
    c = transaction
    with pytest.raises(ValueError, match="已改变"):
        activation.apply(c, expected_sha256="wrong")
    activate(c)
    c.execute("UPDATE ai_prompt_presets SET prompt_text='用户修改' WHERE id=?", (playback_comedy_profile().prompt_preset_id,))
    with pytest.raises(ValueError, match="不能覆盖"):
        activate(c)
    assert c.execute("SELECT prompt_text FROM ai_prompt_presets WHERE id=?", (playback_comedy_profile().prompt_preset_id,)).fetchone()[0] == "用户修改"


def test_legacy_unknown_task_new_job_uses_legacy_not_active_profile(transaction):
    c = transaction
    old = add_task(c, "playback-unknown")
    c.execute("UPDATE task_generation_rules SET content_profile_version_id=NULL,content_profile_sha256=NULL,content_profile_json=NULL WHERE task_id='playback-unknown'")
    activate(c)
    frozen = profiles.freeze_new_job_payload(c, "playback-unknown", "ai_analysis", {})[profiles.JOB_SNAPSHOT_KEY]["snapshot"]
    assert frozen["prompt"]["content_profile_sha256"] == old["content_profile_sha256"]
    assert profiles.read_task_profile(c, "playback-unknown") == {}


def candidate():
    return {"source_id": "w001_m01", "title": "具体问题与回应", "start_time": "00:00:00", "end_time": "00:00:50",
            "duration_seconds": 50, "key_moment_time": "00:00:20", "topic_key": "事件一", "summary": "具体事件",
            "highlight_reason": "问题后给出解释", "arc_structure": "问题→解释→回应", "suggested_editing": "自然边界",
            **dict.fromkeys(SCORE_KEYS, 70), "humor_score": 20,
            "audio_evidence": {"available": True, "score": 100}}


def judgment():
    return {**{k: v for k, v in candidate().items() if k in SCORE_KEYS},
            "source_id": "w001_m01", "title": "具体问题与回应", "topic_key": "事件一", "arc_structure": "问题→解释→回应",
            "why_selected": "00:00问题，00:20解释，00:45回应", "rejection_reason": "",
            **dict.fromkeys(GATES, True), "gate_reason": "开头问题明确；中间解释变化；结尾回应完整且未截断澄清",
            "av_uncertainty": "核心事件有文字证据；语气待人工看成片"}


def test_new_formula_and_evidence_gates_do_not_reuse_old_threshold_or_audio_bonus():
    c, j, p = candidate(), judgment(), playback_comedy_profile()
    fresh = analyzer.score_comedy_candidate(c, j, policy=p)
    assert fresh["quality_score"] == 65  # all 70 except humor 20 weighted at 10%
    assert fresh["quality_tier"] == "A"
    assert analyzer.score_comedy_candidate(c, j)["quality_tier"] == "B"
    j["core_event_confirmed"] = False
    j.update(dict.fromkeys(SCORE_KEYS, 100))
    assert analyzer.score_comedy_candidate(c, j, policy=p)["quality_tier"] == "B"
    assert analyzer.score_comedy_candidate(c, {}, policy=p)["quality_tier"] == "B"


@pytest.mark.parametrize("bad", [None, "true", 1])
def test_playback_judge_schema_rejects_missing_or_untyped_evidence(bad):
    j = judgment()
    j["meaning_preserved"] = bad
    with pytest.raises(analyzer.AIAnalysisError):
        analyzer._validate_payload({"ranked_clips": [j]}, expected_key="ranked_clips", output_schema=analyzer.PLAYBACK_JUDGE_OUTPUT_SCHEMA)


def test_new_boundaries_preserve_fifty_seconds_and_reject_padding_or_truncation():
    rows = [analyzer.TranscriptRow(start_time=f"00:00:{x:02}", end_time=f"00:{(x+10)//60:02}:{(x+10)%60:02}",
                                  start_seconds=x, end_seconds=x+10, text="对话") for x in range(0, 60, 10)]
    p = playback_comedy_profile()
    assert analyzer.normalize_clip_bounds(0, 50, 20, rows, policy=p) == (0, 50)
    assert analyzer.normalize_clip_bounds(0, 50, 20, rows) == (0, 60)
    assert analyzer.normalize_clip_bounds(0, 30, 20, rows, policy=p) is None
    assert analyzer.normalize_clip_bounds(0, 200, 20, rows, policy=p) is None


def test_invalid_high_score_overlap_cannot_displace_eligible_boundary():
    eligible = analyzer.score_comedy_candidate(candidate(), judgment(), policy=playback_comedy_profile())
    bad = {**eligible, "source_id": "other", "quality_tier": "B", "quality_score": 99}
    assert analyzer.dedupe_scored_candidates([bad, eligible], policy=playback_comedy_profile()) == [eligible]


@pytest.mark.parametrize("passed", [True, False])
def test_real_dispatch_and_three_stages_use_snapshot_new_rules(monkeypatch, tmp_path, passed):
    from app.services.ai_analysis_workflow_service import _analyze_with_profile
    transcript = tmp_path / "transcript.md"
    transcript.write_text("| 开始 | 结束 | 原文 |\n|---|---|---|\n" + "\n".join(
        f"| 00:00:{x:02} | 00:{(x+10)//60:02}:{(x+10)%60:02} | 第{x}句 |" for x in range(0, 60, 10)), encoding="utf-8")
    prompts = []
    fingerprints = []
    class Provider:
        def generate_json(self, prompt, **kwargs):
            prompts.append(prompt)
            assert "原始开头" in prompt and "25%" in prompt
            if "只宽召回" in prompt:
                return json.dumps({"moments": [{"key_time": "00:00:20", "title": "事件", "topic_key": "事件一", "humor_reason": "具体处境", "recall_score": 90}]})
            if "围绕每个召回事件" in prompt:
                clip = {k: v for k, v in candidate().items() if k not in {"duration_seconds", "audio_evidence"}}
                return json.dumps({"clips": [clip]})
            j = judgment()
            j["core_event_confirmed"] = passed
            assert "transcript_evidence" in prompt and "第0句" in prompt
            return json.dumps({"ranked_clips": [j]})
    def execute(**kwargs):
        fingerprints.append(kwargs["input_fingerprint"])
        payload = kwargs["operation"]()
        if kwargs.get("validate_payload"):
            kwargs["validate_payload"](payload)
        return SimpleNamespace(status="completed", payload=payload)
    monkeypatch.setattr(analyzer, "build_provider", lambda _: Provider())
    monkeypatch.setattr(analyzer, "execute_checkpointed_ai_unit", execute)
    monkeypatch.setattr(analyzer, "analyze_audio_reaction", lambda *a: {"available": True, "score": 100})
    monkeypatch.setattr("app.services.ai_analysis_workflow_service.append_task_log", lambda *a: None)
    p = playback_comedy_profile()
    task = {"selection_profile": "variety_comedy", "candidate_clip_count": 12, "final_clip_target": 5, "ai_preference": "", "_analysis_feedback_context": []}
    snapshot = {"name": "播放目标", "slot": 6, "prompt_text": playback_rules(), "content_profile_json": p.canonical_json(), "content_profile_sha256": p.content_hash()}
    result = _analyze_with_profile("mock-only", task, {"transcript_path": transcript, "audio_path": tmp_path / "none.wav"}, "remote", snapshot)
    assert len(prompts) == 3 and len(set(fingerprints)) == 1
    assert len(result.clips) == 1 and result.clips[0].duration_seconds == 50
    assert result.clips[0].selected_by_default is passed
    assert result.clips[0].quality_score == 65
    assert result.analysis_meta["analysis_incomplete"] is False
    assert result.analysis_meta["scoring_profile_sha256"] == p.content_hash()
    assert result.analysis_meta["recommendation_mode"] == "evidence_ranked"
