# ruff: noqa: F811
import os
from pathlib import Path
import threading
import time

import pytest
import uvicorn

from app.db.database import get_connection
from app.main import app
from app.services.ai_analysis_workflow_service import (
    _insert_clip_candidates_with_connection,
)
from app.services.ai.content_decision_analyzer import _payload
from tests.test_content_decisions import item
from tests.test_content_decision_persistence import task_id  # noqa: F401
from tests.test_content_review_browser import _free_port

playwright = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize("width", [1440, 390])
def test_decision_groups_do_not_turn_bulk_selection_into_confirmation(
    task_id, width, tmp_path
):
    with get_connection() as connection:
        _insert_clip_candidates_with_connection(
            connection,
            task_id,
            [
                _payload(item(decision=d), i)
                for i, d in enumerate(("publish", "review", "reject"), 1)
            ],
            "now",
        )
        connection.commit()
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
    try:
        with playwright.sync_playwright() as runtime:
            chrome = (
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
                / "Google/Chrome/Application/chrome.exe"
            )
            if not chrome.exists():
                pytest.skip("Chrome unavailable")
            browser = runtime.chromium.launch(
                executable_path=str(chrome), headless=True
            )
            page = browser.new_page(viewport={"width": width, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(
                f"http://127.0.0.1:{port}/tasks/{task_id}/clips/review",
                wait_until="networkidle",
            )
            assert page.locator("[data-clip-card]").count() == 3
            assert page.get_by_text("AI 决定理由：", exact=False).count() == 3
            assert page.locator("[name=confirm_ai_decision]").count() == 2
            page.locator("[data-clip-select-all]").check()
            assert page.locator("[name=enabled]:checked").count() == 3
            assert page.locator("[name=confirm_ai_decision]:checked").count() == 0
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator("[name=clip_filter]").select_option("review")
            page.wait_for_url("**clip_filter=review**", wait_until="networkidle")
            page.locator("[data-clip-card]").first.wait_for()
            assert page.locator("[data-clip-card]").count() == 1
            assert page.get_by_text("缺少最后两秒证据", exact=True).is_visible()
            assert not errors
            page.screenshot(
                path=str(tmp_path / f"content-decisions-{width}.png"), full_page=True
            )
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
