"""A hidden source monitor must wait for explicit opening before decoding media."""
import os
from pathlib import Path
import shutil
import subprocess
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


@pytest.fixture
def source_monitor_tools():
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        pytest.skip("Chrome unavailable")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg unavailable")
    return playwright, chrome, ffmpeg


@pytest.fixture
def source_monitor_page(source_monitor_tools, output_batch, tmp_path):
    playwright, chrome, ffmpeg = source_monitor_tools
    task, candidate, _ = output_batch
    with get_connection() as connection:
        source = Path(connection.execute("SELECT original_video_path FROM tasks WHERE id=?", (task,)).fetchone()[0])
        assert source.resolve().is_relative_to(tmp_path.resolve())
        row = dict(connection.execute("SELECT * FROM clip_candidates WHERE id=?", (candidate,)).fetchone())
        columns = list(row)
        for index in range(11):
            copy = {**row, "id": uuid4().hex, "clip_key": f"monitor-{index}", "title": f"候选 {index + 2}", "start_time": "00:00:04", "end_time": "00:00:06"}
            connection.execute(f"INSERT INTO clip_candidates ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [copy[name] for name in columns])
        connection.commit()
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10", "-t", "12", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(source)], check=True, capture_output=True, timeout=30)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.05)
        assert server.started
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=str(chrome), headless=True)
            try:
                page = browser.new_page()
                errors, writes = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: writes.append(request.url) if request.method not in ("GET", "HEAD") else None)
                yield page, playwright.expect, f"http://127.0.0.1:{port}/tasks/{task}/clips/review", errors, writes
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize("width", [1440, 390])
def test_source_monitor_loads_on_first_open_and_preserves_seeking(width, source_monitor_page):
    page, expect, url, errors, writes = source_monitor_page
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(url, wait_until="networkidle")
    expect(page.locator(".review-candidate")).to_have_count(12)
    page.wait_for_function("document.querySelector('#clip-preview-video').readyState >= 2")
    monitor = page.locator("#source-monitor-video")
    assert monitor.get_attribute("src") is None
    assert monitor.evaluate("el => ({state: el.readyState, currentSrc: el.currentSrc})") == {"state": 0, "currentSrc": ""}
    assert page.evaluate("Array.from(document.querySelectorAll('video')).filter(el => el.readyState > 0 && el.currentSrc).length") == 1
    # Selecting a later candidate and then going backwards still seeks the shared preview.
    page.locator(".review-candidate button").nth(1).click()
    page.wait_for_function("Math.abs(document.querySelector('#clip-preview-video').currentTime - 4) < .2")
    page.locator(".review-candidate button").first.click()
    page.wait_for_function("Math.abs(document.querySelector('#clip-preview-video').currentTime - 1) < .2")
    if width < 1280:
        page.locator('[data-review-view="editor"]').click()
    trigger = page.locator('[data-clip-card]:visible [data-source-monitor-trigger]')
    trigger.click()
    expect(page.locator("#source-monitor-modal")).to_be_visible()
    expect(page.locator("#close-source-monitor")).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(page.locator("#apply-source-monitor")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("#close-source-monitor")).to_be_focused()
    page.wait_for_function("document.querySelector('#source-monitor-video').readyState >= 2 && Math.abs(document.querySelector('#source-monitor-video').currentTime - 1) < .2")
    assert monitor.evaluate("el => el.duration") == pytest.approx(12, abs=.2)
    for selector in ["#close-source-monitor", "#cancel-source-monitor", "#apply-source-monitor"]:
        bounds = page.locator(selector).bounding_box()
        assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
        assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 1000
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.locator('[data-source-action="jump-out"]').click()
    page.wait_for_function("Math.abs(document.querySelector('#source-monitor-video').currentTime - 3) < .2")
    page.locator('[data-source-action="jump-in"]').click()
    page.wait_for_function("Math.abs(document.querySelector('#source-monitor-video').currentTime - 1) < .2")
    page.locator('[data-source-action="in-back"]').click()
    expect(page.locator("#source-monitor-in-time")).to_contain_text("00:00:00")
    page.locator('[data-source-action="preview"]').click()
    page.wait_for_function("document.querySelector('#source-monitor-video').currentTime >= 3 && document.querySelector('#source-monitor-video').paused")
    page.locator('[data-source-action="out-forward"]').focus()
    page.keyboard.press("Tab")
    expect(page.locator("#cancel-source-monitor")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("#apply-source-monitor")).to_be_focused()
    screenshots = Path(__file__).resolve().parents[1] / "data/acceptance/ui-refactor"
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / f"source-monitor-lazy-{width}.png"))
    page.locator("#cancel-source-monitor").click()
    expect(page.locator("#source-monitor-modal")).to_be_hidden()
    expect(trigger).to_be_focused()
    trigger.click()
    page.wait_for_function("Math.abs(document.querySelector('#source-monitor-video').currentTime - 1) < .2")
    page.keyboard.press("Escape")
    expect(page.locator("#source-monitor-modal")).to_be_hidden()
    expect(trigger).to_be_focused()
    page.locator('[data-clip-card]:visible .secondary-button[data-preview-trigger]').click()
    page.wait_for_function("document.querySelector('#clip-preview-video').currentTime >= 3 && document.querySelector('#clip-preview-video').paused")
    assert not errors
    assert not writes
