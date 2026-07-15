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
    split_hashtags,
)
from app.services.publishers.browser_runtime import BrowserRuntime


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

                ready = self.runtime.wait_for_text(
                    page,
                    ("重新上传", "视频上传成功", "作品描述", "发布设置"),
                    timeout_seconds=600,
                )
                if not ready:
                    raise PublishError("抖音视频上传超时", "video_upload_timeout")
                self.runtime.phase("upload_completed", None)

                self.runtime.fill_first(page, (
                    'input[placeholder*="作品标题"]',
                    'input[placeholder*="填写标题"]',
                    'input[placeholder*="标题"]',
                ), payload["title"])
                content = payload["caption"]
                if payload["hashtags"]:
                    tags = " ".join(f"#{part}" for part in split_hashtags(payload["hashtags"]))
                    content = f"{content}\n{tags}".strip()
                self.runtime.fill_first(page, (
                    'div[contenteditable="true"][data-placeholder*="作品描述"]',
                    'div[contenteditable="true"][data-placeholder*="简介"]',
                    'div[contenteditable="true"]',
                    'textarea[placeholder*="作品描述"]',
                ), content)

                self._set_cover(page, payload.get("cover_file_path") or "")
                self._set_visibility(page, payload.get("visibility") or "public")
                self.runtime.detect_manual_challenge(page)
                self.runtime.phase("submit_clicked", None)
                submitted = True
                self.runtime.click_first(page, (
                    'button:has-text("发布")',
                    '[role="button"]:has-text("发布")',
                ))

                success = self.runtime.wait_for_text(page, ("发布成功", "作品发布成功", "投稿成功"), 120)
                platform_url = self.runtime.extract_link(page, ("/video/", "/creator-micro/content/manage"))
                if not success and "/content/manage" not in page.url:
                    raise PublishNeedsReview(
                        "已点击抖音发布，但未能确认最终结果，请在创作者中心核对",
                        "publish_result_uncertain",
                    )
                if not platform_url and "/content/manage" in page.url:
                    platform_url = page.url
                remote_id = self.runtime.extract_remote_id(platform_url)
                self.runtime.phase("confirmed_success", {"platform_url": platform_url, "remote_video_id": remote_id})
                return PublishResult(
                    outcome=PublishOutcome.PUBLISHED,
                    message="抖音投稿成功",
                    remote_video_id=remote_id,
                    platform_url=platform_url,
                    published_at=utc_now_iso(),
                    provider_response={"success_marker": success, "final_url": page.url},
                )
            except PublishNeedsReview:
                raise
            except Exception as exc:
                self.runtime.screenshot(page, "douyin-publish-error")
                if submitted:
                    raise PublishNeedsReview(
                        f"抖音投稿结果不确定，请人工确认。{exc}",
                        "publish_result_uncertain",
                    ) from exc
                if isinstance(exc, PublishError):
                    raise
                raise PublishError(str(exc), "douyin_publish_failed") from exc

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
