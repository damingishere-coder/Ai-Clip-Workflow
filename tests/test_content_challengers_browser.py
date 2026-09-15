import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest
import uvicorn

from app.db.database import get_connection
from app.main import app
from app.services.content_intelligence_service import create_report
from tests.test_content_review_browser import _free_port
from tests.test_human_review import human_db as _human_db_fixture

human_db = _human_db_fixture


@pytest.mark.parametrize("width", [1440, 390])
def test_human_draft_page_saves_version_diff_without_production_activation(width, human_db, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        pytest.skip("本机未安装 Chrome")
    report = create_report("", 30, str(uuid4()))
    with get_connection() as c:
        original = tuple(c.execute("SELECT * FROM ai_prompt_presets WHERE id='preset_001'").fetchone())
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
            page.goto(f"http://127.0.0.1:{port}/content-review/challengers?report_id={report['id']}", wait_until="networkidle")
            page.locator("#challenger-profile").select_option("variety_comedy")
            page.locator("#challenger-load").click()
            try:
                page.get_by_text("请修改 Prompt 并填写假设。保存后仍不会启用生产策略。", exact=True).wait_for(timeout=5000)
            except Exception as exc:
                raise AssertionError({"status": page.locator("#challenger-status").inner_text(), "errors": errors}) from exc
            assert not writes
            before = page.locator("#challenger-original").input_value()
            page.locator("#challenger-name").fill("<script>试验名称</script>")
            page.locator("#challenger-hypothesis").fill("减少开场铺垫可能更好理解；尚未验证。")
            page.locator("#challenger-prompt").fill(before + "\n优先完整的开场。")
            page.locator("#challenger-confirm").check()
            page.locator("#challenger-save").click()
            page.get_by_text("草稿已保存。当前正式 Prompt、Profile 和排期保持不变。", exact=True).wait_for()
            assert "+优先完整的开场。" in page.locator("#challenger-detail pre").inner_text()
            assert page.locator("#challenger-detail script").count() == 0
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.screenshot(path=str(tmp_path / f"challenger-{width}.png"), full_page=True)
            page.reload(wait_until="networkidle")
            page.locator("#challenger-history button").first.click()
            page.locator("#challenger-detail pre").wait_for()
            assert not errors and len(writes) == 1 and writes[0].endswith("/challengers")
            with get_connection() as c:
                assert tuple(c.execute("SELECT * FROM ai_prompt_presets WHERE id='preset_001'").fetchone()) == original
                assert c.execute("SELECT COUNT(*) FROM content_strategy_challengers").fetchone()[0] == 1
                assert c.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == 0
                assert c.execute("SELECT COUNT(*) FROM content_improvement_experiments").fetchone()[0] == 0
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
