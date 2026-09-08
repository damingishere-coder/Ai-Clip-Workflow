"""真实故障形状的隔离回归：严格输出、成功单元复用、提示词归档。"""

from copy import deepcopy
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import prompt_archive_migration
from app.db.database import get_connection, init_db
from app.main import app
from app.models.task import AIPromptPresetUpdate
from app.services import ai_prompt_preset_service as presets, job_service
from app.services.ai import variety_comedy_analyzer as comedy, unit_checkpoint
from app.services.ai.analysis_status import incomplete_analysis_message
from app.services.ai_retry_service import _checkpoint_for_uncertain_resume


def clip(key="ranked_clips"):
    item = {
        "source_id": "candidate-a", "title": "具体标题", "topic_key": "单一话题",
        "arc_structure": "提问→回答→反应", "why_selected": "完整互动", "rejection_reason": "",
        **{field: 80 for field in comedy.SCORE_FIELDS},
    }
    if key == "clips":
        item.update(start_time="00:00:00", end_time="00:01:00", key_moment_time="00:00:30",
                    summary="具体事件", highlight_reason="反差", suggested_editing="连续截取")
    return item


class Provider:
    def __init__(self, raw):
        self.raw = raw
        self.schemas = []

    def generate_json_with_schema(self, prompt, output_schema, retry_instruction=None):
        self.schemas.append(output_schema)
        return self.raw


def generate(provider, key="ranked_clips"):
    return comedy._generate_payload(
        provider, "test", expected_key=key, known_ids={"candidate-a"}, require_all=key == "ranked_clips",
        output_schema=comedy.JUDGE_OUTPUT_SCHEMA if key == "ranked_clips" else comedy.EXPANSION_OUTPUT_SCHEMA,
    )


@pytest.mark.parametrize("key", ["clips", "ranked_clips"])
@pytest.mark.parametrize("value", ["forty", "70", True, None, -1, 101, float("nan"), float("inf")])
def test_invalid_scores_never_become_valid_results(key, value):
    item = clip(key)
    item["hook_score"] = value
    provider = Provider(json.dumps({key: [item]}))
    with pytest.raises(comedy.AIAnalysisError, match="hook_score"):
        generate(provider, key)
    assert len(provider.schemas) == 1


@pytest.mark.parametrize("key", ["clips", "ranked_clips"])
def test_complete_fields_and_numeric_scores_are_required(key):
    item = clip(key)
    provider = Provider(json.dumps({key: [item]}))
    assert generate(provider, key)[key][0]["hook_score"] == 80
    for field in provider.schemas[0]["properties"][key]["items"]["required"]:
        incomplete = dict(item)
        del incomplete[field]
        with pytest.raises(comedy.AIAnalysisError):
            generate(Provider(json.dumps({key: [incomplete]})), key)


@pytest.mark.parametrize("items", [[], [dict(clip(), source_id="foreign")], [clip(), clip()]])
def test_final_judge_requires_exactly_one_of_each_candidate(items):
    with pytest.raises(comedy.AIAnalysisError):
        generate(Provider(json.dumps({"ranked_clips": items})))


@pytest.fixture
def leased_task():
    task_id = "test-kangxi-contracts"
    with get_connection() as connection:
        connection.execute("INSERT INTO tasks(id,task_name,created_at,updated_at,ai_prompt_preset_id) VALUES(?,?, '2026-09-08','2026-09-08','preset_001')", (task_id, task_id))
        connection.commit()
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AUTO_PIPELINE, payload={})
    claimed = job_service.claim_job(job["id"], "contract-test")
    with job_service.job_lease_context(job["id"], "contract-test", claimed["lease_token"]):
        yield task_id, job["id"]
    with get_connection() as connection:
        connection.execute("DELETE FROM workflow_jobs WHERE task_id=?", (task_id,))
        connection.execute("DELETE FROM ai_analysis_runs WHERE task_id=?", (task_id,))
        connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        connection.commit()


