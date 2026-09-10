# ruff: noqa: F811
# pytest discovers the imported fixture; its test argument intentionally has the same name.
from pathlib import Path
import os
import threading
import time

import pytest
import uvicorn

from app.main import app
from tests.test_content_review_browser import _free_port
from tests.test_weekly_content_review import sample, ready_report  # noqa: F401

playwright = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize("width", [1440, 390])
def test_three_suggestions_preview_apply_rollback_and_compact_disclosure(
    sample,
    width,
    tmp_path,
):
    ready_report(sample)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"
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
            chrome = (
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
                / "Google/Chrome/Application/chrome.exe"
            )
            if not chrome.exists():
                pytest.skip("本机未安装 Chrome")
            browser = runtime.chromium.launch(
                executable_path=str(chrome), headless=True
            )
            page = browser.new_page(viewport={"width": width, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(
                f"http://127.0.0.1:{port}/content-review", wait_until="networkidle"
            )
            page.locator("#content-review-account").select_option(sample["account"])
            # 账号切换会重新加载全部模块；旧页面也可能已有同样文案，
            # 等这次请求完成再点击，避免操作即将被替换的旧 details。
            page.wait_for_load_state("networkidle")
            page.locator("#content-review-insight-count").filter(
                has_text="3 条总结建议"
            ).wait_for()
            assert page.locator("#content-review-insights > article").count() == 3
            page.get_by_text("新的选片补充规则：", exact=False).wait_for()
            page.locator(".weekly-review-evidence summary").first.click()
            page.locator(".weekly-review-evidence[open] article").nth(1).wait_for(state="visible")
            assert page.locator(".weekly-review-evidence[open] article").count() >= 2
            folded = page.locator('[data-content-review-disclosure="prompt-evidence"]')
            if folded.get_attribute("open") is not None:
                folded.locator(":scope > summary").click()
            assert folded.locator("..").bounding_box()["height"] < 200
            folded.locator(":scope > summary").click()
            page.get_by_text("查看当时使用的完整生成规则（Prompt）").first.click()
            assert page.locator(".content-review-prompt-item pre").first.is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.on("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="确认应用这些改动", exact=True).click()
            page.get_by_role("heading", name="已应用的改动", exact=True).wait_for()
            page.get_by_role("button", name="回退到应用前规则", exact=True).click()
            page.get_by_role("heading", name="已回退的改动", exact=True).wait_for()
            page.route(
                "**/api/content-review/prompt-comparison?*",
                lambda route: route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"detail":"test module unavailable"}',
                ),
            )
            page.reload(wait_until="networkidle")
            page.locator("#content-review-insight-count").filter(
                has_text="3 条总结建议"
            ).wait_for()
            assert page.get_by_text("部分复盘模块加载失败", exact=False).is_visible()
            assert not errors, errors
            page.screenshot(
                path=str(tmp_path / f"weekly-review-{width}.png"), full_page=True
            )
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
