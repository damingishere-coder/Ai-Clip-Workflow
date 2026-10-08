import os
from datetime import datetime, timedelta
from pathlib import Path
import threading
import time

import pytest
import uvicorn

playwright = pytest.importorskip("playwright.sync_api")

from app.db.database import get_connection  # noqa: E402
from app.main import app  # noqa: E402
from app.services.publish_scheduler import PublishScheduler  # noqa: E402
from tests.test_adaptive_schedule import db  # noqa: E402,F401
from tests.test_adaptive_schedule import seed_metrics  # noqa: E402
from tests.test_publish_center_browser import _seed_job, _free_port  # noqa: E402


def test_adaptive_drawer_confirm_and_disable(db, monkeypatch, tmp_path):  # noqa: F811
    chrome = (
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        / "Google/Chrome/Application/chrome.exe"
    )
    if not chrome.exists():
        pytest.skip("需要本机 Chrome")
    job_id = _seed_job(tmp_path, 1)
    seed_metrics()
    with get_connection() as c:
        c.execute("UPDATE publish_jobs SET account_id='target' WHERE id=?", (job_id,))
        c.commit()
    monkeypatch.setattr(PublishScheduler, "_require_ready_jobs", lambda *a, **kw: {})
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(
                headless=True, executable_path=str(chrome)
            )
            page = browser.new_page(
                viewport={"width": 1440, "height": 1000}, timezone_id="Asia/Shanghai"
            )
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/publish", wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
            assert not errors, errors
            info = page.request.get(
                f"http://127.0.0.1:{port}/api/publish/schedules/adaptive"
            ).json()
            assert info.get("available"), info
            page.locator("[data-adaptive-panel]").wait_for(state="visible")
            checkbox = page.locator(
                f'[data-section="content"][data-job-id="{job_id}"] [data-publish-select]'
            )
            checkbox.check()
            page.locator("[data-open-schedule-drawer]").click()
            assert page.locator('[name="schedule_mode"]').input_value() == "adaptive"
            assert page.locator('[name="daily_end_time"]').input_value() == "23:59"
            assert not page.locator('[name="interval_preset"]').is_visible()
            start = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT09:00")
            page.locator('[name="start_at_local"]').fill(start)
            page.locator("[data-preview-schedule]").click()
            page.wait_for_function(
                "!document.querySelector('[data-confirm-schedule]').disabled"
            )
            assert "测试" in page.locator("[data-schedule-preview]").inner_text()
            artifact_dir = Path(os.environ.get("NIUMA_UI_ARTIFACT_DIR", str(tmp_path)))
            artifact_dir.mkdir(parents=True, exist_ok=True)
            page.locator("[data-schedule-drawer]").evaluate("el => el.scrollTop = 0")
            page.screenshot(path=str(artifact_dir / "adaptive-preview.png"))
            page.locator("[data-confirm-schedule]").click()
            page.locator("[data-schedule-drawer]").wait_for(state="hidden")
            with get_connection() as c:
                row = c.execute(
                    "SELECT status,adaptive_managed FROM publish_jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
                assert tuple(row) == ("SCHEDULED", 1)
            # Close automatic adjustments without changing the concrete time.
            page.locator("[data-adaptive-panel] summary").click()
            assert "完播中位数" in page.locator("[data-adaptive-panel]").inner_text()
            assert "涨粉中位数" in page.locator("[data-adaptive-panel]").inner_text()
            page.locator("[data-refresh-adaptive]").click()
            page.wait_for_function(
                "document.querySelector('[data-toggle-adaptive]').textContent.includes('停用')"
            )
            page.locator("[data-toggle-adaptive]").click()
            page.wait_for_function(
                "document.querySelector('[data-toggle-adaptive]').textContent.includes('启用')"
            )
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
            page.screenshot(
                path=str(artifact_dir / "adaptive-mobile.png"), full_page=True
            )
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
