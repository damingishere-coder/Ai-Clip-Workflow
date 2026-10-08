import json
import os
from pathlib import Path
import threading
import time

import pytest
import uvicorn

from app.main import app
from tests.test_challenger_trials import create_trial
from tests.test_content_review_browser import _free_port
from tests.test_human_review import human_db as _human_db_fixture

human_db = _human_db_fixture


@pytest.mark.parametrize("width", [1440, 390])
def test_trial_creation_confirmation_and_manual_ai_do_not_save_production_prompt(width, human_db, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        pytest.skip("本机未安装 Chrome")
    draft, task = create_trial()
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
            errors, writes, uploads = [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda r: writes.append(r.url) if r.method not in {"GET", "HEAD"} else None)
            page.goto(f"http://127.0.0.1:{port}/tasks/new?challenger_id={draft['id']}", wait_until="networkidle")
            assert page.input_value("#new-task-prompt") == draft["challenger_preset_id"]
            assert page.locator("#new-task-prompt option").count() == 1
            assert page.locator('[name="auto_mode"]').count() == 0
            page.fill('[name="task_name"]', "试验上传")
            page.set_input_files("#video-file-input", {"name": "fixture.mp4", "mimeType": "video/mp4", "buffer": b"isolated-fake-video"})

            def upload(route):
                uploads.append(route.request.post_data_buffer.decode("utf-8", errors="replace"))
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"message": "模拟上传", "detail_url": f"/tasks/{task}"}))
            page.route("**/api/tasks/upload", upload)
            page.click("#new-task-submit-button")
            assert not uploads and not writes  # Required human confirmation.
            page.check('[name="confirm_challenger"]')
            page.click("#new-task-submit-button")
            page.wait_for_url(f"http://127.0.0.1:{port}/tasks/{task}")
            for name, value in (("challenger_id", draft["id"]), ("challenger_sha256", draft["evidence_sha256"]),
                                ("confirm_challenger", "true"), ("auto_mode", "false")):
                assert f'name="{name}"\r\n\r\n{value}\r\n' in uploads[0]
            assert page.locator("[data-prompt-preset-card] textarea").evaluate("el => el.readOnly")
            analysis = page.locator('#analysis > details')
            if not analysis.evaluate('element => element.open'):
                analysis.locator('summary').first.click()
            provider_select = page.locator("#ai-analysis-provider")
            analyze_button = page.locator(".js-ai-process-action")
            assert analyze_button.count() == 1
            assert analyze_button.inner_text() == "开始分析"
            assert provider_select.input_value() == ""
            assert provider_select.locator("option").count() == 4
            before_cancel = len(writes)
            page.once("dialog", lambda dialog: dialog.dismiss())
            analyze_button.click()
            assert len(writes) == before_cancel  # Cancelling must not save or queue analysis.

            analysis_requests = []

            def reject_analysis(route):
                assert provider_select.is_disabled() and analyze_button.is_disabled()
                analysis_requests.append(route.request.url)
                route.fulfill(status=409, content_type="application/json", body='{"detail":"隔离测试已拦截模型请求"}')

            page.route("**/process/ai*", reject_analysis)
            page.on("dialog", lambda dialog: dialog.accept())
            for provider in ("", "codex", "remote", "local"):
                before_select = len(writes)
                provider_select.select_option(provider)
                assert len(writes) == before_select  # Choosing a provider has no write side effects.
                analyze_button.click()
                page.wait_for_function("!document.querySelector('#ai-analysis-provider').disabled")
                assert "隔离测试已拦截模型请求" in page.locator("#ai-process-result").inner_text()
                suffix = f"?provider={provider}" if provider else ""
                assert analysis_requests[-1] == f"http://127.0.0.1:{port}/api/tasks/{task}/process/ai{suffix}"
                assert analyze_button.inner_text() == "开始分析"
            assert len(analysis_requests) == 4
            assert not any("/api/ai-prompt-presets/" in url or url.endswith("/ai-prompt-preset") for url in writes)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.screenshot(path=str(tmp_path / f"trial-{width}.png"), full_page=True)
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
