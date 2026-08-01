from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from app.services.publishers import page_scripts
from app.services.publishers.base import PublishError, PublishNeedsReview, PublishOutcome
from app.services.publishers.bilibili import BilibiliPublisher
from app.services.publishers.browser_runtime import BrowserRuntime
from app.services.publishers.douyin import DouyinPublisher


class FakeUpload:
    def __init__(self) -> None:
        self.files: list[str] = []

    def set_input_files(self, value: str) -> None:
        self.files.append(value)


class FakePage:
    url = "https://creator.example.test/upload"


class FakeRuntime:
    def __init__(self, platform: str, *, confirmed: bool = True) -> None:
        self.platform = platform
        self.confirmed = confirmed
        self.upload = FakeUpload()
        self.phases: list[str] = []
        self.scripts: list[str] = []

    @contextmanager
    def page(self, _url: str):
        yield FakePage()

    def phase(self, phase: str, _details=None) -> None:
        self.phases.append(phase)

    def body_text(self, _page) -> str:
        return "上传视频 基础设置"

    def detect_manual_challenge(self, _page) -> None:
        return None

    def first_visible(self, _page, _selectors, timeout_ms=1500):
        del timeout_ms
        return self.upload

    def wait_for_text(self, _page, _patterns, timeout_seconds: int) -> str:
        del timeout_seconds
        return "视频上传成功"

    def wait_for_script_state(
        self,
        _page,
        script,
        *,
        phase,
        ready_key,
        timeout_seconds,
        timeout_error_code,
        timeout_message,
        stable_polls=1,
        interval_seconds=1.0,
    ):
        del script, timeout_seconds, timeout_error_code, timeout_message, interval_seconds
        self.phases.append(phase)
        return {ready_key: True, "state": "ready", "stable_polls": stable_polls}

    def evaluate_script(self, _page, script: str, *, phase: str, default_error_code="platform_form_changed"):
        del default_error_code
        self.phases.append(phase)
        self.scripts.append(script)
        if "return {publish_confirmed:true" in script:
            return {
                "publish_confirmed": self.confirmed,
                "success_text": "发布成功" if self.confirmed else "",
                "url": "https://creator.douyin.com/creator-micro/content/manage",
            }
        if "return {bilibili_publish_confirmed:true" in script:
            return {
                "bilibili_publish_confirmed": self.confirmed,
                "success_text": "投稿成功" if self.confirmed else "",
                "url": "https://member.bilibili.com/platform/upload-manager/article",
            }
        if "return {clicked:true" in script:
            return {"clicked": True, "text": "发布"}
        if "visibility_verified:true" in script:
            return {"visibility_verified": True, "visibility_text": "仅自己可见"}
        return {"ok": True}

    def click_first(self, _page, _selectors, *, required=True) -> bool:
        del required
        return False

    def extract_link(self, _page, _patterns) -> str:
        return ""

    def extract_remote_id(self, url: str) -> str:
        return BrowserRuntime.extract_remote_id(url)

    def screenshot(self, _page, _name: str) -> str:
        return ""

    def hold_for_manual_review(self, _page, _message, _error_code, **_kwargs) -> None:
        self.phases.append("manual_review_waiting")


def make_job(tmp_path: Path, platform: str) -> dict:
    video = tmp_path / f"{platform}.mp4"
    cover = tmp_path / f"{platform}.jpg"
    video.write_bytes(b"video")
    cover.write_bytes(b"cover")
    job = {
        "id": f"job-{platform}",
        "platform": platform,
        "account_id": f"account-{platform}",
        "video_path": str(video),
        "title": "测试标题",
        "description": "测试正文",
        "caption": "测试正文",
        "tags": "测试,视频",
        "hashtags": "测试,视频",
        "cover_file_path": str(cover),
        "visibility": "private",
    }
    if platform == "bilibili":
        job.update({"bilibili_tid": "娱乐", "bilibili_copyright": "original"})
    return job


