import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest
import uvicorn

from app.db.database import get_connection
from app.main import app
from tests.test_content_review_browser import _free_port
from tests.test_production_review import output_batch as _output_batch, batch_db as _batch_db, human_db as _human_db

output_batch, batch_db, human_db = _output_batch, _batch_db, _human_db


def test_large_review_has_one_current_editor_and_visible_actions_at_five_widths(output_batch, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        pytest.skip("Chrome unavailable")
    task, candidate, _ = output_batch
    with get_connection() as connection:
        row = dict(connection.execute("SELECT * FROM clip_candidates WHERE id=?", (candidate,)).fetchone())
        columns = list(row)
        for index in range(35):
            copy = {**row, "id": uuid4().hex, "title": f"长标题候选{index}：" + "检查完整表达与自然收尾" * 5}
            connection.execute(f"INSERT INTO clip_candidates ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                               [copy[name] for name in columns])
        connection.commit()
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
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            for width in [1440, 1280, 1024, 768, 390]:
                page.set_viewport_size({"width": width, "height": 1000})
                page.goto(f"http://127.0.0.1:{port}/tasks/{task}/clips/review", wait_until="networkidle")
                assert page.locator(".review-candidate").count() == 36
                page.locator(".review-candidate button").nth(1).click()
                if width < 1280:
                    page.locator('[data-review-view="editor"]').click()
                assert page.locator("[data-clip-card]:visible").count() == 1
                assert page.locator("#clip-preview-video").count() == 1
                for selector in ["#save-clips-button", "#generate-clips-button", "[data-clip-card]:visible [name=title]"]:
                    button = page.locator(selector)
                    button.scroll_into_view_if_needed()
                    bounds = button.bounding_box()
                    assert bounds and bounds["x"] >= -1 and bounds["x"] + bounds["width"] <= width + 1
                page.screenshot(path=str(tmp_path / f"review-{width}.png"))
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                assert page.locator(".review-workspace").bounding_box()["height"] < 2500
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
