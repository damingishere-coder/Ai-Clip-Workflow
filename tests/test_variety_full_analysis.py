from dataclasses import replace
import json

import pytest

from app.services.ai import variety_full_analyzer as full
from app.services.ai.analysis_modes import FULL_MODE
from app.services.ai.base import AIProviderError
from app.services.ai.variety_comedy_analyzer import ComedyAnalysisRequest, analyze_variety_comedy
from app.services.content_profile_definitions import playback_comedy_profile
from tests.test_playback_production import candidate, judgment
from tests.test_kangxi_analysis_contracts import leased_task as _leased_task

leased_task = _leased_task


def item(**changes):
    value = {**candidate(), **judgment()}
    value.pop("duration_seconds")
    value.pop("audio_evidence")
    return {**value, **changes}


class Provider:
    name = "codex"

    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_json_with_schema(self, prompt, schema, retry_instruction=None):
        self.calls.append((prompt, schema))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def analysis_request(tmp_path, monkeypatch):
    path = tmp_path / "transcript.md"
    path.write_text("## 逐句时间戳原文\n" + "\n".join(
        f"| 00:{s//60:02}:{s%60:02} | 00:{(s+10)//60:02}:{(s+10)%60:02} | 原文标记{s} |"
        for s in range(0, 300, 10)), encoding="utf-8")
    monkeypatch.setattr(full, "analyze_audio_reaction", lambda *args: {"available": True, "score": 100})
    return ComedyAnalysisRequest(task_id="full-test", transcript_path=path, audio_path=tmp_path/"audio.wav",
        candidate_pool_limit=12, final_clip_target=5, ai_preference="完整回应", provider_name="codex",
        prompt_template="冻结偏好完整保留", profile=playback_comedy_profile(),
        feedback_context=[{"note": "冻结反馈标记"}], analysis_mode=FULL_MODE)


def run(monkeypatch, analysis_request, response):
    provider = Provider(response)
    monkeypatch.setattr(full, "build_provider", lambda *args: provider)
    result = analyze_variety_comedy(analysis_request)
    return result, provider


def test_full_single_call_preserves_every_row_and_current_formula(analysis_request, monkeypatch):
    with analysis_request.transcript_path.open('a', encoding='utf-8') as stream:
        stream.write('\n保留原文管线标记 | 全文尾注')
    result, provider = run(monkeypatch, analysis_request, json.dumps({"clips": [item()]}))
    assert len(provider.calls) == 1
    prompt, schema = provider.calls[0]
    for s in range(0, 300, 10):
        assert f"原文标记{s}" in prompt
    assert "冻结反馈标记" in prompt and "冻结偏好完整保留" in prompt
    assert '保留原文管线标记 | 全文尾注' in prompt
    assert schema == full.FULL_OUTPUT_SCHEMA
    assert result.clips[0].quality_score == 65
    assert result.clips[0].audio_reaction_score == 0
    assert result.clips[0].selected_by_default
    assert result.clips[0].duration_seconds == 50
    assert result.analysis_meta["expected_units"] == result.analysis_meta["completed_units"] == 1
    assert result.analysis_meta["submitted_row_count"] == 30
    assert result.analysis_meta["analysis_mode"] == FULL_MODE
    assert result.analysis_meta["submitted_last_time"] == "00:05:00"


def test_unconfirmed_gate_does_not_select_even_at_one_hundred(analysis_request, monkeypatch):
    bad = item(core_event_confirmed=False)
    for field in full._full_item["properties"]:
        if field.endswith("_score"):
            bad[field] = 100
    result, _ = run(monkeypatch, analysis_request, json.dumps({"clips": [bad]}))
    assert result.clips[0].quality_tier == "B"
    assert not result.clips[0].selected_by_default


@pytest.mark.parametrize('change', [
    {'model_call_count':True}, {'model_call_count':2}, {'submitted_row_count':0},
    {'submitted_row_count':None}, {'expected_units':18}, {'scoring_rules_version':'legacy'},
])
def test_full_cut_gate_rejects_inconsistent_execution_evidence(analysis_request, monkeypatch, change):
    from app.services.ai_analysis_workflow_service import validate_ai_analysis_meta_for_cut
    result, _ = run(monkeypatch, analysis_request, json.dumps({'clips':[item()]}))
    meta = {**result.analysis_meta, 'selection_profile':'variety_comedy'}
    assert not validate_ai_analysis_meta_for_cut(meta, 'variety_comedy')['analysis_incomplete']
    assert validate_ai_analysis_meta_for_cut({**meta, **change}, 'variety_comedy')['analysis_incomplete']


