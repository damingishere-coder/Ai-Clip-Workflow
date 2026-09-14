import os
from pathlib import Path
import threading
import time

import pytest
import uvicorn

from app.db.database import get_connection
from app.main import app
from tests.test_content_review_browser import _free_port
from tests.test_human_review import human_db as _human_db_fixture, seed_run, decide

human_db = _human_db_fixture


@pytest.mark.parametrize("width", [1440, 390])
def test_freeze_and_reopen_report_without_official_account(width, human_db, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        pytest.skip("本机未安装 Chrome")
    seed_run()
    decide("human-task", "run-1")
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
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("request", lambda r: writes.append(r.url) if r.method not in {"GET", "HEAD"} else None)
            page.goto(f"http://127.0.0.1:{port}/content-review", wait_until="networkidle")
            assert not writes
            page.locator("#intelligence-create").click()
            page.get_by_text("报告已保存。生产策略和排期保持不变。", exact=True).wait_for()
            assert "接受 1" in page.locator("#intelligence-result").inner_text()
            assert "暂无达到比较门槛" in page.locator("#intelligence-result").inner_text()
            assert "仅提供人工审片统计" in page.locator("#intelligence-result").inner_text()
            decide("human-task", "run-1", decision="reject", reason="low_value")
            page.reload(wait_until="networkidle")
            page.locator("#intelligence-report > details > summary").click()
            page.locator("#intelligence-history button").first.click()
            page.get_by_text("人工明确审阅 1 条，接受 1，拒绝 0；数据不足。", exact=True).wait_for()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            assert len(writes) == 1 and writes[0].endswith("/intelligence/reports")
            assert not errors
            page.screenshot(path=str(tmp_path / f"intelligence-{width}.png"), full_page=True)
            with get_connection() as c:
                assert c.execute("SELECT COUNT(*) FROM content_intelligence_reports").fetchone()[0] == 1
                assert c.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == 0
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
