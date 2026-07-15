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
                upload = self.runtime.first_visible(page, ('input[type="file"]',), timeout_ms=5000)
                if upload is None:
                    raise PublishError("未找到 B站视频上传入口", "platform_form_changed")
                self.runtime.phase("upload_started", {"video": Path(payload["video_path"]).name})
                upload.set_input_files(payload["video_path"])
                ready = self.runtime.wait_for_text(page, ("上传完成", "稿件标题", "基础设置", "立即投稿"), 900)
                if not ready:
                    raise PublishError("B站视频上传超时", "video_upload_timeout")
                self.runtime.phase("upload_completed", None)

                self.runtime.fill_first(page, (
                    'input[placeholder*="稿件标题"]',
                    'input[placeholder*="标题"]',
                    'input[maxlength="80"]',
                ), payload["title"])
                self.runtime.fill_first(page, (
                    'textarea[placeholder*="填写简介"]',
                    'textarea[placeholder*="简介"]',
                    'div[contenteditable="true"][data-placeholder*="简介"]',
                ), payload["caption"])
                self._set_tags(page, payload["hashtags"])
                self._set_copyright(page, payload["bilibili_copyright"], payload["bilibili_source"])
                self._set_partition(page, payload["bilibili_tid"])
                self._set_cover(page, payload.get("cover_file_path") or "")
                self.runtime.detect_manual_challenge(page)

                self.runtime.phase("submit_clicked", None)
                submitted = True
                self.runtime.click_first(page, (
                    'button:has-text("立即投稿")',
                    'button:has-text("投稿")',
                    '[role="button"]:has-text("立即投稿")',
                ))
                success = self.runtime.wait_for_text(page, ("投稿成功", "稿件提交成功", "提交成功"), 180)
                platform_url = self.runtime.extract_link(page, ("/video/BV", "member.bilibili.com/platform/upload-manager"))
                if not success and "upload-manager" not in page.url:
                    raise PublishNeedsReview(
                        "已点击 B站投稿，但未能确认最终结果，请在创作中心核对",
                        "publish_result_uncertain",
                    )
                if not platform_url and "upload-manager" in page.url:
                    platform_url = page.url
                remote_id = self.runtime.extract_remote_id(platform_url)
                self.runtime.phase("confirmed_success", {"platform_url": platform_url, "remote_video_id": remote_id})
                return PublishResult(
                    outcome=PublishOutcome.PUBLISHED,
                    message="B站投稿成功",
                    remote_video_id=remote_id,
                    platform_url=platform_url,
                    published_at=utc_now_iso(),
                    provider_response={"success_marker": success, "final_url": page.url},
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
