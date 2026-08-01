"""抖音创作者中心 Playwright Publisher。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

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
        if not str(job.get("cover_file_path") or "").strip():
            raise PublishValidationError("请选择或生成抖音封面", "missing_cover")

    def validate(self, job: dict[str, Any]) -> None:
        super().validate(job)
        self.validate_job_data(job)

    def check_login(self, account_id: str) -> dict[str, Any]:
        with self.runtime.page(self.creator_url) as page:
            time.sleep(2)
            text = self.runtime.body_text(page)
            login_required = "扫码登录" in text or "手机号登录" in text or "login" in page.url.lower()
            normal = not login_required and (
                page.locator('input[type="file"]').count() > 0 or "发布视频" in text or "上传视频" in text
            )
            return {
                "login_status": "normal" if normal else "login_required",
                "message": "登录状态正常" if normal else "抖音账号需要重新登录",
            }

    def open_login(self, account_id: str) -> dict[str, Any]:
        with self.runtime.page(self.creator_url) as page:
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                text = self.runtime.body_text(page)
                if "发布视频" in text or "上传视频" in text or page.locator('input[type="file"]').count() > 0:
                    return {"login_status": "normal", "message": "抖音登录成功"}
                time.sleep(2)
        return {"login_status": "login_required", "message": "等待登录超时，请重新打开登录窗口"}

    def publish(self, job: dict[str, Any]) -> PublishResult:
        self.validate(job)
        payload = self.build_payload(job)
        submitted = False
        evidence: dict[str, Any] = {}
        with self.runtime.page(self.creator_url) as page:
            try:
                self.runtime.detect_manual_challenge(page)
                login = self._page_logged_in(page)
                if not login:
                    raise PublishNeedsReview("抖音账号登录失效，请重新登录", "account_login_required")

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

    def _page_logged_in(self, page: Any) -> bool:
        text = self.runtime.body_text(page)
        return not ("扫码登录" in text or "手机号登录" in text or "login" in page.url.lower())

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
