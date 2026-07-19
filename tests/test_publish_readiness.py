from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.database import get_connection, init_db
from app.services.publish_readiness import SendReadinessBlocked, build_send_readiness
from app.services.publish_scheduler import PublishScheduler
from app.services.publishers.base import PublishOutcome, PublishResult, PublishWorkerUnavailable


PREFIX = "test-send-readiness-"


@pytest.fixture(autouse=True)
def clean_readiness_rows():
    init_db()
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_job_events WHERE job_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM publish_accounts WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _iso(seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _account(*, platform: str = "douyin", login_status: str = "normal", name: str = "测试账号") -> dict:
    return {
        "id": f"{PREFIX}account-{uuid4().hex[:8]}",
        "platform": platform,
        "account_name": name,
        "login_status": login_status,
        "login_message": "",
    }


def _job_payload(*, platform: str = "douyin", publish_mode: str = "local_browser", account_id: str = "") -> dict:
    return {
        "id": f"{PREFIX}unit-job",
        "status": "WAITING",
        "platform": platform,
        "publish_mode": publish_mode,
        "account_id": account_id,
        "title": "测试标题",
        "caption": "测试正文",
        "hashtags": "测试",
        "cover_file_path": "cover.jpg",
        "video_path": "video.mp4",
        "bilibili_tid": "娱乐",
        "bilibili_copyright": "original",
    }


def test_readiness_handles_no_unique_multiple_unlogged_and_mismatched_accounts():
    job = _job_payload()
    assert build_send_readiness(job, accounts=[])["action"] == "create_account"

    unique = _account()
    ready = build_send_readiness(job, accounts=[unique])
    assert ready["dispatch_ready"] is True
    assert ready["auto_selected_account"] is True
    assert ready["resolved_account_id"] == unique["id"]

    multiple = build_send_readiness(job, accounts=[unique, _account(name="第二账号")])
    assert multiple["action"] == "select_account"

    unlogged = build_send_readiness(job, accounts=[_account(login_status="login_required")])
    assert unlogged["action"] == "login_account"

    mismatch_account = _account(platform="bilibili")
    mismatch = build_send_readiness(
        _job_payload(account_id=mismatch_account["id"]),
        accounts=[mismatch_account],
    )
    assert mismatch["action"] == "select_account"


def test_manual_export_does_not_require_account_cover_tags_or_worker():
    job = _job_payload(publish_mode="manual_export")
    job.update({"hashtags": "", "cover_file_path": ""})
    readiness = build_send_readiness(job, accounts=[], worker_available=False)
    assert readiness["ready"] is True
    assert readiness["requires_worker"] is False
    assert readiness["action"] == "export"


class FakeWorker:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.publish_calls: list[dict] = []

    def health(self) -> dict:
        if not self.available:
            raise PublishWorkerUnavailable("测试 Worker 离线")
        return {"status": "ok"}

    def check_account(self, platform: str, account_id: str) -> dict:
        return {"status": "normal", "login_status": "normal", "platform": platform, "account_id": account_id}

    def publish(self, payload: dict) -> PublishResult:
        self.publish_calls.append(payload)
        return PublishResult(
            outcome=PublishOutcome.PUBLISHED,
            message="测试投稿成功",
            remote_video_id="remote-test-1",
            platform_url="https://www.douyin.com/video/test-1",
        )


def _insert_account(*, login_status: str = "normal", platform: str = "douyin") -> str:
    account_id = f"{PREFIX}account-{uuid4().hex[:8]}"
    now = _iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO publish_accounts (
                id, platform, account_name, auth_type, login_status, login_message, created_at, updated_at
            ) VALUES (?, ?, ?, 'browser_profile', ?, '', ?, ?)
            """,
            (account_id, platform, f"账号-{account_id[-4:]}", login_status, now, now),
        )
        connection.commit()
    return account_id


def _insert_job(
    tmp_path: Path,
    *,
    status: str = "WAITING",
    publish_mode: str = "opencli_publish",
    account_id: str = "",
    error_code: str = "",
    remote_video_id: str = "",
) -> str:
    suffix = uuid4().hex[:8]
    task_id = f"{PREFIX}task-{suffix}"
    clip_id = f"{PREFIX}clip-{suffix}"
    job_id = f"{PREFIX}job-{suffix}"
    video = tmp_path / f"{suffix}.mp4"
    cover = tmp_path / f"{suffix}.jpg"
    video.write_bytes(b"fake-video")
    cover.write_bytes(b"fake-cover")
    now = _iso()
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO tasks (id, task_name, task_dir_name, platform, status, created_at, updated_at) VALUES (?, ?, ?, 'douyin', 'COMPLETED', ?, ?)",
            (task_id, task_id, task_id, now, now),
        )
        connection.execute(
            "INSERT INTO output_clip (id, task_id, output_file_path, output_file_name, status, is_active, created_at, updated_at) VALUES (?, ?, ?, 'clip.mp4', 'completed', 1, ?, ?)",
            (clip_id, task_id, str(video), now, now),
        )
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, account_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption,
                tags, hashtags, cover_file_path, scheduled_at, schedule_timezone, timezone,
                status, error_code, remote_video_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'douyin', ?, 'original', ?, ?, '测试标题', '测试正文',
                '测试正文', '测试', '测试', ?, ?, 'Asia/Shanghai', 'Asia/Shanghai', ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                task_id,
                clip_id,
                clip_id,
                account_id or None,
                publish_mode,
                str(video),
                str(video),
                str(cover),
                _iso(-60),
                status,
                error_code,
                remote_video_id,
                now,
                now,
            ),
        )
        connection.commit()
    return job_id


def _raw(job_id: str) -> dict:
    with get_connection() as connection:
        return dict(connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone())


def test_unique_logged_account_keeps_legacy_job_and_creates_replacement(tmp_path):
    account_id = _insert_account()
    job_id = _insert_job(tmp_path)
    worker = FakeWorker()
    scheduler = PublishScheduler(worker_client=worker)

    scheduled = scheduler.publish_now(job_id)
    assert scheduled["status"] == "scheduled"
    replacement_id = scheduled["job_id"]
    assert replacement_id != job_id
    assert _raw(job_id)["status"] == "NEED_REVIEW"
    assert _raw(job_id)["publish_mode"] == "opencli_publish"
    assert _raw(replacement_id)["account_id"] == account_id
    assert _raw(replacement_id)["publish_mode"] == "local_browser"

    scheduler.run_once()
    assert _raw(job_id)["status"] == "NEED_REVIEW"
    assert _raw(replacement_id)["status"] == "PUBLISHED"
    assert len(worker.publish_calls) == 1


def test_worker_offline_never_changes_status_or_calls_publish(tmp_path):
    account_id = _insert_account()
    job_id = _insert_job(tmp_path, publish_mode="local_browser", account_id=account_id)
    worker = FakeWorker(available=False)
    scheduler = PublishScheduler(worker_client=worker)

    with pytest.raises(SendReadinessBlocked) as caught:
        scheduler.publish_now(job_id)
    assert caught.value.readiness["action"] == "start_worker"
    assert _raw(job_id)["status"] == "WAITING"
    assert worker.publish_calls == []

    with get_connection() as connection:
        connection.execute("UPDATE publish_jobs SET status = 'SCHEDULED' WHERE id = ?", (job_id,))
        connection.commit()
    skipped = scheduler.execute_job(job_id)
    assert skipped["error_code"] == "publish_worker_unavailable"
    assert _raw(job_id)["status"] == "SCHEDULED"
    assert worker.publish_calls == []


def test_safe_repair_keeps_original_and_creates_local_browser_replacement(tmp_path):
    account_id = _insert_account()
    source_id = _insert_job(
        tmp_path,
        status="NEED_REVIEW",
        publish_mode="opencli_publish",
        error_code="opencli_fallback_disabled",
    )
    worker = FakeWorker()
    scheduler = PublishScheduler(worker_client=worker)

    result = scheduler.repair_and_publish(source_id, visibility="private")
    replacement_id = result["job_id"]
    assert _raw(source_id)["status"] == "NEED_REVIEW"
    replacement = _raw(replacement_id)
    assert replacement["retry_of_job_id"] == source_id
    assert replacement["publish_mode"] == "local_browser"
    assert replacement["account_id"] == account_id
    assert replacement["status"] == "SCHEDULED"
    assert replacement["visibility"] == "private"

    repeated = scheduler.repair_and_publish(source_id)
    assert repeated["status"] == "already_created"
    assert repeated["job_id"] == replacement_id

    scheduler.run_once()
    assert _raw(source_id)["status"] == "NEED_REVIEW"
    assert _raw(replacement_id)["status"] == "PUBLISHED"
    assert len(worker.publish_calls) == 1


def test_uncertain_result_cannot_use_safe_repair(tmp_path):
    _insert_account()
    source_id = _insert_job(
        tmp_path,
        status="NEED_REVIEW",
        publish_mode="opencli_publish",
        error_code="publish_result_uncertain",
    )
    with pytest.raises(ValueError, match="不能自动修复"):
        PublishScheduler(worker_client=FakeWorker()).repair_and_publish(source_id)
