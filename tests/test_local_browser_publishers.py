from __future__ import annotations

from pathlib import Path

import pytest

from app.services.publishers.base import PublishOutcome, PublishResult, PublishValidationError
from app.services.publishers.local_browser import LocalBrowserPublisher


class FakeWorker:
    def __init__(self, *, login_status: str = "normal", result: PublishResult | None = None) -> None:
        self.login_status = login_status
        self.result = result or PublishResult(
            outcome=PublishOutcome.PUBLISHED,
            message="投稿成功",
            remote_video_id="remote-123",
            platform_url="https://example.test/video/remote-123",
            published_at="2026-07-15T02:00:00+00:00",
            provider_response={"status": "confirmed", "token": "should-not-leave-worker"},
        )
        self.checked: list[tuple[str, str]] = []
        self.payloads: list[dict] = []

    def check_account(self, platform: str, account_id: str) -> dict:
        self.checked.append((platform, account_id))
        return {"login_status": self.login_status, "message": "登录正常" if self.login_status == "normal" else "需要重新登录"}

    def publish(self, payload: dict) -> PublishResult:
        self.payloads.append(payload)
        return self.result


class FakeRepository:
    def __init__(self) -> None:
        self.accounts: list[tuple] = []
        self.results: list[tuple[str, PublishResult]] = []

    def update_account_status(self, *args, **kwargs) -> None:
        self.accounts.append((args, kwargs))

    def record_provider_result(self, job_id: str, result: PublishResult) -> None:
        self.results.append((job_id, result))


def make_job(video: Path, platform: str = "douyin") -> dict:
    cover = video.with_suffix(".jpg")
    cover.write_bytes(b"fake cover")
    job = {
        "id": "job-1",
        "task_id": "task-1",
        "clip_id": "clip-1",
        "execution_id": "exec-1",
        "platform": platform,
        "publish_mode": "local_browser",
        "account_id": "account-1",
        "video_path": str(video),
        "title": "测试标题",
        "caption": "测试正文",
        "hashtags": "测试,视频",
        "cover_file_path": str(cover),
        "visibility": "private",
    }
    if platform == "bilibili":
        job.update({"bilibili_tid": "17", "bilibili_copyright": "original"})
    return job


@pytest.mark.parametrize("platform", ["douyin", "bilibili"])
def test_local_browser_checks_login_and_forwards_platform_payload(tmp_path, platform):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video")
    worker = FakeWorker()
    repository = FakeRepository()
    result = LocalBrowserPublisher(
        platform=platform,
        worker_client=worker,
        repository=repository,
    ).publish(make_job(video, platform))

    assert result.outcome == PublishOutcome.PUBLISHED
    assert worker.checked == [(platform, "account-1")]
    assert worker.payloads[0]["platform"] == platform
    assert worker.payloads[0]["video_path"] == str(video.resolve())
    assert repository.results == []


def test_login_expired_becomes_need_review_without_upload(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video")
    worker = FakeWorker(login_status="login_required")
    repository = FakeRepository()

    result = LocalBrowserPublisher(
        platform="douyin", worker_client=worker, repository=repository
    ).publish(make_job(video))

    assert result.outcome == PublishOutcome.NEED_REVIEW
    assert result.error_code == "account_login_required"
    assert result.needs_manual_review is True
    assert worker.payloads == []
    assert repository.results == []


def test_bilibili_repost_requires_source(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video")
    job = make_job(video, "bilibili")
    job["bilibili_copyright"] = "repost"
    job["bilibili_source"] = ""

    with pytest.raises(PublishValidationError) as caught:
        LocalBrowserPublisher(platform="bilibili", worker_client=FakeWorker()).publish(job)
    assert caught.value.error_code == "missing_bilibili_source"


def test_douyin_title_limit_is_validated_before_worker(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video")
    job = make_job(video)
    job["title"] = "长" * 31
    worker = FakeWorker()

    with pytest.raises(PublishValidationError) as caught:
        LocalBrowserPublisher(platform="douyin", worker_client=worker).publish(job)
    assert caught.value.error_code == "douyin_title_too_long"
    assert worker.checked == []


def test_uncertain_worker_result_remains_need_review(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video")
    uncertain = PublishResult(
        outcome=PublishOutcome.NEED_REVIEW,
        message="点击投稿后未读取到结果",
        error_code="publish_result_uncertain",
        needs_manual_review=True,
    )
    result = LocalBrowserPublisher(
        platform="douyin", worker_client=FakeWorker(result=uncertain)
    ).publish(make_job(video))
    assert result == uncertain


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [("hashtags", "", "missing_hashtags"), ("cover_file_path", "", "missing_cover")],
)
def test_local_browser_requires_topics_and_cover(tmp_path, field, value, error_code):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video")
    job = make_job(video)
    job[field] = value
    with pytest.raises(PublishValidationError) as caught:
        LocalBrowserPublisher(platform="douyin", worker_client=FakeWorker()).publish(job)
    assert caught.value.error_code == error_code
