"""Playwright 持久化 Chrome 上下文和通用页面操作。

此模块只会由 Windows 发布 Worker 实际调用；FastAPI/Docker 调度进程不会启动浏览器。
"""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from app.core.config import settings
from app.services.publishers.base import PublishError, PublishNeedsReview


PhaseCallback = Callable[[str, dict[str, Any] | None], None]


class BrowserRuntime:
    def __init__(
        self,
        platform: str,
        account_id: str,
        *,
        phase_callback: PhaseCallback | None = None,
    ) -> None:
        self.platform = platform
        self.account_id = account_id
        self.phase_callback = phase_callback or (lambda _phase, _details=None: None)
        self.profile_dir = Path(settings.publish_browser_profile_dir) / platform / account_id
        self.artifact_dir = Path(settings.publish_browser_artifact_dir) / platform / account_id

    def phase(self, phase: str, details: dict[str, Any] | None = None) -> None:
        self.phase_callback(phase, details)

    @contextmanager
    def page(self, url: str) -> Iterator[Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PublishError(
                "Windows 发布 Worker 未安装 Playwright，请执行 pip install -r requirements.txt",
                "playwright_not_installed",
            ) from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.phase("browser_opening", {"url": url})
        with sync_playwright() as playwright:
            launch_options: dict[str, Any] = {
                "user_data_dir": str(self.profile_dir),
                "headless": bool(settings.publish_browser_headless),
                "locale": "zh-CN",
                "timezone_id": settings.app_timezone,
                "viewport": {"width": 1440, "height": 960},
            }
            channel = str(settings.publish_browser_channel or "chrome").strip()
            if channel:
                launch_options["channel"] = channel
            context = playwright.chromium.launch_persistent_context(**launch_options)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=settings.publish_browser_navigation_timeout_ms)
                self.phase("browser_opened", {"url": page.url})
                yield page
            finally:
                context.close()

    def first_visible(self, page: Any, selectors: Sequence[str], timeout_ms: int = 1500) -> Any | None:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=timeout_ms)
                return locator
            except Exception:
                continue
        return None

    def fill_first(self, page: Any, selectors: Sequence[str], value: str, *, required: bool = True) -> bool:
        locator = self.first_visible(page, selectors)
        if locator is None:
            if required:
                raise PublishError(f"未找到平台表单字段：{selectors[0]}", "platform_form_changed")
            return False
        try:
            locator.fill(value)
        except Exception:
            locator.click()
            locator.press("Control+A")
            locator.press("Backspace")
            locator.type(value, delay=20)
        return True

    def click_first(self, page: Any, selectors: Sequence[str], *, required: bool = True) -> bool:
        locator = self.first_visible(page, selectors)
        if locator is None:
            if required:
                raise PublishError(f"未找到平台操作按钮：{selectors[0]}", "platform_form_changed")
            return False
        locator.click()
        return True

    @staticmethod
    def body_text(page: Any) -> str:
        try:
            return page.locator("body").inner_text(timeout=3000)
        except Exception:
            return ""

    def detect_manual_challenge(self, page: Any) -> None:
        text = self.body_text(page)
        markers = ("滑块", "验证码", "安全验证", "扫码登录", "短信验证", "操作频繁", "账号存在风险")
        matched = next((marker for marker in markers if marker in text), "")
        if matched:
            self.phase("manual_review", {"reason": matched})
            raise PublishNeedsReview(f"平台要求人工处理：{matched}", "platform_verification_required")

    def wait_for_text(self, page: Any, patterns: Sequence[str], timeout_seconds: int) -> str:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            text = self.body_text(page)
            for pattern in patterns:
                if pattern in text:
                    return pattern
            self.detect_manual_challenge(page)
            time.sleep(1)
        return ""

    @staticmethod
    def extract_link(page: Any, href_patterns: Sequence[str]) -> str:
        for pattern in href_patterns:
            try:
                links = page.locator(f'a[href*="{pattern}"]').evaluate_all(
                    "nodes => nodes.map(node => node.href).filter(Boolean)"
                )
            except Exception:
                continue
            if links:
                return str(links[0])
        return ""

    @staticmethod
    def extract_remote_id(url: str) -> str:
        for pattern in (r"/video/(\d+)", r"/(BV[0-9A-Za-z]+)", r"[?&]aid=(\d+)"):
            matched = re.search(pattern, url or "")
            if matched:
                return matched.group(1)
        return ""

    def screenshot(self, page: Any, name: str) -> str:
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)
        path = self.artifact_dir / f"{int(time.time())}-{safe_name}.png"
        try:
            page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception:
            return ""
