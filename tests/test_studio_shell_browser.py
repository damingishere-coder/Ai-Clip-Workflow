"""The shared shell searches read-only and preserves deliberate settings writes."""
import os
from pathlib import Path
import threading
import time

import pytest
import uvicorn

from app.main import app
from tests.test_content_review_browser import _free_port


@pytest.fixture
def studio_browser():
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
    if not chrome.exists():
        pytest.skip("Chrome unavailable")
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
            yield page, playwright.expect, f"http://127.0.0.1:{port}"
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize("width", [1440, 390])
def test_navigation_and_search_only_read_tasks(width, studio_browser):
    page, expect, base = studio_browser
    page.set_viewport_size({"width": width, "height": 900})
    writes, searches = [], []
    page.on("request", lambda request: writes.append(request.url) if request.method not in ("GET", "HEAD") else None)

    def search(route):
        searches.append(route.request.url)
        route.fulfill(json={"tasks": [{"id": "shell-test-task", "title": "康熙长标题测试制作", "platform_label": "抖音", "ui": {"stage_label": "待审片"}}], "pagination": {"total": 1}})

    page.route("**/api/ui/tasks?*", search)
    page.goto(base, wait_until="networkidle")
    expect(page.locator('nav[aria-label="主导航"] a')).to_have_count(5)
    if width < 901:
        page.get_by_role("button", name="打开导航", exact=True).click()
        expect(page.get_by_role("link", name="制作任务", exact=True)).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#studio-menu-toggle")).to_be_focused()
        expect(page.locator("#studio-menu-toggle")).to_have_attribute("aria-expanded", "false")
    else:
        expect(page.locator("#studio-menu-toggle")).to_be_hidden()
    search_box = page.get_by_role("searchbox", name="搜索制作任务")
    search_box.fill("康熙")
    expect(page.locator(".studio-search-result")).to_contain_text("康熙长标题测试制作")
    expect(page.locator(".studio-search-result")).to_have_attribute("href", "/tasks/shell-test-task")
    search_box.press("ArrowDown")
    expect(page.locator(".studio-search-result")).to_be_focused()
    page.keyboard.press("Escape")
    expect(search_box).to_be_focused()
    assert searches and "q=" in searches[0]
    assert not writes
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


def test_prompt_editor_requires_confirmation_and_keeps_failed_edits(studio_browser):
    page, expect, base = studio_browser
    writes = []
    preset = {"id": "test-shell-plan", "slot": 1, "name": "正式共享方案", "prompt_text": "原始正文", "updated_at": "2026-10-08", "is_default": True}
    page.route("**/api/ai-prompt-presets", lambda route: route.fulfill(json=[preset]))

    def patch(route):
        writes.append(route.request.post_data_json)
        if len(writes) == 1:
            route.fulfill(status=500, json={"detail": "隔离测试模拟保存失败"})
        else:
            route.fulfill(json={"status": "ok", "preset": {**preset, **route.request.post_data_json, "updated_at": "2026-10-09"}})

    page.route("**/api/ai-prompt-presets/test-shell-plan", patch)
    page.goto(f"{base}/system?tab=plans", wait_until="networkidle")
    expect(page.get_by_role("tab", name="制作方案", exact=True)).to_have_attribute("aria-selected", "true")
    editor = page.locator('#studio-plan-form textarea[name="prompt_text"]')
    expect(editor).to_have_value("原始正文")
    editor.fill("准备的新正文")
    page.locator("#studio-plan-save").evaluate("button => { button.click(); button.click(); }")
    dialog = page.get_by_role("dialog", name="保存共享制作方案")
    expect(dialog).to_have_count(1)
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="取消", exact=True).click()
    assert not writes
    expect(editor).to_have_value("准备的新正文")
    page.get_by_role("button", name="保存共享方案", exact=True).click()
    page.get_by_role("dialog", name="保存共享制作方案").get_by_role("button", name="保存共享方案", exact=True).click()
    expect(page.locator("#studio-plan-status")).to_contain_text("保存失败")
    expect(editor).to_have_value("准备的新正文")
    expect(page.locator("#studio-plan-save")).to_be_enabled()
    assert writes == [{"name": "正式共享方案", "prompt_text": "准备的新正文"}]
    page.locator("#studio-plan-save").click()
    page.get_by_role("dialog", name="保存共享制作方案").get_by_role("button", name="保存共享方案", exact=True).click()
    expect(page.locator("#studio-plan-status")).to_contain_text("共享方案已保存")
    expect(page.locator("#studio-plan-save")).to_be_disabled()
    expect(editor).to_be_enabled()
    expect(editor).to_have_value("准备的新正文")
    assert len(writes) == 2


