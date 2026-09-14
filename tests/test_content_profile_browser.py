"""Creation flow in real Chrome; intercept submit before any job is created."""

import os
from pathlib import Path
import threading
import time

import pytest
import uvicorn

from app.main import app
from tests.test_content_review_browser import _free_port


@pytest.mark.parametrize("width", [1440, 390])
def test_profile_prompt_provider_creation_flow(width, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        pytest.skip("本机未安装 Chrome")
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
            page = browser.new_page(viewport={"width": width, "height": 1100})
            errors, posts = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/tasks/new", wait_until="networkidle")
            assert page.locator('#selection-profile option:not([value=""])').count() == 5
            page.select_option("#selection-profile", "variety_comedy")
            assert page.input_value("#new-task-prompt") == "preset_001"
            assert "试用" not in page.inner_text("#selection-profile-hint")
            page.select_option("#selection-profile", "interview_story")
            assert "试用" in page.inner_text('#selection-profile option:checked')
            assert "真实内容质量待日常使用验证" in page.inner_text("#selection-profile-hint")
            assert page.input_value("#new-task-prompt") == "profile_interview_v1"
            assert page.input_value('[name="max_clip_duration"]') == "4"
            page.select_option("#selection-profile", "long_live_talk")
            assert "试用" not in page.inner_text("#selection-profile-hint")
            assert page.locator("#long-live-settings").is_visible()
            assert page.input_value('[name="candidate_clip_count"]') == "30"
            page.select_option("#selection-profile", "knowledge_opinion")
            assert "试用" in page.inner_text('#selection-profile option:checked')
            assert "真实内容质量待日常使用验证" in page.inner_text("#selection-profile-hint")
            assert page.locator("#long-live-settings").is_hidden()
            assert page.input_value("#new-task-prompt") == "profile_knowledge_v1"
            assert page.input_value('[name="max_clip_duration"]') == "3"
            assert "30–180" in page.inner_text("#profile-duration-hint")
            assert page.locator('[name="candidate_clip_count"] option[value="20"]').evaluate("el => el.disabled"), page.locator('[name="candidate_clip_count"]').evaluate("el => el.outerHTML")
            page.select_option("#new-task-prompt", "preset_002")
            page.select_option("#new-task-provider", "remote")
            page.fill('[name="task_name"]', "浏览器知识素材")
            page.set_input_files("#video-file-input", {"name": "test.mp4", "mimeType": "video/mp4", "buffer": b"isolated-fake-video"})

            def submitted(route):
                posts.append(route.request.post_data_buffer.decode("utf-8", errors="replace"))
                route.fulfill(status=200, content_type="application/json", body='{"message":"模拟提交完成","detail_url":"/tasks/new"}')
            page.route("**/api/tasks/upload", submitted)
            page.click("#new-task-submit-button")
            page.wait_for_url(f"http://127.0.0.1:{port}/tasks/new")
            page.wait_for_function("document.querySelector('#selection-profile').value === ''")
            assert len(posts) == 1
            for name, value in (("selection_profile", "knowledge_opinion"), ("ai_prompt_preset_id", "preset_002"), ("ai_provider", "remote")):
                assert f'name="{name}"\r\n\r\n{value}\r\n' in posts[0]
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
