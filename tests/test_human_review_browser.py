import os
from pathlib import Path
import threading
import time

import pytest
import uvicorn

from app.main import app
from app.db.database import get_connection
from tests.test_content_review_browser import _free_port
from tests.test_human_review import human_db as _human_db_fixture, seed_run

human_db = _human_db_fixture


@pytest.mark.parametrize("width", [1440, 390])
def test_human_review_without_account_explicit_decisions_and_c_diagnostics(width, human_db, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        pytest.skip("本机未安装 Chrome")
    seed_run()
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
            page.on("request", lambda req: writes.append(req.url) if req.method not in {"GET", "HEAD"} else None)
            page.goto(f"http://127.0.0.1:{port}/content-review", wait_until="networkidle")
            assert "未明确审阅 3" in page.locator("#human-review-summary").inner_text()
            assert "暂无有效分母" in page.locator("#human-review-summary").inner_text()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.goto(f"http://127.0.0.1:{port}/tasks/human-task/clips", wait_until="networkidle")
            page.locator("#human-observation-disclosure > summary").click()
            first = page.locator("#human-review-observations article").first
            first.get_by_role("button", name="明确接受").wait_for()
            assert not writes
            first.get_by_role("button", name="明确接受").click()
            page.get_by_text("评价已保存。切片选择仍使用原有审核操作。", exact=True).wait_for()
            assert "已明确接受" in first.inner_text()
            page.locator("#human-review-diagnostics > summary").click()
            diagnostic = page.locator("#human-review-diagnostic-items article").first
            diagnostic.get_by_role("button", name="明确拒绝").click()
            assert "请先" in page.locator("#human-review-decision-status").inner_text()
            assert len(writes) == 1
            diagnostic.get_by_role("combobox").select_option("low_value")
            diagnostic.get_by_role("button", name="明确拒绝").click()
            page.get_by_text("评价已保存。切片选择仍使用原有审核操作。", exact=True).wait_for()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.screenshot(path=str(tmp_path/f"human-review-{width}.png"), full_page=True)
            page.goto(f"http://127.0.0.1:{port}/content-review", wait_until="networkidle")
            assert "接受 1" in page.locator("#human-review-summary").inner_text()
            assert "拒绝 1" in page.locator("#human-review-summary").inner_text()
            assert "数据不足" in page.locator("#human-review-summary").inner_text()
            assert "100.0%" in page.locator("#human-review-summary").inner_text()  # Correct denominator: one reviewed recommendation.
            assert len(writes) == 2 and all(url.endswith("/review-observations") for url in writes)
            assert not errors
            with get_connection() as c:
                assert c.execute("SELECT COUNT(*) FROM clip_feedback").fetchone()[0] == 2
                assert c.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == 0
                assert c.execute("SELECT COUNT(*) FROM publish_jobs").fetchone()[0] == 0
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