def test_18_successful_units_survive_invalid_json_and_only_judge_is_retried(leased_task):
    task_id, job_id = leased_task
    provider = Provider(json.dumps({"ranked_clips": [clip()]}).replace('"hook_score": 80', '"hook_score": forty'))
    calls = []

    def run():
        for index in range(18):
            def success(index=index):
                calls.append(index)
                return {"value": index}
            assert unit_checkpoint.execute_checkpointed_ai_unit(
                task_id=task_id, namespace="successful", input_fingerprint="same-input",
                unit_id=str(index), request_fingerprint=str(index), operation=success,
            ).status == "completed"
        return unit_checkpoint.execute_checkpointed_ai_unit(
            task_id=task_id, namespace="variety_global_judge", input_fingerprint="same-input",
            unit_id="judge_001", request_fingerprint="same-candidates", operation=lambda: generate(provider),
        )

    assert run().status == "uncertain"
    assert run().status == "uncertain"
    assert len(provider.schemas) == 1  # 未确认时不能自动再次调用
    cp = job_service.get_job(job_id)["checkpoint_json"]
    cp.update(steps={"AI_ANALYZING": {"state": "failed"}, "PREPARING_SOURCE": {"state": "succeeded"},
                     "TRANSCRIBING": {"state": "succeeded"}},
              completed_steps=["PREPARING_SOURCE", "TRANSCRIBING"], current_step="AI_ANALYZING", start_step="PREPARING_SOURCE")
    preserved = deepcopy(cp["_ai_analysis_units_v1"]["namespaces"]["successful"])
    authorized = _checkpoint_for_uncertain_resume(cp, authorized_at="2026-09-08T12:00:00+08:00")
    assert authorized["_ai_analysis_units_v1"]["namespaces"]["successful"] == preserved
    with get_connection() as connection:
        connection.execute("UPDATE workflow_jobs SET checkpoint_json=? WHERE id=?", (json.dumps(authorized), job_id))
        connection.commit()
    provider.raw = json.dumps({"ranked_clips": [clip()]})
    assert run().status == "completed"
    assert len(provider.schemas) == 2
    assert calls == list(range(18))


def test_legacy_cached_invalid_score_is_marked_uncertain_without_model_call(leased_task):
    task_id, job_id = leased_task
    invalid = {"ranked_clips": [dict(clip(), hook_score="forty")]}
    kwargs = dict(task_id=task_id, namespace="variety_global_judge", input_fingerprint="same",
                  unit_id="judge_001", request_fingerprint="same", operation=lambda: invalid)
    assert unit_checkpoint.execute_checkpointed_ai_unit(**kwargs).status == "completed"
    result = unit_checkpoint.execute_checkpointed_ai_unit(
        **{**kwargs, "operation": lambda: pytest.fail("不能重发已缓存请求")},
        validate_payload=lambda payload: comedy._validate_payload(payload, expected_key="ranked_clips", known_ids={"candidate-a"}, require_all=True),
    )
    assert result.status == "uncertain"
    stored = job_service.get_job(job_id)["checkpoint_json"]["_ai_analysis_units_v1"]["namespaces"]["variety_global_judge"]["units"]["judge_001"]
    assert stored["status"] == "uncertain"
    assert "forty" in stored["result_json"]  # 原始证据仍保留


def test_archive_migration_preserves_all_prompt_content_and_is_idempotent():
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE ai_prompt_presets(id TEXT, prompt_text TEXT, is_default INTEGER)")
        for index in range(1, 5):
            connection.execute("INSERT INTO ai_prompt_presets VALUES (?,?,?)", (f"preset_{index:03d}", f"人工规则{index}", int(index == 1)))
        before = connection.execute("SELECT * FROM ai_prompt_presets").fetchall()
        prompt_archive_migration.apply(connection)
        prompt_archive_migration.apply(connection)
        prompt_archive_migration.verify(connection)
        assert connection.execute("SELECT id,prompt_text,is_default FROM ai_prompt_presets").fetchall() == before
        assert connection.execute("SELECT is_archived FROM ai_prompt_presets ORDER BY id").fetchall() == [(0,), (0,), (0,), (1,)]


def test_archived_prompt_hidden_readable_but_not_editable_or_bindable(leased_task):
    task_id, _ = leased_task
    old = presets.get_ai_prompt_preset("preset_004")
    others = [presets.get_ai_prompt_preset(f"preset_{index:03d}") for index in (2, 3)]
    init_db()
    assert presets.get_ai_prompt_preset("preset_004") == old
    assert [presets.get_ai_prompt_preset(f"preset_{index:03d}") for index in (2, 3)] == others
    with TestClient(app) as client:
        assert "preset_004" not in {r["id"] for r in client.get("/api/ai-prompt-presets").json()}
        assert "preset_004" in {r["id"] for r in client.get("/api/ai-prompt-presets?include_archived=true").json()}
    with pytest.raises(ValueError, match="归档"):
        presets.update_task_ai_prompt_preset(task_id, "preset_004")
    with pytest.raises(ValueError, match="归档"):
        presets.update_ai_prompt_preset("preset_004", AIPromptPresetUpdate(name="覆盖", prompt_text="不应写入"))
    with get_connection() as connection:
        connection.execute("UPDATE tasks SET ai_prompt_preset_id='preset_004' WHERE id=?", (task_id,))
        connection.commit()
    assert presets.get_task_ai_prompt_snapshot(task_id)["id"] == "preset_004"
    with TestClient(app) as client:
        page = client.get(f"/tasks/{task_id}")
    assert "历史绑定（已归档）" in page.text
    assert 'name="ai_prompt_preset_id" value="preset_004"' not in page.text


