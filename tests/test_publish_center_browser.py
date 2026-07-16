from __future__ import annotations

import socket
import threading
import time
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn

playwright = pytest.importorskip("playwright.sync_api")

from app.core.config import settings  # noqa: E402
from app.db.database import get_connection, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import publish_service  # noqa: E402


PREFIX = "test-browser-publish-"


def _seed_job(tmp_path: Path, index: int, platform: str = "douyin") -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    task_id = f"{PREFIX}{uuid4().hex[:8]}"
    clip_id = f"{PREFIX}clip-{uuid4().hex[:8]}"
    job_id = f"{PREFIX}job-{uuid4().hex[:8]}"
    video = tmp_path / f"browser-{index}.mp4"
    video.write_bytes(b"fake-video")
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO tasks (id, task_name, task_dir_name, platform, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'COMPLETED', ?, ?)",
            (task_id, f"浏览器排期任务 {index}", task_id, platform, now, now),
        )
        connection.execute(
            "INSERT INTO output_clip (id, task_id, output_file_path, output_file_name, status, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, 'completed', 1, ?, ?)",
            (clip_id, task_id, str(video), video.name, now, now),
        )
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption,
                tags, hashtags, scheduled_at, schedule_timezone, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'opencli_publish', 'original', ?, ?, ?, '测试正文',
                '测试正文', '测试', '测试', '', 'Asia/Shanghai', 'WAITING', ?, ?)
            """,
            (job_id, task_id, clip_id, clip_id, platform, str(video), str(video), f"浏览器测试片段 {index}", now, now),
        )
        connection.commit()
    return job_id


def _cleanup():
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_publish_center_schedule_preview_confirm_and_publish(monkeypatch, tmp_path):
    init_db()
    _cleanup()
    first = _seed_job(tmp_path, 1)
    second = _seed_job(tmp_path, 2)
    bilibili = _seed_job(tmp_path, 3, "bilibili")
    opencli_calls: list[str] = []

    def mocked_opencli(job_id: str, runner=None):
        opencli_calls.append(job_id)
        return {"status": "ok", "confirmed": True, "message": "mock opencli submitted", "job": {"id": job_id}}

    monkeypatch.setattr(publish_service, "execute_opencli_send_job", mocked_opencli)
    original_opencli_fallback = settings.publish_enable_opencli_fallback
    object.__setattr__(settings, "publish_enable_opencli_fallback", True)
    future_start = datetime.now() + timedelta(days=2)
    future_day = future_start.strftime("%Y-%m-%d")
    following_day = (future_start + timedelta(days=1)).strftime("%Y-%m-%d")
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started

    try:
        with playwright.sync_playwright() as runtime:
            chrome_path = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
            if not chrome_path.exists():
                pytest.skip("浏览器级测试需要本机安装 Google Chrome")
            browser = runtime.chromium.launch(headless=True, executable_path=str(chrome_path))
            context = browser.new_context(timezone_id="Asia/Shanghai")
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{port}/publish", wait_until="networkidle")

            page.locator('[data-center-tab="schedule"]').click()
            assert page.locator('[data-schedule-calendar] .publish-calendar-day').count() == 42
            assert "抖音" in page.locator("[data-calendar-title]").inner_text()
            assert page.locator(
                f'[data-publish-row][data-section="schedule"][data-job-id="{bilibili}"]'
            ).is_hidden()

            page.locator('[data-schedule-platform="bilibili"]').click()
            assert "B站" in page.locator("[data-calendar-title]").inner_text()
            assert page.locator(
                f'[data-publish-row][data-section="schedule"][data-job-id="{bilibili}"]'
            ).is_visible()
            assert page.locator(
                f'[data-publish-row][data-section="schedule"][data-job-id="{first}"]'
            ).is_hidden()

            page.locator('[data-schedule-platform="douyin"]').click()
            page.locator(f'[data-publish-row][data-section="schedule"][data-job-id="{first}"] [data-publish-select]').check()
            page.locator(f'[data-publish-row][data-section="schedule"][data-job-id="{second}"] [data-publish-select]').check()
            page.locator("[data-open-schedule-drawer]").click()
            page.locator('[name="start_at_local"]').fill(f"{future_day}T20:00")
            page.locator('[name="daily_start_time"]').fill("09:00")
            page.locator('[name="daily_end_time"]').fill("21:00")
            page.locator("[data-preview-schedule]").click()
            page.locator("[data-confirm-schedule]:not([disabled])").wait_for()
            preview_text = page.locator("[data-schedule-preview]").inner_text()
            assert f"{future_day} 20:00" in preview_text
            assert f"{following_day} 09:00" in preview_text

            page.locator("[data-confirm-schedule]").click()
            page.locator("[data-schedule-drawer]").wait_for(state="hidden")
            assert f"{future_day} 20:00" in page.locator(
                f'[data-publish-row][data-section="schedule"][data-job-id="{first}"] [data-row-schedule]'
            ).inner_text()

            page.on("dialog", lambda dialog: dialog.accept())
            page.locator(f'[data-publish-row][data-section="schedule"][data-job-id="{first}"] [data-publish-now]').click()
            page.wait_for_function(
                "jobId => document.querySelector(`[data-publish-row][data-section=\"history\"][data-job-id=\"${jobId}\"]`).dataset.status === 'PUBLISHED'",
                arg=first,
            )
            assert opencli_calls == [first]
            context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        object.__setattr__(settings, "publish_enable_opencli_fallback", original_opencli_fallback)
        _cleanup()
