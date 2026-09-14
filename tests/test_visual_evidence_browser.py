import os
from pathlib import Path
import threading
import time

import pytest
import uvicorn

from app.main import app
from app.services.visual_cache_service import cleanup_visual_cache
from datetime import datetime, timezone, timedelta
from tests.test_content_review_browser import _free_port
from tests.test_visual_evidence import visual_task as _visual_task_fixture  # noqa: F401
from tests.test_visual_cache import complete

visual_task = _visual_task_fixture


@pytest.mark.parametrize("width", [1440, 390])
def test_visual_evidence_frames_pin_cleaned_state_and_no_resend(width, visual_task, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        pytest.skip("本机未安装 Chrome")
    c = visual_task
    row = complete(c)
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
            page = browser.new_page(viewport={"width":width, "height":1000})
            errors, writes = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda req: writes.append(req.url) if req.method not in {"GET","HEAD"} else None)
            page.goto(f"http://127.0.0.1:{port}/tasks/{c.task_id}/visual-evidence", wait_until="networkidle")
            article = page.locator(f'[data-evidence-id="{row["id"]}"]')
            assert "11.02 秒" in article.inner_text()
            assert "清晰字幕" in article.inner_text()
            assert "same-model" in article.inner_text()
            page.get_by_role("button", name="固定图片，暂停自动清理").click()
            page.get_by_role("button", name="取消固定图片").wait_for()
            assert "图片已固定" in article.inner_text()
            page.get_by_role("button", name="取消固定图片").click()
            page.get_by_role("button", name="固定图片，暂停自动清理").wait_for()
            cleanup_visual_cache(now=datetime.now(timezone.utc) + timedelta(days=8), seconds=10, limit=1000)
            page.reload(wait_until="networkidle")
            assert "结构证据继续保留" in article.inner_text()
            assert article.locator("img").count() == 0
            assert "清晰字幕" in article.inner_text()
            assert not page.get_by_role("button", name="固定图片，暂停自动清理").count()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            assert c.provider.calls == 1 and len(writes) == 2 and all(url.endswith("/pin") for url in writes)
            assert not errors
            page.screenshot(path=str(tmp_path/f"visual-evidence-{width}.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