def test_runtime_statistics_fit_all_widths_with_long_paths(studio_browser):
    page, expect, base = studio_browser
    writes = []
    page.on("request", lambda request: writes.append(request.url) if request.method not in ("GET", "HEAD") else None)
    page.goto(f"{base}/system", wait_until="networkidle")
    expect(page.locator("#studio-settings-runtime")).to_be_visible()
    long_path = "C:/Users/10578/Documents/" + "超长运行目录" * 40 + "/Ai-Clip-Workflow-offline-runtime/data/workflow.sqlite3"
    page.locator("#studio-settings-runtime .stat-card small").first.evaluate("(el, path) => {el.textContent = path;}", long_path)
    page.locator("#studio-settings-runtime .compact-list li").first.evaluate("(el, path) => {el.textContent = '数据库文件：' + path;}", long_path)
    for width in (1440, 1280, 1024, 768, 390):
        page.set_viewport_size({"width": width, "height": 960})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
        assert page.locator("#studio-settings-runtime .system-grid").evaluate_all(
            "elements => elements.every(el => el.scrollWidth <= el.clientWidth + 1)"
        ), width
        assert page.locator("#studio-settings-runtime .stat-card").evaluate_all(
            "elements => elements.every(el => {const box = el.getBoundingClientRect(); return box.left >= 0 && box.right <= innerWidth + 1;})"
        ), width
    assert not writes


def test_twenty_real_work_records_keep_matching_actions_in_view(studio_browser):
    from app.services import content_review_service
    from tests.test_content_review import _cleanup, _insert_account, _insert_publish_job

    account_id = _insert_account()
    try:
        items = []
        for index in range(20):
            item_id = f"studio-responsive-{account_id}-{index}"
            title = f"作品 {index:02d}：" + "需要审核的长标题" * 6
            if index < 18:
                _insert_publish_job(account_id, title=title, published_at="2026-10-08T08:00:00+08:00", platform_item_id=item_id)
            items.append({"aweme_id": item_id, "title": title, "published_at": "2026-10-08T08:00:00+08:00", "duration_seconds": 60, "play_count": index * 100 + 10, "completion_rate": .42})
        content_review_service.commit_douyin_item_export(account_id=account_id, captured_at="2026-10-08T10:00:00+08:00", source_filename="官方作品数据导出.xlsx", items=items)
        assert len(content_review_service.list_content_review_works(account_id)) == 20
        page, expect, base = studio_browser
        writes = []
        page.on("request", lambda request: writes.append(request.url) if request.method not in ("GET", "HEAD") else None)
        page.goto(f"{base}/content-review?account_id={account_id}", wait_until="networkidle")
        expect(page.locator("#content-review-works tr")).to_have_count(20)
        page.locator('[data-content-review-disclosure="work-attribution"] summary').click()
        expect(page.locator("#content-review-works").get_by_role("button", name="解除关联", exact=True)).to_have_count(18)
        expect(page.locator("#content-review-works").get_by_role("button", name="人工确认", exact=True)).to_have_count(2)
        for width in (1440, 1280, 1024, 768, 390):
            page.set_viewport_size({"width": width, "height": 960})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
            assert page.locator(".content-review-works-table").evaluate("el => el.scrollWidth <= el.clientWidth + 1"), width
            assert page.locator("#content-review-works button, #content-review-works input").evaluate_all(
                "elements => elements.every(el => {const box = el.getBoundingClientRect(); return box.width > 0 && box.left >= 0 && box.right <= innerWidth + 1;})"
            ), width
        assert not writes
    finally:
        _cleanup()