@pytest.mark.parametrize(
    ("platform", "publisher_class", "required_phases"),
    [
        (
            "douyin",
            DouyinPublisher,
            {
                "upload_waiting", "upload_completed", "description_filled",
                "recommended_cover_verified", "form_verified_before_submit",
                "visibility_verified", "submit_clicked", "publish_result_checked",
            },
        ),
        (
            "bilibili",
            BilibiliPublisher,
            {"local_draft_prompt_checked", "recommended_cover_selected", "declaration_selected", "publish_result_checked"},
        ),
    ],
)
def test_playwright_publishers_reuse_robust_page_scripts(tmp_path, platform, publisher_class, required_phases):
    runtime = FakeRuntime(platform)
    result = publisher_class(runtime=runtime).publish(make_job(tmp_path, platform))
    assert result.outcome == PublishOutcome.PUBLISHED
    assert required_phases.issubset(runtime.phases)
    assert runtime.upload.files
    assert result.provider_response["confirmation"]["success_text"] in {"发布成功", "投稿成功"}
    if platform == "douyin":
        ordered = [
            "upload_waiting", "upload_completed", "title_filled", "description_filled",
            "recommended_cover_verified", "visibility_verified", "submit_clicked",
            "publish_result_checked",
        ]
        assert [runtime.phases.index(phase) for phase in ordered] == sorted(
            runtime.phases.index(phase) for phase in ordered
        )


@pytest.mark.parametrize(("platform", "publisher_class"), [("douyin", DouyinPublisher), ("bilibili", BilibiliPublisher)])
def test_no_success_evidence_never_becomes_published(tmp_path, platform, publisher_class):
    runtime = FakeRuntime(platform, confirmed=False)
    with pytest.raises(PublishNeedsReview) as caught:
        publisher_class(runtime=runtime).publish(make_job(tmp_path, platform))
    assert caught.value.error_code == "publish_result_uncertain"


def test_shared_scripts_keep_key_form_cover_and_result_markers():
    douyin_upload = page_scripts.douyin_upload_state()
    douyin_description = page_scripts.douyin_set_description("正文\n#话题")
    douyin_cover = page_scripts.douyin_verify_cover()
    douyin_visibility = page_scripts.douyin_set_visibility("private")
    douyin_result = page_scripts.douyin_wait_result("标题")
    bilibili_description = page_scripts.bilibili_set_description("简介")
    bilibili_ready = page_scripts.bilibili_verify_ready("标题", "简介")
    bilibili_result = page_scripts.bilibili_wait_result("标题")
    assert "文件解析中" in douyin_upload
    assert "progress<100" in douyin_upload
    assert "preview_count" in douyin_upload
    assert "douyin_video_upload_failed" in douyin_upload
    assert "douyin_description_editor_not_found" in douyin_description
    assert "douyin_cover_not_applied" in douyin_cover
    assert "douyin_visibility_not_applied" in douyin_visibility
    assert "仅自己可见" in douyin_visibility
    assert "douyin_publish_not_confirmed" in douyin_result
    assert "bilibili_description_field_not_found" in bilibili_description
    assert "bilibili_default_tags_kept:true" in bilibili_ready
    assert "bilibili_publish_not_confirmed" in bilibili_result


def test_douyin_worker_payload_uses_caption_and_hashtags():
    content = page_scripts.douyin_description(
        {"caption": "这是完整正文", "hashtags": "话题一, 话题二"},
        "备用标题",
    )

    assert content == "这是完整正文\n#话题一 #话题二"


def test_script_platform_block_is_manual_review():
    class BlockedPage:
        def evaluate(self, _script):
            raise RuntimeError("Error: douyin_publish_blocked:验证码")

    runtime = BrowserRuntime("douyin", "account-test")
    with pytest.raises(PublishNeedsReview) as caught:
        runtime.evaluate_script(BlockedPage(), "ignored", phase="publish_result_checked")
    assert caught.value.error_code == "douyin_publish_blocked"


def test_upload_wait_requires_two_stable_ready_polls(monkeypatch):
    class SequencePage:
        def __init__(self):
            self.results = iter([
                {"state": "processing", "upload_ready": False, "progress": 0},
                {"state": "waiting_preview", "upload_ready": False, "progress": 100},
                {"state": "ready", "upload_ready": True, "progress": 100, "preview_count": 1},
                {"state": "ready", "upload_ready": True, "progress": 100, "preview_count": 1},
            ])

        def evaluate(self, _script):
            return next(self.results)

        def locator(self, _selector):
            raise RuntimeError("no body fixture")

    monkeypatch.setattr("app.services.publishers.browser_runtime.time.sleep", lambda _seconds: None)
    phases = []
    runtime = BrowserRuntime("douyin", "account-test", phase_callback=lambda phase, details=None: phases.append((phase, details)))
    result = runtime.wait_for_script_state(
        SequencePage(),
        "upload-state",
        phase="upload_waiting",
        ready_key="upload_ready",
        timeout_seconds=5,
        timeout_error_code="video_upload_timeout",
        timeout_message="上传超时",
        stable_polls=2,
    )
    assert result["stable_polls"] == 2
    assert result["preview_count"] == 1
    assert any(details and details.get("progress") == 0 for _, details in phases)
    assert any(details and details.get("progress") == 100 and not details.get("upload_ready") for _, details in phases)


