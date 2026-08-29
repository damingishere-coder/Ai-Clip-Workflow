from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
import socket
import threading
import time
from uuid import uuid4

import pytest
import uvicorn

playwright = pytest.importorskip("playwright.sync_api")

from app.db.database import get_connection, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import content_review_service  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _seed_account() -> str:
    account_id = f"test-content-review-browser-{uuid4().hex[:8]}"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO publish_accounts (
                id, platform, account_name, login_status, created_at, updated_at
            ) VALUES (?, 'douyin', '页面测试账号', 'normal', 'now', 'now')
            """,
            (account_id,),
        )
        connection.commit()
    return account_id


@pytest.mark.parametrize("width", [1440, 390])
def test_content_review_page_has_no_document_overflow(width: int, monkeypatch, tmp_path):
    init_db()
    account_id = _seed_account()
    monkeypatch.setattr(
        content_review_service,
        "_now",
        lambda: datetime.fromisoformat("2026-08-29T00:48:37+08:00"),
    )
    content_review_service.commit_douyin_item_export(
        account_id=account_id,
        items=[
            {
                "aweme_id": "export:browser-test",
                "title": "页面测试作品",
                "published_at": "2026-08-28T20:00:00+08:00",
                "duration_seconds": 60,
                "play_count": 100,
                "completion_rate": 0.4,
                "five_second_completion_rate": 0.6,
                "two_second_bounce_rate": 0.2,
                "average_watch_seconds": 24,
                "content_genre": "视频",
            }
        ],
        captured_at="2026-08-29T00:47:00+08:00",
        source_filename="作品列表导出.xlsx",
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started

    try:
        with playwright.sync_playwright() as runtime:
            chrome_path = (
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
                / "Google/Chrome/Application/chrome.exe"
            )
            if not chrome_path.exists():
                pytest.skip("浏览器级测试需要本机安装 Google Chrome")
            browser = runtime.chromium.launch(
                headless=True,
                executable_path=str(chrome_path),
            )
            page = browser.new_page(viewport={"width": width, "height": 900})
            try:
                page.goto(
                    f"http://127.0.0.1:{port}/content-review",
                    wait_until="networkidle",
                )
                assert page.locator("h1").filter(has_text="内容复盘").is_visible()
                assert page.locator("#content-review-import-form").is_visible()
                assert page.locator("#content-review-sync").is_visible()
                page.locator("#content-review-last-export").filter(
                    has_text="上次成功导出：北京时间 2026-08-29 00:48"
                ).wait_for()
                assert page.locator("#content-review-last-export-stats").inner_text() == (
                    "1 条作品 · 已匹配 0 · 待确认 0 · 未匹配 1"
                )
                assert page.locator("label.file-upload-button").is_visible()
                assert not page.locator("#content-review-file").is_visible()
                assert page.locator("#content-review-preview-button").is_disabled()
                sample_file = tmp_path / "页面测试.xlsx"
                sample_file.write_bytes(b"not-uploaded")
                page.locator("#content-review-file").set_input_files(str(sample_file))
                assert page.locator("#content-review-file-name").inner_text() == "页面测试.xlsx"
                assert page.locator("#content-review-preview-button").is_enabled()
                replacement_file = tmp_path / "页面测试-新.xlsx"
                replacement_file.write_bytes(b"not-uploaded-either")
                page.evaluate(
                    """
                    window.fetch = ((originalFetch) => (path, options = {}) => {
                      if (!String(path).includes('/api/content-review/imports/preview')) {
                        return originalFetch(path, options);
                      }
                      return new Promise((resolve, reject) => {
                        window.__resolveOldContentReviewPreview = () => resolve({
                          ok: true,
                          json: async () => ({
                            batch_id: 'old-preview-batch',
                            already_imported: false,
                            filename: '页面测试.xlsx',
                            report_type: 'douyin_item_export',
                            period_start: '2026-08-01',
                            period_end: '2026-08-28',
                            row_count: 1,
                            message: '旧预览不应显示'
                          })
                        });
                        options.signal?.addEventListener(
                          'abort',
                          () => reject(new DOMException('Aborted', 'AbortError')),
                          {once: true}
                        );
                      });
                    })(window.fetch);
                    """
                )
                page.locator("#content-review-preview-button").click()
                page.locator("#content-review-file").set_input_files(str(replacement_file))
                page.evaluate("window.__resolveOldContentReviewPreview?.()")
                page.wait_for_timeout(100)
                assert page.locator("#content-review-file-name").inner_text() == "页面测试-新.xlsx"
                assert page.locator("#content-review-preview").is_hidden()
                assert page.locator("#content-review-commit").is_disabled()
                assert "旧预览不应显示" not in page.locator("#content-review-message").inner_text()
                account_details = page.locator('[data-content-review-disclosure="account-history"]')
                work_details = page.locator('[data-content-review-disclosure="work-attribution"]')
                assert account_details.get_attribute("open") is None
                assert work_details.get_attribute("open") is None
                account_details.locator("summary").click()
                assert account_details.get_attribute("open") is not None
                page.reload(wait_until="networkidle")
                assert page.locator(
                    '[data-content-review-disclosure="account-history"]'
                ).get_attribute("open") is not None
                assert page.locator(
                    '[data-content-review-disclosure="work-attribution"]'
                ).get_attribute("open") is None
                page.evaluate(
                    """
                    renderSummary({
                      latest_metric_date: '2026-08-28',
                      last_export_committed_at: '2026-08-29T00:48:37+08:00',
                      current_period: {two_second_bounce_rate: 0.30},
                      previous_period: {two_second_bounce_rate: 0.20},
                      comparisons: {two_second_bounce_rate: 0.50},
                      history: [],
                      match_summary: {}
                    })
                    """
                )
                bounce_delta = page.locator(
                    '[data-metric="two_second_bounce_rate"] [data-delta]'
                )
                assert "is-down" in (bounce_delta.get_attribute("class") or "")
                page.evaluate(
                    """
                    renderSummary({
                      latest_metric_date: '2026-08-28',
                      current_period: {two_second_bounce_rate: 0.18},
                      previous_period: {two_second_bounce_rate: 0.20},
                      comparisons: {two_second_bounce_rate: -0.10},
                      history: [],
                      match_summary: {}
                    })
                    """
                )
                assert "is-up" in (bounce_delta.get_attribute("class") or "")
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth - window.innerWidth"
                )
                assert overflow <= 1
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        with get_connection() as connection:
            batch_rows = connection.execute(
                "SELECT id FROM content_metric_import_batches WHERE account_id = ?",
                (account_id,),
            ).fetchall()
            for batch in batch_rows:
                connection.execute(
                    "DELETE FROM douyin_item_metric_snapshots WHERE batch_id = ?",
                    (batch["id"],),
                )
            connection.execute(
                "DELETE FROM content_metric_import_batches WHERE account_id = ?",
                (account_id,),
            )
            connection.execute(
                "DELETE FROM publish_accounts WHERE id = ?",
                (account_id,),
            )
            connection.commit()