def test_weekly_rules_reach_all_stages_without_truncation():
    prompt = presets.get_ai_prompt_preset("preset_001")["prompt_text"]
    assert "【已确认的周复盘补充规则】" not in prompt
    prompt += "\n【已确认的周复盘补充规则】\n经对照审片确认的单项补充。"
    long_prompt = prompt + "\n" + "保留完整规则。" * 500 + "末尾边界规则不能丢失"
    preference = comedy._preference_summary(long_prompt, "")
    window = comedy.ComedyTranscriptWindow(1, 1, 0, 60, (), "转写原文")
    for text in (comedy._recall_prompt(window, preference), comedy._expansion_prompt([], preference), comedy._judge_prompt([], preference, [])):
        assert "【已确认的周复盘补充规则】" in text
        assert "末尾边界规则不能丢失" in text


def test_failure_explanation_names_final_judge_and_preserved_units():
    from app.services.ai_analysis_workflow_service import _analysis_run_row_to_dict
    meta = {"completed_units": 18, "expected_units": 19, "coverage_percent": 94.74,
        "analysis_incomplete": True, "failed_stages": [{"stage": "global_judge", "message": "[invalid_response_json]"}]}
    text = incomplete_analysis_message(meta)
    assert "最终评审格式错误" in text and "18/19" in text and "94.74%" in text
    assert "输入一致" in text and "确认" in text
    result = _analysis_run_row_to_dict({"analysis_payload_json": json.dumps({"analysis_meta": meta})}, include_payload=True)
    assert result["failure_message"] == text


@pytest.mark.parametrize("width", [1440, 390])
def test_browser_shows_prompt_revision_separately_from_analysis_count(leased_task, width, tmp_path):
    import os
    from pathlib import Path
    import threading
    import time
    import uvicorn
    from tests.test_content_review_browser import _free_port

    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        pytest.skip("本机未安装 Chrome")
    task_id, _ = leased_task
    snapshot = presets.get_task_ai_prompt_snapshot(task_id)
    untouched = [presets.get_ai_prompt_preset(f"preset_{index:03d}") for index in (2, 3)]
    with get_connection() as connection:
        connection.execute("UPDATE workflow_jobs SET status='completed' WHERE task_id=?", (task_id,))
        connection.execute("""INSERT INTO ai_analysis_runs (
            id,task_id,run_number,provider,provider_label,model,ai_prompt_preset_id,ai_prompt_preset_name,
            prompt_version_id,prompt_text_sha256,analysis_payload_json,created_at,is_active
        ) VALUES ('test-kangxi-run',?,4,'test','测试','test','preset_001',?,?,?,'{}','2026-09-08',1)""",
            (task_id, snapshot["name"], snapshot["prompt_version_id"], snapshot["prompt_sha256"]))
        connection.commit()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        assert server.started
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=str(chrome), headless=True)
            page = browser.new_page(viewport={"width": width, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/tasks/{task_id}", wait_until="networkidle")
            assert page.locator('input[name="ai_prompt_preset_id"]').count() == 3
            assert page.locator('input[value="preset_004"]').count() == 0
            page.locator('[data-prompt-preset-tab][data-preset-id="preset_002"]').click()
            page.locator('[name="preset_name_preset_002"]').fill("未选择的编辑不能保存")
            page.locator('[data-prompt-preset-tab][data-preset-id="preset_001"]').click()
            assert "【已确认的周复盘补充规则】" not in page.locator('[name="preset_prompt_preset_001"]').input_value()
            page.locator("#save-ai-prompts-button").click()
            page.locator("#ai-process-result").filter(has_text="当前任务已选择").wait_for()
            assert [presets.get_ai_prompt_preset(f"preset_{index:03d}") for index in (2, 3)] == untouched
            assert "第 4 次分析" in page.locator("#ai-analysis-summary").inner_text()
            assert f"内容修订：第 {snapshot['prompt_version_number']} 次" in page.locator("#ai-analysis-summary").inner_text()
            assert "提示词方案：1 号" in page.locator("#ai-analysis-summary").inner_text()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors
            page.screenshot(path=str(tmp_path / f"kangxi-prompts-{width}.png"), full_page=True)
            with get_connection() as connection:
                connection.execute("UPDATE tasks SET ai_prompt_preset_id='preset_004' WHERE id=?", (task_id,))
                connection.commit()
            page.reload(wait_until="networkidle")
            page.get_by_text("当前任务保留 4 号历史绑定", exact=False).click()
            assert page.locator("details.empty-note pre").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
