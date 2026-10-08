from __future__ import annotations

import os
import base64
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn

playwright = pytest.importorskip("playwright.sync_api")

from app.db.database import get_connection, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import publish_service  # noqa: E402
from tests.test_publish_center_browser import _cleanup, _free_port, _seed_job  # noqa: E402


@pytest.fixture
def workspace_page(tmp_path, monkeypatch):
    init_db()
    _cleanup()
    ids = [_seed_job(tmp_path, index, task_key="workspace") for index in range(2)]
    with get_connection() as connection:
        connection.executemany("UPDATE publish_jobs SET tags='测试,片段,访谈', description='具体片段的测试简介', caption='具体片段的测试简介' WHERE id=?", [(id,) for id in ids])
        connection.commit()
    monkeypatch.setattr(publish_service, "generate_publish_metadata", lambda *args, **kwargs: {
        "title": "AI提出的具体片段标题", "description": "这个回答，把话题带到意料之外", "tags": "测试,片段,访谈", "source": "ai:offline-browser"
    })
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        server.should_exit = True
        pytest.skip("需要本机 Chrome")
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=str(chrome))
        page = browser.new_page(timezone_id="Asia/Shanghai", viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(f"http://127.0.0.1:{port}/publish", wait_until="networkidle")
            yield page, ids, errors
        finally:
            browser.close()
            server.should_exit = True
            thread.join(timeout=10)
            _cleanup()


def raw_job(id):
    with get_connection() as connection:
        return dict(connection.execute("SELECT * FROM publish_jobs WHERE id=?", (id,)).fetchone())


@pytest.mark.parametrize("width", [390, 1440])
def test_compact_editor_save_failure_keeps_current_tab_then_saves_before_plan(workspace_page, width):
    page, ids, errors = workspace_page
    page.set_viewport_size({"width": width, "height": 1000})
    row = page.locator(f'[data-section="content"][data-job-id="{ids[0]}"]')
    # The collapsed health/help region leaves the actual content in the first viewport.
    assert page.locator('[data-section="content"]:visible').first.bounding_box()["y"] < 750, page.evaluate("""() => Array.from(document.querySelectorAll('.send-heading,.scheduler-health-card,.publish-platform-context,.publish-center-tabs,.publish-content-header,.publish-content-maintenance,.publish-task-group-header')).map(el => [el.className, el.getBoundingClientRect().y, el.getBoundingClientRect().height])""")
    assert page.locator(".publish-health-details").get_attribute("open") is None
    playwright.expect(row.locator("[data-publish-editor]")).to_be_hidden()
    row.locator("[data-open-content-editor]").click()
    form = row.locator("[data-publish-editor]")
    form.locator('[name="title"]').fill("")
    form.locator("[data-add-to-plan]").click()
    playwright.expect(form.locator("[data-editor-save-state]")).to_contain_text("保存失败")
    playwright.expect(page.locator('[data-center-panel="content"]')).to_be_visible()
    assert raw_job(ids[0])["title"] != ""
    form.locator('[name="title"]').fill("用户修改后保存的片段标题")
    form.locator("[data-add-to-plan]").click()
    playwright.expect(page.locator("[data-schedule-drawer]")).to_be_visible()
    assert raw_job(ids[0])["title"] == "用户修改后保存的片段标题"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors


def test_dirty_editor_survives_poll_and_stale_save_keeps_server_content(workspace_page):
    page, ids, errors = workspace_page
    row = page.locator(f'[data-section="content"][data-job-id="{ids[0]}"]')
    row.locator("[data-open-content-editor]").click()
    form = row.locator("[data-publish-editor]")
    form.locator('[name="title"]').fill("尚未保存的手工标题")
    old_cover = form.locator('[name="cover_file_path"]').input_value()
    with get_connection() as connection:
        connection.execute("UPDATE publish_jobs SET title='其他窗口已保存标题', cover_file_path='server-cover.png', updated_at='changed-version' WHERE id=?", (ids[0],))
        connection.commit()
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    playwright.expect(form.locator("[data-editor-save-state]")).to_contain_text("服务器版本已变化", timeout=10000)
    assert form.locator('[name="title"]').input_value() == "尚未保存的手工标题"
    assert form.locator('[name="cover_file_path"]').input_value() == old_cover
    form.locator('button[type="submit"]').click()
    playwright.expect(form.locator("[data-editor-save-state]")).to_contain_text("版本冲突")
    assert raw_job(ids[0])["title"] == "其他窗口已保存标题"
    assert form.locator('[name="title"]').input_value() == "尚未保存的手工标题"
    form.locator('[data-reconcile-editor]').click()
    confirm = page.locator('[data-publish-confirm]')
    playwright.expect(confirm).to_contain_text('其他窗口已保存标题')
    confirm.locator('[value="confirm"]').click()
    form.locator('button[type="submit"]').click()
    playwright.expect(form.locator('[data-editor-save-state]')).to_have_text('已保存')
    assert raw_job(ids[0])["title"] == "尚未保存的手工标题"
    assert not errors


def test_ai_suggestion_is_preview_only_until_accept_and_preserves_schedule(workspace_page):
    page, ids, errors = workspace_page
    before = raw_job(ids[0])
    row = page.locator(f'[data-section="content"][data-job-id="{ids[0]}"]')
    row.locator("[data-open-content-editor]").click()
    row.locator("[data-generate-metadata]").click()
    dialog = page.locator("[data-ai-preview-dialog]")
    playwright.expect(dialog.locator("[data-accept-ai-suggestion]")).to_be_visible(timeout=15000)
    assert raw_job(ids[0]) == before
    dialog.locator("[data-accept-ai-suggestion]").click()
    playwright.expect(dialog).to_contain_text("本条建议已接受并保存")
    saved = raw_job(ids[0])
    assert saved["title"] == "AI提出的具体片段标题"
    assert saved["status"] == before["status"]
    assert saved["scheduled_at"] == before["scheduled_at"]
    assert not errors


def test_selection_is_scoped_to_tab_and_calendar_is_optional(workspace_page):
    page, ids, errors = workspace_page
    page.locator(f'[data-section="content"][data-job-id="{ids[0]}"] [data-publish-select]').check()
    playwright.expect(page.locator("[data-selection-bar]")).to_be_visible()
    page.locator('[data-center-tab="schedule"]').click()
    playwright.expect(page.locator("[data-selection-bar]")).to_be_hidden()
    playwright.expect(page.locator("[data-schedule-calendar-card]")).to_be_hidden()
    page.locator('[data-schedule-view="calendar"]').click()
    playwright.expect(page.locator("[data-schedule-calendar-card]")).to_be_visible()
    page.locator('[data-center-tab="history"]').click()
    playwright.expect(page.locator("[data-selection-bar]")).to_be_hidden()
    page.locator('[data-center-tab="content"]').click()
    playwright.expect(page.locator("[data-selected-count]")).to_have_text("1")
    assert not errors


def test_history_job_deeplink_filters_records_and_opens_readable_events(workspace_page):
    page, ids, errors = workspace_page
    with get_connection() as connection:
        connection.executemany("UPDATE publish_jobs SET status='EXPORTED', finished_at='2026-10-08T08:00:00+08:00' WHERE id=?", [(id,) for id in ids])
        connection.commit()
    page.goto(f"{page.url.split('?')[0]}?job_id={ids[0]}&tab=history", wait_until="networkidle")
    records = page.locator('[data-history-record]')
    playwright.expect(records).to_have_count(1)
    assert records.first.get_attribute('data-job-id') == ids[0]
    playwright.expect(page.locator('[data-history-job-focus]')).to_be_visible()
    records.first.locator('[data-view-events]').click()
    dialog = page.locator('[data-events-dialog]')
    playwright.expect(dialog).to_contain_text('暂无执行事件')
    dialog.press('Escape')
    playwright.expect(dialog).to_be_hidden()
    page.locator('[data-clear-history-job-focus]').click()
    playwright.expect(records).to_have_count(2)
    assert not errors


def test_cover_commit_advances_version_and_keeps_typing_before_save_and_plan(workspace_page, monkeypatch, tmp_path):
    from app.services import production_review_service
    page, ids, errors = workspace_page
    before = raw_job(ids[0])
    monkeypatch.setattr(production_review_service, 'check_preparation', lambda *args: None)
    monkeypatch.setattr(publish_service, 'ensure_ffmpeg_available', lambda: 'test-ffmpeg')
    cover = tmp_path / 'frame.png'
    monkeypatch.setattr(publish_service, '_unique_cover_path', lambda *args: cover)
    def generate_frame(command, **kwargs):
        time.sleep(0.5)
        Path(command[-1]).write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=='))
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(publish_service.subprocess, 'run', generate_frame)
    row = page.locator(f'[data-section="content"][data-job-id="{ids[0]}"]')
    row.locator('[data-open-content-editor]').click()
    form = row.locator('[data-publish-editor]')
    form.locator('[data-generate-cover]').click()
    page.locator('[data-cover-dialog] [data-confirm-cover]').click()
    form.locator('[name="title"]').fill('生成封面期间输入的标题')
    playwright.expect(form.locator('[data-editor-save-state]')).to_contain_text('封面已保存')
    assert raw_job(ids[0])['updated_at'] != before['updated_at']
    assert form.locator('[name="title"]').input_value() == '生成封面期间输入的标题'
    form.locator('button[type="submit"]').click()
    playwright.expect(form.locator('[data-editor-save-state]')).to_have_text('已保存')
    assert raw_job(ids[0])['cover_file_path'] == str(cover)
    assert raw_job(ids[0])['title'] == '生成封面期间输入的标题'
    form.locator('[data-add-to-plan]').click()
    playwright.expect(page.locator('[data-schedule-drawer]')).to_be_visible()
    assert not errors


def test_send_setup_reveals_focused_content_editor(workspace_page):
    page, ids, errors = workspace_page
    with get_connection() as connection:
        connection.execute("UPDATE publish_jobs SET description='',caption='',tags='',hashtags='' WHERE id=?", (ids[0],))
        connection.commit()
    page.reload(wait_until='networkidle')
    page.locator('[data-center-tab="schedule"]').click()
    row = page.locator(f'[data-section="schedule"][data-job-id="{ids[0]}"]')
    row.locator('[data-send-setup]').click()
    form = page.locator(f'[data-section="content"][data-job-id="{ids[0]}"] [data-publish-editor]')
    playwright.expect(form).to_be_visible()
    assert page.locator('[data-center-panel="content"]').is_visible()
    assert not errors
