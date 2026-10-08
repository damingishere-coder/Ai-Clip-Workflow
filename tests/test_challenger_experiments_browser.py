import os
from pathlib import Path
import threading
import time

import pytest
import uvicorn

from app.main import app
from app.services.ai_prompt_preset_service import get_ai_prompt_preset
from tests.test_challenger_experiments import seed_experiment
from tests.test_content_review_browser import _free_port
from tests.test_human_review import human_db as _human_db_fixture

human_db = _human_db_fixture


@pytest.mark.parametrize("width", [1440, 390])
def test_experiment_browser_requires_distinct_baseline_decision_and_policy_confirmation(width, human_db, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        pytest.skip("本机未安装 Chrome")
    draft, account, _, report, _, experiment, _ = seed_experiment(treatment=20)
    original = get_ai_prompt_preset("preset_001")["prompt_text"]
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.05)
    try:
        assert server.started
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=str(chrome), headless=True)
            page = browser.new_page(viewport={"width": width, "height": 1000})
            errors, writes = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda r: writes.append(r.url) if r.method not in {"GET", "HEAD"} else None)
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto(f"http://127.0.0.1:{port}/content-review/challengers?report_id={draft['report_id']}", wait_until="networkidle")
            page.locator("#challenger-history button").first.click()
            area = page.locator(f'[data-challenger-experiment="{draft["id"]}"]')
            area.get_by_label("实验账号", exact=True).select_option(account)
            area.get_by_label("冻结基线报告", exact=True).select_option(report["id"])
            area.get_by_role("button", name="检查官方基线").click()
            page.wait_for_function("document.querySelector('[aria-label=基线分组]').options.length > 1")
            area.get_by_label("基线分组", exact=True).select_option(index=1)
            area.get_by_role("button", name="确认创建作品实验").click()
            assert not writes
            area.locator('input[type="checkbox"]').check()
            area.get_by_role("button", name="确认创建作品实验").click()
            area.get_by_text("前往内容复盘查看实验", exact=True).wait_for()
            assert get_ai_prompt_preset("preset_001")["prompt_text"] == original
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.screenshot(path=str(tmp_path / f"experiment-baseline-{width}.png"), full_page=True)
            page.goto(f"http://127.0.0.1:{port}/content-review", wait_until="networkidle")
            page.get_by_role("button", name="保留改动", exact=True).click()
            page.get_by_role("button", name="预览正式启用", exact=True).wait_for()
            assert get_ai_prompt_preset("preset_001")["prompt_text"] == original
            page.get_by_role("button", name="预览正式启用", exact=True).click()
            page.get_by_role("button", name="确认正式启用", exact=True).wait_for()
            assert not any(url.endswith("/policy-events") for url in writes)
            assert page.get_by_label("当前正式正文", exact=True).input_value() == original
            page.get_by_role("button", name="确认正式启用", exact=True).click()
            assert not any(url.endswith("/policy-events") for url in writes)
            page.get_by_label("我已核对差异", exact=False).check()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.screenshot(path=str(tmp_path / f"policy-preview-{width}.png"), full_page=True)
            page.locator("#content-review-experiments").screenshot(path=str(tmp_path / f"policy-detail-{width}.png"))
            page.get_by_role("button", name="确认正式启用", exact=True).click()
            page.get_by_text("正式策略已按本次确认启用，旧任务保持冻结。", exact=True).wait_for()
            assert get_ai_prompt_preset("preset_001")["prompt_text"] == draft["evidence"]["challenger_prompt_text"]
            page.get_by_role("button", name="预览回退", exact=True).click()
            page.get_by_role("button", name="确认回退", exact=True).wait_for()
            page.get_by_label("我已核对差异", exact=False).check()
            page.get_by_role("button", name="确认回退", exact=True).click()
            page.get_by_text("已恢复启用前正文，已创建的任务保持冻结。", exact=True).wait_for()
            assert get_ai_prompt_preset("preset_001")["prompt_text"] == original
            assert len([url for url in writes if url.endswith("/policy-events")]) == 2
            assert not any("/sync" in url or "/process/" in url or "/schedule" in url for url in writes)
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
