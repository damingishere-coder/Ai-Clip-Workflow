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
from app.services.publishers.worker_client import validate_worker_identifier


PhaseCallback = Callable[[str, dict[str, Any] | None], None]


class BrowserRuntime:
    def __init__(
        self,
        platform: str,
        account_id: str,
        *,
        phase_callback: PhaseCallback | None = None,
    ) -> None:
        self.platform = validate_worker_identifier(platform, "platform", max_length=20)
        self.account_id = validate_worker_identifier(account_id, "account_id", max_length=120)
        self.phase_callback = phase_callback or (lambda _phase, _details=None: None)
        self.profile_dir = Path(settings.publish_browser_profile_dir) / self.platform / self.account_id
        self.artifact_dir = Path(settings.publish_browser_artifact_dir) / self.platform / self.account_id

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
                try:
                    context.close()
                except Exception:
                    # 用户可能在人工保留期间提前关闭窗口；清理动作必须幂等，
                    # 不能用“浏览器已经关闭”覆盖真正的上传/验证错误。
                    pass

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

    def evaluate_script(
        self,
        page: Any,
        script: str,
        *,
        phase: str,
        default_error_code: str = "platform_form_changed",
    ) -> dict[str, Any]:
        """执行共享 DOM 脚本，并保留脚本内的稳定错误标记。"""

        if phase:
            self.phase(phase, None)
        try:
            result = page.evaluate(script)
        except Exception as exc:
            message = str(exc)
            marker = re.search(r"\b((?:douyin|bilibili)_[a-z0-9_]+)", message)
            error_code = marker.group(1) if marker else default_error_code
            if "_publish_blocked" in error_code or any(
                text in message for text in ("验证码", "安全验证", "登录失效", "风控", "内容违规")
            ):
                raise PublishNeedsReview(f"平台要求人工处理：{message}", error_code) from exc
            raise PublishError(message, error_code) from exc
        if result is None:
            return {}
        if not isinstance(result, dict):
            return {"result": result}
        return result

    def wait_for_script_state(
        self,
        page: Any,
        script: str,
        *,
        phase: str,
        ready_key: str,
        timeout_seconds: int,
        timeout_error_code: str,
        timeout_message: str,
        stable_polls: int = 1,
        interval_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """轮询页面状态，只有连续稳定命中后才返回。

        页面脚本可以返回 ``error_code`` 和 ``message`` 表示确定失败；这种情况
        会立即终止，避免继续填写表单或点击发布。
        """

        self.phase(phase, {"state": "waiting"})
        deadline = time.monotonic() + max(1, int(timeout_seconds))
        stable_count = 0
        last_result: dict[str, Any] = {}
        last_signature: tuple[Any, ...] | None = None
        while time.monotonic() < deadline:
            self.detect_manual_challenge(page)
            result = self.evaluate_script(page, script, phase="")
            last_result = result
            error_code = str(result.get("error_code") or "").strip()
            if error_code:
                message = str(result.get("message") or "平台返回了失败状态")
                raise PublishError(message, error_code)

            signature = (
                result.get("state"),
                result.get("progress"),
                result.get("message"),
                bool(result.get(ready_key)),
            )
            if signature != last_signature:
                self.phase(phase, result)
                last_signature = signature

            if bool(result.get(ready_key)):
                stable_count += 1
                if stable_count >= max(1, int(stable_polls)):
                    return {**result, "stable_polls": stable_count}
            else:
                stable_count = 0
            time.sleep(max(0.05, float(interval_seconds)))

        details = str(last_result.get("message") or last_result.get("state") or "")
        suffix = f"（最后状态：{details}）" if details else ""
        raise PublishError(f"{timeout_message}{suffix}", timeout_error_code)

    def hold_for_manual_review(
        self,
        page: Any,
        message: str,
        error_code: str,
        *,
        seconds: int | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        """在失败后保留可见 Chrome，供用户查看或人工处理。"""

        hold_seconds = max(
            0,
            int(
                settings.publish_browser_failure_hold_seconds
                if seconds is None
                else seconds
            ),
        )
        if hold_seconds <= 0 or bool(getattr(page, "is_closed", lambda: False)()):
            return
        details = {
            **(evidence or {}),
            "error_code": error_code,
            "message": message,
            "hold_seconds": hold_seconds,
        }
        self.phase("manual_review_waiting", details)
        try:
            page.evaluate(
                """
                ({message, errorCode, holdSeconds}) => {
                  const existing = document.getElementById('niuma-publish-review-banner');
                  if (existing) existing.remove();
                  const banner = document.createElement('div');
                  banner.id = 'niuma-publish-review-banner';
                  banner.setAttribute('role', 'alert');
                  Object.assign(banner.style, {
                    position: 'fixed', top: '16px', left: '50%', transform: 'translateX(-50%)',
                    zIndex: '2147483647', width: 'min(760px, calc(100vw - 32px))',
                    padding: '16px 18px', borderRadius: '14px', color: '#172033',
                    background: 'rgba(255,255,255,.97)', border: '1px solid rgba(255,59,48,.32)',
                    boxShadow: '0 18px 50px rgba(15,23,42,.24)', font: '14px/1.55 system-ui'
                  });
                  const title = document.createElement('strong');
                  title.textContent = '牛马片场已暂停自动发送';
                  title.style.display = 'block';
                  title.style.color = '#c62828';
                  title.style.marginBottom = '6px';
                  const body = document.createElement('div');
                  body.textContent = `${message}（${errorCode}）`;
                  const footer = document.createElement('div');
                  footer.style.marginTop = '6px';
                  footer.style.color = '#5f6b7a';
                  footer.textContent = `此窗口最多保留 ${Math.ceil(holdSeconds / 60)} 分钟。若你人工完成发布，请回发送中心核对结果，不要直接重试。`;
                  banner.append(title, body, footer);
                  document.documentElement.appendChild(banner);
                }
                """,
                {"message": message, "errorCode": error_code, "holdSeconds": hold_seconds},
            )
        except Exception:
            pass

        deadline = time.monotonic() + hold_seconds
        while time.monotonic() < deadline:
            try:
                if page.is_closed():
                    break
            except Exception:
                break
            time.sleep(1)

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
