from __future__ import annotations

import os
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
def test_content_review_page_has_no_document_overflow(width: int):
    init_db()
    account_id = _seed_account()
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
            connection.execute(
                "DELETE FROM publish_accounts WHERE id = ?",
                (account_id,),
            )
            connection.commit()
