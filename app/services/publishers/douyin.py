"""抖音创作者中心 Playwright Publisher。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.services.publish_copy_rules import validate_douyin_publish_copy
from app.services.publish_time import utc_now_iso
from app.services.publishers.base import (
    BasePlatformPublisher,
    PublishError,
    PublishNeedsReview,
    PublishOutcome,
    PublishResult,
    PublishValidationError,
    job_caption,
    job_hashtags,
)
from app.services.publishers.browser_runtime import BrowserRuntime
from app.services.publishers import page_scripts


class DouyinPublisher(BasePlatformPublisher):
    name = "douyin"
    platform = "douyin"
    creator_url = "https://creator.douyin.com/creator-micro/content/upload"

    def __init__(self, *, runtime: BrowserRuntime | None = None, account_id: str = "", **_: Any) -> None:
        self.runtime = runtime or BrowserRuntime(self.platform, account_id)

    @classmethod
    def validate_job_data(cls, job: dict[str, Any]) -> None:
        title = str(job.get("title") or "").strip()
        caption = job_caption(job)
        if not title:
            raise PublishValidationError("抖音标题不能为空", "missing_title")
        if len(title) > 30:
            raise PublishValidationError("抖音标题不能超过 30 个字符", "douyin_title_too_long")
        if not caption:
            raise PublishValidationError("抖音正文不能为空", "missing_caption")
        if not job_hashtags(job):
            raise PublishValidationError("抖音话题不能为空", "missing_hashtags")
        try:
            validate_douyin_publish_copy(title, caption, job_hashtags(job))
        except ValueError as exc:
            raise PublishValidationError(str(exc), "douyin_copy_invalid") from exc
        if not str(job.get("cover_file_path") or "").strip():
            raise PublishValidationError("请选择或生成抖音封面", "missing_cover")

    def validate(self, job: dict[str, Any]) -> None:
        super().validate(job)
        self.validate_job_data(job)

    def check_login(self, account_id: str) -> dict[str, Any]:
        with self.runtime.page(self.creator_url) as page:
            return self._wait_for_login(page)

    def open_login(self, account_id: str) -> dict[str, Any]:
        with self.runtime.page(self.creator_url) as page:
            return self._wait_for_login(page, timeout_seconds=600, wait_for_user=True)

    def _login_snapshot(self, page: Any) -> dict[str, Any]:
        text = self.runtime.body_text(page)
        parsed = urlsplit(str(page.url))
        path = parsed.path.lower().rstrip("/")
        login_url = parsed.hostname in {"login.douyin.com", "passport.douyin.com"} or any(
            part in {"login", "passport"} for part in path.split("/")
        )
        login_form = any(marker in text for marker in ("扫码登录", "手机号登录", "请先登录", "登录后使用"))
        if login_url or login_form:
            return {"login_status": "login_required", "message": "抖音账号需要重新登录",
                    "reason": "login_page", "path": path}
        if any(marker in text for marker in ("滑块", "验证码", "安全验证", "短信验证", "操作频繁", "账号存在风险")):
            return {"login_status": "check_failed", "message": "平台要求人工验证，请在创作者中心处理",
                    "reason": "verification_required", "path": path}
        try:
            file_inputs = page.locator('input[type="file"]').count()
        except Exception:
            file_inputs = 0
        normal = parsed.hostname == "creator.douyin.com" and (
            file_inputs > 0 or ("加载中" not in text and any(marker in text for marker in ("发布视频", "上传视频")))
        )
        return {
            "login_status": "normal" if normal else "check_failed",
            "message": "登录状态正常" if normal else "页面尚未就绪，暂时无法确认登录状态，请重新检查状态",
            "reason": "upload_ready" if normal else "page_not_ready", "path": path,
            "file_inputs": file_inputs,
        }

    def _wait_for_login(self, page: Any, *, timeout_seconds: float = 45, wait_for_user: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        previous = ""
        stable = 0
        checks = 0
        while True:
            snapshot = self._login_snapshot(page)
            checks += 1
            status = snapshot["login_status"]
            stable = stable + 1 if status == previous else 1
            previous = status
            elapsed = time.monotonic() - started
            confirmed = stable >= 2 and (status == "normal" or (
                status == "login_required" and elapsed >= 2 and not wait_for_user
            ))
            timed_out = elapsed >= timeout_seconds
            verification = snapshot["reason"] == "verification_required" and not wait_for_user
            if confirmed or timed_out or verification:
                if timed_out and stable < 2 and status != "check_failed":
                    status = "check_failed"
                    snapshot.update(message="页面状态尚未稳定，请重新检查状态", reason="state_unstable")
                evidence = {key: value for key, value in snapshot.items() if key not in {"message", "login_status"}}
                evidence.update(checks=checks, elapsed_seconds=round(elapsed, 2))
                if status == "check_failed":
                    evidence["screenshot"] = self.runtime.screenshot(page, "douyin-login-check-inconclusive")
                self.runtime.phase("account_login_checked", evidence)
                return {"login_status": status, "message": snapshot["message"], "evidence": evidence}
            wait_ms = min(500, max(1, int((timeout_seconds - elapsed) * 1000)))
            if hasattr(page, "wait_for_timeout"):
                page.wait_for_timeout(wait_ms)
            else:
                time.sleep(wait_ms / 1000)

    def publish(self, job: dict[str, Any]) -> PublishResult:
        self.validate(job)
        payload = self.build_payload(job)
        submitted = False
        evidence: dict[str, Any] = {}
        with self.runtime.page(self.creator_url) as page:
            try:
                login = self._wait_for_login(page)
                evidence["login"] = login
                if login["login_status"] != "normal":
                    code = "account_login_required" if login["login_status"] == "login_required" else "account_check_inconclusive"
                    if login["evidence"]["reason"] == "verification_required":
                        code = "platform_verification_required"
                    raise PublishNeedsReview(login["message"], code)
                self.runtime.detect_manual_challenge(page)

                upload = self.runtime.first_visible(page, ['input[type="file"]'], timeout_ms=5000)
                if upload is None:
                    raise PublishError("未找到抖音视频上传入口", "platform_form_changed")
                self.runtime.phase("upload_started", {"video": Path(payload["video_path"]).name})
                upload.set_input_files(payload["video_path"])
                upload_result = self.runtime.wait_for_script_state(
                    page,
                    page_scripts.douyin_upload_state(),
                    phase="upload_waiting",
                    ready_key="upload_ready",
                    timeout_seconds=600,
                    timeout_error_code="video_upload_timeout",
                    timeout_message="抖音视频上传或解析超时",
                    stable_polls=2,
                )
                evidence["upload"] = upload_result
                self.runtime.phase("upload_completed", upload_result)

                content = page_scripts.douyin_description(job, payload["title"])
                evidence["preview_tip"] = self.runtime.evaluate_script(
                    page, page_scripts.douyin_close_preview_tip(), phase="preview_tip_closed"
                )
                evidence["title"] = self.runtime.evaluate_script(
                    page, page_scripts.fill_title(payload["title"]), phase="title_filled"
                )
                evidence["description"] = self.runtime.evaluate_script(
                    page,
                    page_scripts.douyin_set_description(content),
                    phase="description_filled",
                )
                evidence["form_before_cover"] = self.runtime.evaluate_script(
                    page,
                    page_scripts.douyin_verify_ready(payload["title"], content),
                    phase="form_verified_before_cover",
                )
                evidence["recommended_cover_ready"] = self.runtime.evaluate_script(
                    page,
                    page_scripts.douyin_wait_recommended_cover(),
                    phase="recommended_cover_ready",
                    default_error_code="douyin_ai_cover_not_ready",
                )
                evidence["recommended_cover_click"] = self.runtime.evaluate_script(
                    page,
                    page_scripts.douyin_click_recommended_cover(),
                    phase="recommended_cover_clicked",
                )
                evidence["recommended_cover_confirm"] = self.runtime.evaluate_script(
                    page,
                    page_scripts.douyin_confirm_cover(),
                    phase="recommended_cover_confirmed",
                )
                evidence["recommended_cover"] = self.runtime.evaluate_script(
                    page,
                    page_scripts.douyin_verify_cover(),
                    phase="recommended_cover_verified",
                )
                evidence["form_before_submit"] = self.runtime.evaluate_script(
                    page,
                    page_scripts.douyin_verify_ready(payload["title"], content),
                    phase="form_verified_before_submit",
                )
                evidence["visibility"] = self.runtime.evaluate_script(
                    page,
                    page_scripts.douyin_set_visibility(payload.get("visibility") or "public"),
                    phase="visibility_verified",
                    default_error_code="douyin_visibility_not_applied",
                )
                self.runtime.detect_manual_challenge(page)
                click_result = self.runtime.evaluate_script(
                    page,
                    page_scripts.douyin_click_publish(),
                    phase="precise_publish_clicked",
                    default_error_code="douyin_publish_button_not_found",
                )
                if not click_result.get("clicked"):
                    raise PublishError("抖音发布按钮没有返回已点击证据", "douyin_publish_click_not_confirmed")
                submitted = True
                evidence["click"] = click_result
                self.runtime.phase("submit_clicked", click_result)
                confirmation = self.runtime.evaluate_script(
                    page,
                    page_scripts.douyin_wait_result(payload["title"]),
                    phase="publish_result_checked",
                    default_error_code="douyin_publish_not_confirmed",
                )
                if not confirmation.get("publish_confirmed"):
                    raise PublishNeedsReview(
                        "抖音没有返回可验证的发布成功证据，请在创作者中心核对",
                        "publish_result_uncertain",
                    )
                evidence["confirmation"] = confirmation
                platform_url = self.runtime.extract_link(page, ("/video/", "/creator-micro/content/manage"))
                confirmed_url = str(confirmation.get("url") or page.url or "")
                if not platform_url and "/manage" in confirmed_url:
                    platform_url = confirmed_url
                remote_id = self.runtime.extract_remote_id(platform_url)
                self.runtime.phase("confirmed_success", {"platform_url": platform_url, "remote_video_id": remote_id})
                return PublishResult(
                    outcome=PublishOutcome.PUBLISHED,
                    message="抖音投稿成功",
                    remote_video_id=remote_id,
                    platform_url=platform_url,
                    published_at=utc_now_iso(),
                    provider_response={
                        **evidence,
                        "final_url": confirmed_url,
                    },
                )
            except Exception as exc:
                screenshot = self.runtime.screenshot(page, "douyin-publish-error")
                error_code = (
                    exc.error_code if isinstance(exc, PublishError) else "douyin_publish_failed"
                )
                message = (
                    exc.message if isinstance(exc, PublishError) else str(exc)
                )
                diagnostic = {**evidence, "screenshot": screenshot, "submitted": submitted}
                self.runtime.hold_for_manual_review(
                    page,
                    message,
                    error_code,
                    evidence=diagnostic,
                )
                if submitted:
                    raise PublishNeedsReview(
                        f"抖音投稿结果不确定，请人工确认。{message}",
                        "publish_result_uncertain",
                    ) from exc
                if isinstance(exc, PublishNeedsReview):
                    raise
                # 失败窗口允许用户人工操作，因此即使错误发生在点击前，也不能再自动重试。
                raise PublishNeedsReview(message, error_code) from exc

    def _set_cover(self, page: Any, cover_path: str) -> None:
        if not cover_path or not Path(cover_path).is_file():
            return
        if not self.runtime.click_first(page, ('text="选择封面"', 'button:has-text("选择封面")'), required=False):
            return
        cover_input = self.runtime.first_visible(
            page,
            ('input[type="file"][accept*="image"]', 'input[type="file"]'),
            timeout_ms=3000,
        )
        if cover_input is not None:
            cover_input.set_input_files(cover_path)
            self.runtime.click_first(page, ('button:has-text("完成")', 'button:has-text("确定")'), required=False)

    def _set_visibility(self, page: Any, visibility: str) -> None:
        labels = {"public": "公开", "friends": "好友可见", "private": "仅自己可见"}
        label = labels.get(visibility, "公开")
        if label == "公开":
            return
        self.runtime.click_first(page, ('text="公开"', '[role="combobox"]'), required=False)
        self.runtime.click_first(page, (f'text="{label}"',), required=False)