def test_upload_wait_stops_on_explicit_platform_failure(monkeypatch):
    class FailedPage:
        def evaluate(self, _script):
            return {
                "state": "failed",
                "upload_ready": False,
                "error_code": "douyin_video_upload_failed",
                "message": "视频处理失败",
            }

        def locator(self, _selector):
            raise RuntimeError("no body fixture")

    monkeypatch.setattr("app.services.publishers.browser_runtime.time.sleep", lambda _seconds: None)
    runtime = BrowserRuntime("douyin", "account-test")
    with pytest.raises(PublishError) as caught:
        runtime.wait_for_script_state(
            FailedPage(), "upload-state", phase="upload_waiting", ready_key="upload_ready",
            timeout_seconds=5, timeout_error_code="video_upload_timeout", timeout_message="上传超时",
        )
    assert caught.value.error_code == "douyin_video_upload_failed"


@pytest.mark.parametrize(
    ("visibility", "label"),
    [("public", "公开"), ("friends", "好友可见"), ("private", "仅自己可见")],
)
def test_visibility_scripts_require_verified_selected_state(visibility, label):
    script = page_scripts.douyin_set_visibility(visibility)
    assert label in script
    assert "visibility_verified:true" in script
    assert "douyin_visibility_option_not_found" in script
    assert "douyin_visibility_not_applied" in script


def test_douyin_page_scripts_against_real_chrome_dom_fixtures():
    playwright_module = pytest.importorskip("playwright.sync_api")
    playwright = playwright_module.sync_playwright().start()
    try:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # pragma: no cover - 仅无 Chrome 的 CI 跳过
            pytest.skip(f"系统 Chrome 不可用：{exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            page.set_content("<div>文件解析中，请稍等</div><div>0%</div><canvas width='640' height='360'></canvas>")
            processing = page.evaluate(page_scripts.douyin_upload_state())
            assert processing["state"] == "processing"
            assert processing["upload_ready"] is False
            assert processing["progress"] == 0

            page.set_content("<div>100%</div>")
            no_preview = page.evaluate(page_scripts.douyin_upload_state())
            assert no_preview["state"] == "waiting_preview"
            assert no_preview["upload_ready"] is False

            page.set_content("<canvas width='640' height='360' style='width:640px;height:360px'></canvas>")
            ready = page.evaluate(page_scripts.douyin_upload_state())
            assert ready["state"] == "ready"
            assert ready["upload_ready"] is True
            assert ready["preview_count"] == 1

            page.set_content(
                "<canvas width='640' height='360' style='width:640px;height:360px'></canvas>"
                "<div>点击发布后，如作品还在上传中，请勿关闭页面、等待上传发布完成。</div>"
            )
            explanatory_text = page.evaluate(page_scripts.douyin_upload_state())
            assert explanatory_text["upload_ready"] is True
            assert explanatory_text["busy_marker"] == ""

            page.set_content(
                "<input placeholder='填写作品标题' value='测试标题'>"
                "<section><strong>作品描述</strong><div contenteditable='true' "
                "style='width:500px;height:100px'>测试正文 #测试话题</div></section>"
                "<canvas width='640' height='360' style='width:640px;height:360px'></canvas>"
                "<div>点击发布后，如作品还在上传中，请勿关闭页面、等待上传发布完成。</div>"
            )
            publish_ready = page.evaluate(
                page_scripts.douyin_verify_ready("测试标题", "测试正文\n#测试话题")
            )
            assert publish_ready["publish_ready"] is True
            assert publish_ready["preview_checked"] is True

            page.set_content("<div>视频处理失败</div>")
            failed = page.evaluate(page_scripts.douyin_upload_state())
            assert failed["error_code"] == "douyin_video_upload_failed"

            page.set_content(
                """
                <section><strong>谁可以看</strong>
                  <label><input type="radio" name="visibility" checked>公开</label>
                  <label><input type="radio" name="visibility">好友可见</label>
                  <label><input type="radio" name="visibility">仅自己可见</label>
                </section>
                """
            )
            visibility = page.evaluate(page_scripts.douyin_set_visibility("private"))
            assert visibility["visibility_verified"] is True
            assert visibility["visibility_text"] == "仅自己可见"
            assert page.locator('input[type="radio"]').nth(2).is_checked()
        finally:
            browser.close()
    finally:
        playwright.stop()
