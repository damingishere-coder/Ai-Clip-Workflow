import json

import pytest

from app.services import publish_service
from app.services.metadata_generator import MetadataGenerator
from app.services.publish_copy_rules import build_generated_douyin_publish_copy, validate_douyin_publish_copy
from app.services.publish_copy_style import clean_summary_draft, clip_dialogue
from app.services.publish_readiness import _content_issues


COPY = {"title": "自信刚上线，主持人就喊跳过", "description": "脾气最好的人，发言机会怎么最短", "tags": ["康熙来了", "造型师", "职场"]}


@pytest.fixture
def provider(monkeypatch):
    class Provider:
        name = "codex"

        def __init__(self):
            self.value = COPY.copy()
            self.prompt = ""

        def generate_json(self, prompt, retry_instruction=None):
            self.prompt = prompt
            return json.dumps(self.value, ensure_ascii=False)

    result = Provider()
    monkeypatch.setattr(publish_service, "build_provider", lambda *a, **k: result)
    monkeypatch.setattr("app.services.production_review_service.check_preparation", lambda *a, **k: None)
    monkeypatch.setattr("app.services.weekly_review_service.task_rules", lambda *a: {})
    return result


def test_ai_default_writes_a_hook_and_gets_dialogue_instead_of_editor_notes(provider, monkeypatch):
    monkeypatch.setattr(publish_service, "clip_dialogue", lambda _: "我脾气最好。跳过！")
    result = publish_service.generate_publish_metadata({"clip_title": "林业廷自称脾气最好", "clip_summary": "被主持人喊跳过"})
    assert result["source"].startswith("ai:")
    assert result["description"] == COPY["description"]
    assert result["tags"] == "康熙来了, 造型师, 职场"
    assert "我脾气最好。跳过！" in provider.prompt
    assert "不能复述标题" in provider.prompt
    assert "4～6" not in provider.prompt


@pytest.mark.parametrize("invalid", [
    {"description": "53秒。现场介绍刘珍担任国标评审"},
    {"description": "00:25:10原始开头介绍国标评审"},
    {"description": "据转写，舞蹈效果待核实"},
    {"description": COPY["title"]},
    {"tags": ["康熙来了", "国标"]},
    {"tags": ["康熙来了", "国标", "刘真", "综艺"]},
    {"tags": ["康熙来了", "53秒", "00"]},
    {"title": "很长" * 16},
    {"description": ""},
])
def test_bad_ai_copy_is_reported_as_a_draft(provider, invalid):
    provider.value.update(invalid)
    result = publish_service.generate_publish_metadata({"clip_title": "国标评审", "clip_summary": "老师分享舞蹈经历"})
    assert result["source"] == "rule"
    assert result["error"]


def test_short_punchline_is_not_padded_and_show_prefix_is_not_injected(provider):
    provider.value["description"] = "自信也有保质期"
    result = MetadataGenerator().generate({"task_name": "康熙来了", "clip_summary": "造型师被喊跳过"}, "douyin")
    assert result["caption"] == "自信也有保质期"
    assert result["title"] == COPY["title"]
    assert result["status"] == "READY"
    assert len(result["hashtags"]) == 3


def test_rule_draft_is_marked_for_editing(provider):
    result = MetadataGenerator(use_ai=False).generate({"clip_title": "国标评审", "clip_summary": "53秒。00:01:10原始开头是介绍。刘真分享舞蹈经历。实际效果待核实"}, "douyin")
    assert result["caption"] == "刘真分享舞蹈经历"
    assert result["status"] == "NEED_REVIEW"
    payload = json.loads(publish_service._publish_provider_payload({"source": "rule"}))
    issues = _content_issues({"provider_payload": payload}, "douyin", "local_browser")
    assert any(i["code"] == "metadata_needs_edit" for i in issues)
    manual = json.loads(publish_service._publish_provider_payload({"source": "manual"}, existing=payload))
    assert manual["metadata_review_required"] is False


def test_exactly_three_topics_allow_real_show_names_and_reject_duration():
    validate_douyin_publish_copy(COPY["title"], "自信也有保质期", COPY["tags"])
    for tags in (["康熙来了", "造型师"], ["康熙来了", "造型师", "职场", "访谈"], ["康熙来了", "53秒", "00"]):
        with pytest.raises(ValueError):
            validate_douyin_publish_copy(COPY["title"], COPY["description"], tags)
    assert build_generated_douyin_publish_copy(COPY["title"], "自信也有保质期", COPY["tags"])["description"] == "自信也有保质期"


def test_dialogue_uses_output_snapshot_bounds_and_keeps_ending(tmp_path, monkeypatch):
    path = tmp_path / "transcript.md"
    path.write_text("| 00:00 | 00:10 | 上一段的梗 |\n| 00:10 | 00:15 | 本片开头 |\n| 00:15 | 00:20 | 本片结尾 |\n| 00:20 | 00:30 | 下一段的梗 |", encoding="utf-8")
    monkeypatch.setattr("app.services.publish_copy_style.get_artifact_paths", lambda *a: {"transcript_path": path})
    text = clip_dialogue({"task_id": "fixture", "source_start_ms": 10000, "source_end_ms": 20000, "start_time": "00:00", "end_time": "00:30"})
    assert text == "本片开头\n本片结尾"
    assert clip_dialogue({"task_id": "fixture", "start_time": "invalid"}) == ""


def test_summary_cleanup_keeps_real_content():
    assert clean_summary_draft("53秒。00:01:10原始开头介绍。老师开口就夸她。文字无法确认笑声。") == "老师开口就夸她"


@pytest.mark.parametrize("status", ["PUBLISHING", "PUBLISHED", "NEED_REVIEW", "CANCELLED"])
def test_regeneration_does_not_touch_execution_or_history(status, monkeypatch):
    monkeypatch.setattr(publish_service, "get_publish_job", lambda _: {"status": status})
    with pytest.raises(ValueError, match="可以重写文案"):
        publish_service.regenerate_send_job_metadata("fixture")