def test_duplicate_story_compared_locally_and_no_extra_model_request(analysis_request, monkeypatch):
    result, provider = run(monkeypatch, analysis_request, json.dumps({"clips": [item(), item(source_id="full_002")]}))
    assert len(provider.calls) == len(result.clips) == 1


@pytest.mark.parametrize("change", [
    {"hook_score": "70"}, {"humor_score": True}, {"hook_score": float("nan")},
    {"opening_supported": "true"}, {"start_time": "00:00:01"},
    {"end_time": "00:06:00"}, {"end_time": "00:00:30"},
    {"end_time": "00:03:00"}, {"key_moment_time": "00:00:50"}, {"gate_reason": ""},
])
def test_invalid_return_fails_entire_request_without_retry(analysis_request, monkeypatch, change):
    provider = Provider(json.dumps({"clips": [item(**change)]}))
    monkeypatch.setattr(full, "build_provider", lambda *args: provider)
    with pytest.raises(full.AIAnalysisError):
        analyze_variety_comedy(analysis_request)
    assert len(provider.calls) == 1


@pytest.mark.parametrize("response", ["invalid", '{"clips":[]}', json.dumps({"clips": [item(), item()]})])
def test_invalid_json_empty_or_repeated_ids_fail_closed(analysis_request, monkeypatch, response):
    provider = Provider(response)
    monkeypatch.setattr(full, "build_provider", lambda *args: provider)
    with pytest.raises(full.AIAnalysisError):
        analyze_variety_comedy(analysis_request)
    assert len(provider.calls) == 1


@pytest.mark.parametrize("uncertain", [True, False])
def test_provider_error_never_repeats_full_request(analysis_request, monkeypatch, uncertain):
    provider = Provider(AIProviderError("timeout", safe_to_retry=not uncertain, billing_uncertain=uncertain))
    monkeypatch.setattr(full, "build_provider", lambda *args: provider)
    with pytest.raises(full.AIAnalysisError):
        analyze_variety_comedy(analysis_request)
    assert len(provider.calls) == 1


def test_success_checkpoint_reuses_raw_validated_response(analysis_request, monkeypatch, leased_task):
    task_id, _ = leased_task
    req = replace(analysis_request, task_id=task_id)
    provider = Provider(json.dumps({"clips": [item()]}))
    monkeypatch.setattr(full, "build_provider", lambda *args: provider)
    first, second = analyze_variety_comedy(req), analyze_variety_comedy(req)
    assert len(provider.calls) == 1
    assert not first.analysis_meta["checkpoint_reused"] and second.analysis_meta["checkpoint_reused"]
    assert first.analysis_meta["model_call_seconds"] == second.analysis_meta["model_call_seconds"]


def test_uncertain_checkpoint_never_replays_without_new_confirmation(analysis_request, monkeypatch, leased_task):
    task_id, _ = leased_task
    provider = Provider("invalid")
    monkeypatch.setattr(full, "build_provider", lambda *args: provider)
    for _ in range(2):
        with pytest.raises(full.AIAnalysisError):
            analyze_variety_comedy(replace(analysis_request, task_id=task_id))
    assert len(provider.calls) == 1


def test_oversize_or_wrong_provider_does_not_call_model(analysis_request, monkeypatch):
    provider = Provider(json.dumps({"clips": [item()]}))
    monkeypatch.setattr(full, "build_provider", lambda *args: provider)
    with pytest.raises(ValueError):
        analyze_variety_comedy(replace(analysis_request, provider_name="remote"))
    monkeypatch.setattr(full, "MAX_FULL_PROMPT_CHARS", 10)
    with pytest.raises(full.AIAnalysisError, match="超过"):
        analyze_variety_comedy(analysis_request)
    assert not provider.calls
