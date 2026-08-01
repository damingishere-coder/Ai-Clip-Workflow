"""B站创作中心 Playwright Publisher。"""

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
    split_hashtags,
)
from app.services.publishers.browser_runtime import BrowserRuntime
from app.services.publishers import page_scripts


class BilibiliPublisher(BasePlatformPublisher):
    name = "bilibili"
    platform = "bilibili"
    creator_url = "https://member.bilibili.com/platform/upload/video/frame"

    def __init__(self, *, runtime: BrowserRuntime | None = None, account_id: str = "", **_: Any) -> None:
        self.runtime = runtime or BrowserRuntime(self.platform, account_id)

    @classmethod
    def validate_job_data(cls, job: dict[str, Any]) -> None:
        title = str(job.get("title") or "").strip()
        if not title:
            raise PublishValidationError("B站标题不能为空", "missing_title")
        if len(title) > 80:
            raise PublishValidationError("B站标题不能超过 80 个字符", "bilibili_title_too_long")
        if not job_caption(job):
            raise PublishValidationError("B站简介不能为空", "missing_caption")
        if not job_hashtags(job):
            raise PublishValidationError("B站标签不能为空", "missing_hashtags")
        if not str(job.get("cover_file_path") or "").strip():
            raise PublishValidationError("请选择或生成 B站封面", "missing_cover")
        if not str(job.get("bilibili_tid") or "").strip():
            raise PublishValidationError("请选择 B站分区", "missing_bilibili_tid")
        copyright_value = str(job.get("bilibili_copyright") or "original")
        if copyright_value not in {"original", "repost"}:
            raise PublishValidationError("B站稿件类型必须是原创或转载", "invalid_bilibili_copyright")
        if copyright_value == "repost" and not str(job.get("bilibili_source") or "").strip():
            raise PublishValidationError("转载稿件必须填写转载来源", "missing_bilibili_source")

    def validate(self, job: dict[str, Any]) -> None:
        super().validate(job)
        self.validate_job_data(job)

    def check_login(self, account_id: str) -> dict[str, Any]:
        with self.runtime.page(self.creator_url) as page:
            time.sleep(2)
            text = self.runtime.body_text(page)
            login_required = "扫码登录" in text or "密码登录" in text or "passport.bilibili.com" in page.url
            normal = not login_required and (
                page.locator('input[type="file"]').count() > 0 or "上传视频" in text or "点击上传" in text
            )
            return {
                "login_status": "normal" if normal else "login_required",
                "message": "登录状态正常" if normal else "B站账号需要重新登录",
            }

    def open_login(self, account_id: str) -> dict[str, Any]:
        with self.runtime.page(self.creator_url) as page:
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                text = self.runtime.body_text(page)
                if "上传视频" in text or "点击上传" in text or page.locator('input[type="file"]').count() > 0:
                    return {"login_status": "normal", "message": "B站登录成功"}
                time.sleep(2)
        return {"login_status": "login_required", "message": "等待登录超时，请重新打开登录窗口"}

    def publish(self, job: dict[str, Any]) -> PublishResult:
        self.validate(job)
        payload = self.build_payload(job)
        submitted = False
        with self.runtime.page(self.creator_url) as page:
            try:
                self.runtime.detect_manual_challenge(page)
                if not self._page_logged_in(page):
                    raise PublishNeedsReview("B站账号登录失效，请重新登录", "account_login_required")
                self.runtime.evaluate_script(
                    page,
                    page_scripts.bilibili_dismiss_local_draft(),
                    phase="local_draft_prompt_checked",
                )
                upload = self.runtime.first_visible(page, ('input[type="file"]',), timeout_ms=5000)
                if upload is None:
                    raise PublishError("未找到 B站视频上传入口", "platform_form_changed")
                self.runtime.phase("upload_started", {"video": Path(payload["video_path"]).name})
                upload.set_input_files(payload["video_path"])
                self.runtime.evaluate_script(
                    page,
                    page_scripts.bilibili_wait_uploaded(),
                    phase="upload_completion_checked",
                    default_error_code="video_upload_timeout",
                )
                self.runtime.phase("upload_completed", None)
                self.runtime.evaluate_script(
                    page,
                    page_scripts.bilibili_select_recommended_cover(),
                    phase="recommended_cover_selected",
                    default_error_code="bilibili_cover_not_ready",
                )
                self.runtime.evaluate_script(
                    page, page_scripts.fill_title(payload["title"]), phase="title_filled"
                )
                if payload["bilibili_copyright"] == "repost":
                    self._set_copyright(page, payload["bilibili_copyright"], payload["bilibili_source"])
                self.runtime.evaluate_script(
                    page,
                    page_scripts.bilibili_select_declaration(),
                    phase="declaration_selected",
                )
                self.runtime.evaluate_script(
                    page,
                    page_scripts.bilibili_select_category(payload["bilibili_tid"]),
                    phase="category_checked",
                )
                self.runtime.evaluate_script(
                    page,
                    page_scripts.bilibili_set_description(payload["caption"]),
                    phase="description_filled",
                )
                self.runtime.evaluate_script(
                    page,
                    page_scripts.bilibili_verify_ready(payload["title"], payload["caption"]),
                    phase="form_verified_before_submit",
                )
                self.runtime.detect_manual_challenge(page)

                self.runtime.phase("submit_clicked", None)
                submitted = True
                click_result = self.runtime.evaluate_script(
                    page,
                    page_scripts.bilibili_click_publish(),
                    phase="precise_publish_clicked",
                    default_error_code="bilibili_publish_button_not_found",
                )
                confirmation = self.runtime.evaluate_script(
                    page,
                    page_scripts.bilibili_wait_result(payload["title"]),
                    phase="publish_result_checked",
                    default_error_code="bilibili_publish_not_confirmed",
                )
                if not confirmation.get("bilibili_publish_confirmed"):
                    raise PublishNeedsReview(
                        "B站没有返回可验证的投稿成功证据，请在创作中心核对",
                        "publish_result_uncertain",
                    )
                platform_url = self.runtime.extract_link(page, ("/video/BV", "member.bilibili.com/platform/upload-manager"))
                confirmed_url = str(confirmation.get("url") or page.url or "")
                if not platform_url and any(marker in confirmed_url for marker in ("upload-manager", "archive", "content")):
                    platform_url = confirmed_url
                remote_id = self.runtime.extract_remote_id(platform_url)
                self.runtime.phase("confirmed_success", {"platform_url": platform_url, "remote_video_id": remote_id})
                return PublishResult(
                    outcome=PublishOutcome.PUBLISHED,
                    message="B站投稿成功",
                    remote_video_id=remote_id,
                    platform_url=platform_url,
                    published_at=utc_now_iso(),
                    provider_response={
                        "click": click_result,
                        "confirmation": confirmation,
                        "final_url": confirmed_url,
                        "default_tags_kept": True,
                    },
                )
            except PublishNeedsReview:
                raise
            except Exception as exc:
                self.runtime.screenshot(page, "bilibili-publish-error")
                if submitted:
                    raise PublishNeedsReview(
                        f"B站投稿结果不确定，请人工确认。{exc}",
                        "publish_result_uncertain",
                    ) from exc
                if isinstance(exc, PublishError):
                    raise
                raise PublishError(str(exc), "bilibili_publish_failed") from exc

    def _page_logged_in(self, page: Any) -> bool:
        text = self.runtime.body_text(page)
        return not ("扫码登录" in text or "密码登录" in text or "passport.bilibili.com" in page.url)

    def _set_tags(self, page: Any, value: str) -> None:
        tags = split_hashtags(value)
        tag_input = self.runtime.first_visible(page, ('input[placeholder*="标签"]', 'input[placeholder*="按回车键Enter创建标签"]'))
        if tag_input is None:
            return
        for tag in tags[:10]:
            tag_input.fill(tag)
            tag_input.press("Enter")

    def _set_copyright(self, page: Any, value: str, source: str) -> None:
        label = "转载" if value == "repost" else "自制"
        self.runtime.click_first(page, (f'text="{label}"', f'label:has-text("{label}")'), required=False)
        if value == "repost":
            self.runtime.fill_first(page, ('input[placeholder*="转载来源"]', 'input[placeholder*="来源"]'), source)

    def _set_partition(self, page: Any, tid: str) -> None:
        if not self.runtime.click_first(page, ('text="选择分区"', '[class*="select"]:has-text("分区")'), required=False):
            return
        self.runtime.click_first(page, (f'text="{tid}"',), required=False)

    def _set_cover(self, page: Any, cover_path: str) -> None:
        if not cover_path or not Path(cover_path).is_file():
            return
        self.runtime.click_first(page, ('text="上传封面"', 'button:has-text("上传封面")'), required=False)
        cover = self.runtime.first_visible(page, ('input[type="file"][accept*="image"]',), timeout_ms=3000)
        if cover is not None:
            cover.set_input_files(cover_path)
            self.runtime.click_first(page, ('button:has-text("完成")', 'button:has-text("确定")'), required=False)
