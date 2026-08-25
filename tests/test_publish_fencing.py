from __future__ import annotations

import json
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.services import publish_scheduler as scheduler_module
from app.services.publish_repository import PublishRepository
from app.services.publish_scheduler import PublishScheduler
from app.services.publish_executor import execute_publish_job, is_publish_dispatch_active
from app.services.publishers.base import (
    PublishError,
    PublishOutcome,
    PublishResult,
    PublishValidationError,
    PublishWorkerUnavailable,
    sanitize_provider_response,
)
from app.services.publishers import manual_export as manual_export_module
from app.services.publishers.manual_export import ManualExportPublisher
from app.services.publishers.worker_client import validate_worker_identifier
from scripts import publish_host_worker as worker_module


PREFIX = "test-publish-fence-"


def _hold_execution_lock_in_child(
    state_dir: str,
    execution_id: str,
    ready,
    release,
) -> None:
    object.__setattr__(settings, "publish_worker_state_dir", Path(state_dir))
    journal = worker_module.ExecutionJournal(execution_id)
    with journal.lock:
        if not journal.lock.acquired:
            return
        ready.set()
        release.wait(timeout=10)


def _contend_lock_in_child(
    lock_path: str,
    ready,
    start,
    release,
    results,
) -> None:
    token = None
    try:
        ready.set()
        start.wait(timeout=10)
        path = Path(lock_path)
        token = worker_module._try_create_lock_file(path)
        results.put({"acquired": bool(token), "error": ""})
        if token:
            release.wait(timeout=10)
    except Exception as exc:
        results.put({"acquired": False, "error": repr(exc)})
        raise
    finally:
        worker_module._release_lock_file(Path(lock_path), token)


@pytest.fixture(autouse=True)
def clean_publish_fencing_rows():
    init_db()
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _iso(seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _seed_job(
    tmp_path: Path,
    *,
    status: str = "PUBLISHING",
    execution_id: str = "execution-current",
    publish_mode: str = "manual_export",
) -> str:
    suffix = uuid4().hex[:10]
    task_id = f"{PREFIX}{suffix}"
    clip_id = f"{PREFIX}clip-{suffix}"
    job_id = f"{PREFIX}job-{suffix}"
    video = tmp_path / f"{suffix}.mp4"
    video.write_bytes(b"fake video")
    now = _iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (id, task_name, task_dir_name, platform, status, created_at, updated_at)
            VALUES (?, ?, ?, 'douyin', 'COMPLETED', ?, ?)
            """,
            (task_id, task_id, task_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, output_file_path, output_file_name, status, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, 'clip.mp4', 'completed', 1, ?, ?)
            """,
            (clip_id, task_id, str(video), now, now),
        )
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption,
                tags, hashtags, risk_flags, scheduled_at, schedule_timezone, timezone,
                status, worker_id, execution_id, execution_phase, provider_response,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'douyin', ?, 'original', ?, ?,
                '测试标题', '测试正文', '测试正文', '测试', '测试', '[]', ?,
                'Asia/Shanghai', 'Asia/Shanghai', ?, 'worker-new', ?, 'claimed', ?, ?, ?)
            """,
            (
                job_id,
                task_id,
                clip_id,
                clip_id,
                publish_mode,
                str(video),
                str(video),
                _iso(-60),
                status,
                execution_id,
                json.dumps({"original": True}),
                now,
                now,
            ),
        )
        connection.commit()
    return job_id


def _raw(job_id: str) -> dict:
    with get_connection() as connection:
        return dict(connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone())


def _identity(job_id: str) -> dict[str, str]:
    job = _raw(job_id)
    return {
        "job_id": job_id,
        "platform": str(job.get("platform") or ""),
        "account_id": str(job.get("account_id") or ""),
    }


def _event_count(job_id: str) -> int:
    with get_connection() as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM publish_job_events WHERE job_id = ?", (job_id,)
            ).fetchone()[0]
        )


def test_repository_rejects_stale_provider_result_and_phase(tmp_path):
    job_id = _seed_job(tmp_path)
    repository = PublishRepository()
    result = PublishResult(
        outcome=PublishOutcome.PUBLISHED,
        message="旧执行结果",
        remote_video_id="old-video",
        provider_response={"token": "must-not-persist"},
    )

    assert repository.record_provider_result(
        job_id, result, expected_execution_id="execution-old"
    ) is False
    assert repository.update_execution_phase(
        job_id, "upload_started", expected_execution_id="execution-old"
    ) is False

    stored = _raw(job_id)
    assert stored["execution_phase"] == "claimed"
    assert json.loads(stored["provider_response"]) == {"original": True}
    assert not stored["remote_video_id"]


def test_all_terminal_writes_reject_stale_execution_without_events(tmp_path):
    job_id = _seed_job(tmp_path)
    scheduler = PublishScheduler()
    result = PublishResult(
        outcome=PublishOutcome.PUBLISHED,
        message="旧执行结果",
        provider_response={"replacement": True},
    )
    before_events = _event_count(job_id)

    transitions = [
        scheduler._mark_published(job_id, result, expected_execution_id="execution-old"),
        scheduler._mark_exported(job_id, result, expected_execution_id="execution-old"),
        scheduler._mark_failed(
            job_id, "old_failed", "旧执行失败", result, expected_execution_id="execution-old"
        ),
        scheduler._mark_need_review(
            job_id, "old_review", "旧执行需复核", result, expected_execution_id="execution-old"
        ),
    ]

    assert {item["status"] for item in transitions} == {"skipped"}
    stored = _raw(job_id)
    assert stored["status"] == "PUBLISHING"
    assert stored["execution_id"] == "execution-current"
    assert json.loads(stored["provider_response"]) == {"original": True}
    assert _event_count(job_id) == before_events


def test_recovery_cannot_apply_old_worker_success_to_new_execution(tmp_path):
    job_id = _seed_job(tmp_path, execution_id="execution-old")

    class Worker:
        @staticmethod
        def execution(_execution_id: str) -> dict:
            with get_connection() as connection:
                connection.execute(
                    "UPDATE publish_jobs SET execution_id = 'execution-new', worker_id = 'worker-new' WHERE id = ?",
                    (job_id,),
                )
                connection.commit()
            return {
                "phase": "confirmed_success",
                "details": {"outcome": "PUBLISHED", "message": "旧执行声称成功"},
                "identity": _identity(job_id),
            }

    recovered = PublishScheduler(worker_client=Worker()).recover_interrupted_jobs()

    assert recovered == 0
    stored = _raw(job_id)
    assert stored["status"] == "PUBLISHING"
    assert stored["execution_id"] == "execution-new"
    assert json.loads(stored["provider_response"]) == {"original": True}


def test_recovery_query_failure_never_requeues_from_stale_database_phase(tmp_path):
    job_id = _seed_job(tmp_path, execution_id="execution-query-failed")
    with get_connection() as connection:
        connection.execute(
            "UPDATE publish_jobs SET execution_phase = 'received', updated_at = ? WHERE id = ?",
            (_iso(-3600), job_id),
        )
        connection.commit()

    class Worker:
        @staticmethod
        def execution(_execution_id: str) -> dict:
            raise PublishWorkerUnavailable("Worker 无法查询")

    recovered = PublishScheduler(worker_client=Worker()).recover_interrupted_jobs()

    assert recovered == 1
    stored = _raw(job_id)
    assert stored["status"] == "NEED_REVIEW"
    assert stored["error_code"] == "interrupted_publish_uncertain"


def test_recovery_keeps_execution_pending_while_worker_lock_is_active(tmp_path):
    job_id = _seed_job(tmp_path, execution_id="execution-still-running")
    with get_connection() as connection:
        connection.execute(
            "UPDATE publish_jobs SET execution_phase = 'received', updated_at = ? WHERE id = ?",
            (_iso(-3600), job_id),
        )
        connection.commit()

    class Worker:
        @staticmethod
        def execution(_execution_id: str) -> dict:
            return {
                "phase": "browser_opened",
                "details": {},
                "in_progress": True,
                "identity": _identity(job_id),
            }

    recovered = PublishScheduler(worker_client=Worker()).recover_interrupted_jobs()

    assert recovered == 0
    assert _raw(job_id)["status"] == "PUBLISHING"


def test_recovery_rejects_worker_terminal_phase_result_mismatch(tmp_path):
    job_id = _seed_job(tmp_path, execution_id="execution-mismatched-terminal")

    class Worker:
        @staticmethod
        def execution(_execution_id: str) -> dict:
            return {
                "phase": "confirmed_success",
                "details": {"outcome": "FAILED", "message": "矛盾终态"},
                "in_progress": False,
                "identity": _identity(job_id),
            }

    recovered = PublishScheduler(worker_client=Worker()).recover_interrupted_jobs()

    assert recovered == 1
    stored = _raw(job_id)
    assert stored["status"] == "NEED_REVIEW"
    assert stored["error_code"] == "recovery_result_uncertain"


def test_executor_refuses_stale_execution_before_loading_publisher(monkeypatch, tmp_path):
    job_id = _seed_job(tmp_path, execution_id="execution-new")
    monkeypatch.setattr(
        "app.services.publish_executor.get_publisher",
        lambda *_args, **_kwargs: pytest.fail("旧 execution 不得进入 Publisher"),
    )

    with pytest.raises(PublishValidationError) as caught:
        execute_publish_job(job_id, expected_execution_id="execution-old")

    assert caught.value.error_code == "publish_execution_stale"


def test_executor_reserves_dispatch_after_publisher_resolution(monkeypatch, tmp_path):
    job_id = _seed_job(tmp_path, execution_id="execution-old")

    class Publisher:
        @staticmethod
        def publish(_job):
            pytest.fail("状态已变化后不得进入真实 Publisher")

    def change_state_before_dispatch(*_args, **_kwargs):
        with get_connection() as connection:
            connection.execute(
                "UPDATE publish_jobs SET status = 'NEED_REVIEW', updated_at = ? WHERE id = ?",
                (_iso(1), job_id),
            )
            connection.commit()
        return Publisher()

    monkeypatch.setattr("app.services.publish_executor.get_publisher", change_state_before_dispatch)

    with pytest.raises(PublishValidationError) as caught:
        execute_publish_job(job_id, expected_execution_id="execution-old")

    assert caught.value.error_code == "publish_execution_stale"


def test_local_browser_reserves_dispatch_immediately_before_worker_publish(monkeypatch, tmp_path):
    job_id = _seed_job(
        tmp_path,
        execution_id="execution-local-browser",
        publish_mode="local_browser",
    )

    def publisher_factory(*_args, before_dispatch, **_kwargs):
        class Publisher:
            @staticmethod
            def publish(_job):
                with get_connection() as connection:
                    connection.execute(
                        "UPDATE publish_jobs SET status = 'NEED_REVIEW', updated_at = ? WHERE id = ?",
                        (_iso(1), job_id),
                    )
                    connection.commit()
                before_dispatch()
                pytest.fail("dispatch CAS 失败后不得请求 Worker /publish")

        return Publisher()

    monkeypatch.setattr("app.services.publish_executor.get_publisher", publisher_factory)

    with pytest.raises(PublishValidationError) as caught:
        execute_publish_job(job_id, expected_execution_id="execution-local-browser")

    assert caught.value.error_code == "publish_execution_stale"
    assert not is_publish_dispatch_active("execution-local-browser")


def test_executor_marks_execution_active_before_dispatch_cas(monkeypatch, tmp_path):
    execution_id = "execution-cas-window"
    job_id = _seed_job(tmp_path, execution_id=execution_id)
    repository = PublishRepository()
    original = repository.begin_execution_dispatch
    observed: list[bool] = []

    def guarded_reservation(*args, **kwargs):
        observed.append(is_publish_dispatch_active(execution_id))
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "begin_execution_dispatch", guarded_reservation)

    class Publisher:
        @staticmethod
        def publish(_job):
            return PublishResult(
                outcome=PublishOutcome.NEED_REVIEW,
                message="仅测试 dispatch CAS 窗口",
                needs_manual_review=True,
            )

    monkeypatch.setattr(
        "app.services.publish_executor.get_publisher",
        lambda *_args, **_kwargs: Publisher(),
    )

    result = execute_publish_job(
        job_id,
        expected_execution_id=execution_id,
        repository=repository,
    )

    assert result["outcome"] == "NEED_REVIEW"
    assert observed == [True]
    assert not is_publish_dispatch_active(execution_id)


def test_recovery_does_not_interrupt_active_dispatch_window(monkeypatch, tmp_path):
    job_id = _seed_job(
        tmp_path,
        execution_id="execution-active-dispatch",
        publish_mode="local_browser",
    )

    def publisher_factory(*_args, before_dispatch, **_kwargs):
        class Publisher:
            @staticmethod
            def publish(_job):
                before_dispatch()
                with get_connection() as connection:
                    connection.execute(
                        "UPDATE publish_jobs SET updated_at = ? WHERE id = ?",
                        (_iso(-3600), job_id),
                    )
                    connection.commit()
                assert PublishScheduler().recover_interrupted_jobs() == 0
                assert _raw(job_id)["status"] == "PUBLISHING"
                return PublishResult(
                    outcome=PublishOutcome.NEED_REVIEW,
                    message="仅测试 dispatch 临界区",
                    needs_manual_review=True,
                )

        return Publisher()

    monkeypatch.setattr("app.services.publish_executor.get_publisher", publisher_factory)

    result = execute_publish_job(job_id, expected_execution_id="execution-active-dispatch")

    assert result["outcome"] == "NEED_REVIEW"


def test_recovery_snapshot_cannot_override_new_dispatch_reservation(tmp_path):
    job_id = _seed_job(tmp_path, execution_id="execution-current")
    repository = PublishRepository()

    class Worker:
        @staticmethod
        def execution(_execution_id: str) -> dict:
            before = _raw(job_id)
            assert repository.begin_execution_dispatch(
                job_id,
                "execution-current",
                str(before["updated_at"]),
            )
            return {
                "phase": "received",
                "details": {},
                "in_progress": False,
                "identity": _identity(job_id),
            }

    with get_connection() as connection:
        connection.execute(
            "UPDATE publish_jobs SET updated_at = ? WHERE id = ?",
            (_iso(-3600), job_id),
        )
        connection.commit()

    recovered = PublishScheduler(worker_client=Worker()).recover_interrupted_jobs()

    assert recovered == 0
    stored = _raw(job_id)
    assert stored["status"] == "PUBLISHING"
    assert stored["execution_phase"] == "dispatching"


def test_publish_now_does_not_overwrite_same_status_concurrent_edit(monkeypatch, tmp_path):
    job_id = _seed_job(tmp_path, status="SCHEDULED", execution_id="")
    scheduler = PublishScheduler()

    def readiness(*_args, **_kwargs):
        with get_connection() as connection:
            connection.execute(
                "UPDATE publish_jobs SET title = '并发新标题', updated_at = ? WHERE id = ?",
                (_iso(10), job_id),
            )
            connection.commit()
        return {
            job_id: {
                "resolved_account_id": "",
                "resolved_publish_mode": "manual_export",
            }
        }

    monkeypatch.setattr(scheduler, "_require_ready_jobs", readiness)

    with pytest.raises(ValueError, match="状态已变化"):
        scheduler.publish_now(job_id)

    stored = _raw(job_id)
    assert stored["title"] == "并发新标题"


def test_worker_unavailable_from_stale_execution_cannot_reschedule_or_add_event(tmp_path):
    job_id = _seed_job(tmp_path, execution_id="execution-old")
    scheduler = PublishScheduler()
    captured = _raw(job_id)
    with get_connection() as connection:
        connection.execute(
            "UPDATE publish_jobs SET execution_id = 'execution-new', worker_id = 'worker-new' WHERE id = ?",
            (job_id,),
        )
        connection.commit()
    before_events = _event_count(job_id)

    result = scheduler._handle_worker_unavailable(
        captured,
        PublishWorkerUnavailable("Worker 暂不可用", request_may_have_been_received=False),
    )

    assert result["status"] == "skipped"
    stored = _raw(job_id)
    assert stored["status"] == "PUBLISHING"
    assert stored["execution_id"] == "execution-new"
    assert _event_count(job_id) == before_events


def test_worker_timeout_after_request_keeps_known_execution_pending(tmp_path):
    job_id = _seed_job(tmp_path, execution_id="execution-uploading")

    class Worker:
        @staticmethod
        def execution(_execution_id: str) -> dict:
            return {
                "phase": "upload_started",
                "details": {"message": "上传中"},
                "identity": _identity(job_id),
            }

    result = PublishScheduler(worker_client=Worker())._handle_worker_unavailable(
        _raw(job_id),
        PublishWorkerUnavailable("请求超时", request_may_have_been_received=True),
    )

    assert result["status"] == "pending"
    assert _raw(job_id)["status"] == "PUBLISHING"


def test_worker_timeout_rejects_mismatched_terminal_result(tmp_path):
    job_id = _seed_job(tmp_path, execution_id="execution-timeout-mismatch")

    class Worker:
        @staticmethod
        def execution(_execution_id: str) -> dict:
            return {
                "phase": "failed",
                "details": {"outcome": "PUBLISHED", "message": "矛盾终态"},
                "identity": _identity(job_id),
            }

    result = PublishScheduler(worker_client=Worker())._handle_worker_unavailable(
        _raw(job_id),
        PublishWorkerUnavailable("请求超时", request_may_have_been_received=True),
    )

    assert result["status"] == "need_review"
    assert _raw(job_id)["status"] == "NEED_REVIEW"


def test_concurrent_safe_repair_creates_exactly_one_replacement(monkeypatch, tmp_path):
    source_id = _seed_job(
        tmp_path,
        status="NEED_REVIEW",
        execution_id="execution-source",
        publish_mode="manual_export",
    )

    def readiness(*_args, **_kwargs):
        return {
            "repairable": True,
            "dispatch_ready": True,
            "requires_worker": False,
            "resolved_publish_mode": "local_browser",
            "resolved_account_id": "",
        }

    monkeypatch.setattr(scheduler_module, "build_send_readiness", readiness)
    scheduler = PublishScheduler()
    monkeypatch.setattr(scheduler, "_public_job", lambda job_id: scheduler.repository.get_job(job_id))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: scheduler.repair_and_publish(source_id), range(2)))

    assert sorted(item["status"] for item in results) == ["already_created", "scheduled"]
    with get_connection() as connection:
        replacements = connection.execute(
            "SELECT id FROM publish_jobs WHERE retry_of_job_id = ?", (source_id,)
        ).fetchall()
        source_events = connection.execute(
            """
            SELECT COUNT(*) FROM publish_job_events
            WHERE job_id = ? AND event_type = 'safe_repair_replacement_created'
            """,
            (source_id,),
        ).fetchone()[0]
    assert len(replacements) == 1
    assert source_events == 1


def test_safe_repair_rechecks_source_state_inside_final_transaction(monkeypatch, tmp_path):
    source_id = _seed_job(
        tmp_path,
        status="NEED_REVIEW",
        execution_id="execution-source",
        publish_mode="manual_export",
    )

    def readiness(*_args, **_kwargs):
        return {
            "repairable": True,
            "dispatch_ready": True,
            "requires_worker": True,
            "resolved_publish_mode": "local_browser",
            "resolved_account_id": "",
        }

    def change_source_state(_client):
        with get_connection() as connection:
            connection.execute(
                "UPDATE publish_jobs SET status = 'PUBLISHED', updated_at = ? WHERE id = ?",
                (_iso(1), source_id),
            )
            connection.commit()

    monkeypatch.setattr(scheduler_module, "build_send_readiness", readiness)
    monkeypatch.setattr(scheduler_module, "require_worker_available", change_source_state)

    with pytest.raises(ValueError, match="源任务状态已变化"):
        PublishScheduler().repair_and_publish(source_id)

    with get_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM publish_jobs WHERE retry_of_job_id = ?", (source_id,)
        ).fetchone()[0]
    assert count == 0


def test_concurrent_failed_retry_creates_only_one_active_replacement(monkeypatch, tmp_path):
    source_id = _seed_job(tmp_path, status="FAILED", publish_mode="manual_export")
    scheduler = PublishScheduler()
    monkeypatch.setattr(
        scheduler,
        "_require_ready_jobs",
        lambda *_args, **_kwargs: {
            source_id: {
                "resolved_publish_mode": "manual_export",
                "resolved_account_id": "",
            }
        },
    )

    def retry() -> tuple[str, str]:
        try:
            return "ok", scheduler.retry_failed(source_id)["job_id"]
        except ValueError as exc:
            return "error", str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: retry(), range(2)))

    assert [kind for kind, _ in results].count("ok") == 1
    assert [kind for kind, _ in results].count("error") == 1
    with get_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM publish_jobs WHERE retry_of_job_id = ?", (source_id,)
        ).fetchone()[0]
    assert count == 1


def test_update_schedule_cannot_overwrite_job_claimed_after_readiness(monkeypatch, tmp_path):
    job_id = _seed_job(tmp_path, status="SCHEDULED")
    scheduler = PublishScheduler()

    def readiness(*_args, **_kwargs):
        with get_connection() as connection:
            connection.execute(
                "UPDATE publish_jobs SET status = 'PUBLISHING', updated_at = ? WHERE id = ?",
                (_iso(1), job_id),
            )
            connection.commit()
        return {
            job_id: {
                "resolved_account_id": "",
                "resolved_publish_mode": "manual_export",
            }
        }

    monkeypatch.setattr(scheduler, "_require_ready_jobs", readiness)

    with pytest.raises(ValueError, match="任务状态已变化"):
        scheduler.update_schedule(job_id, _iso(3600))

    assert _raw(job_id)["status"] == "PUBLISHING"


def test_update_schedule_cannot_overwrite_same_status_concurrent_edit(monkeypatch, tmp_path):
    job_id = _seed_job(tmp_path, status="SCHEDULED")
    scheduler = PublishScheduler()

    def readiness(*_args, **_kwargs):
        with get_connection() as connection:
            connection.execute(
                "UPDATE publish_jobs SET title = '并发修改', updated_at = ? WHERE id = ?",
                (_iso(2), job_id),
            )
            connection.commit()
        return {
            job_id: {
                "resolved_account_id": "",
                "resolved_publish_mode": "manual_export",
            }
        }

    monkeypatch.setattr(scheduler, "_require_ready_jobs", readiness)

    with pytest.raises(ValueError, match="任务状态已变化"):
        scheduler.update_schedule(job_id, _iso(3600))

    stored = _raw(job_id)
    assert stored["status"] == "SCHEDULED"
    assert stored["title"] == "并发修改"


def test_batch_schedule_cannot_overwrite_concurrently_claimed_job(monkeypatch, tmp_path):
    job_id = _seed_job(tmp_path, status="SCHEDULED")
    scheduler = PublishScheduler()

    def readiness(*_args, **_kwargs):
        with get_connection() as connection:
            connection.execute(
                "UPDATE publish_jobs SET status = 'PUBLISHING', updated_at = ? WHERE id = ?",
                (_iso(3), job_id),
            )
            connection.commit()
        return {
            job_id: {
                "dispatch_ready": True,
                "resolved_account_id": "",
                "resolved_publish_mode": "manual_export",
            }
        }

    monkeypatch.setattr(scheduler, "_require_ready_jobs", readiness)

    with pytest.raises(ValueError, match="当前状态不能修改排期"):
        scheduler.update_batch_schedule(
            [job_id],
            platform="douyin",
            action="apply",
            confirmed_schedule=[{"job_id": job_id, "scheduled_at_utc": _iso(3600)}],
        )

    assert _raw(job_id)["status"] == "PUBLISHING"


def test_malformed_risk_flags_fail_closed_before_executor(tmp_path):
    job_id = _seed_job(tmp_path, status="SCHEDULED")
    with get_connection() as connection:
        connection.execute("UPDATE publish_jobs SET risk_flags = '{broken' WHERE id = ?", (job_id,))
        connection.commit()
    scheduler = PublishScheduler(
        executor=lambda *_args, **_kwargs: pytest.fail("风险字段损坏时不得调用 Publisher")
    )

    result = scheduler.execute_job(job_id)

    assert result["status"] == "need_review"
    assert _raw(job_id)["status"] == "NEED_REVIEW"


@pytest.mark.parametrize("risk_flags", ['"not-a-list"', '{"unexpected": true}', '{"risk_flags": "bad"}'])
def test_wrong_shape_risk_flags_fail_closed_before_executor(tmp_path, risk_flags):
    job_id = _seed_job(tmp_path, status="SCHEDULED")
    with get_connection() as connection:
        connection.execute("UPDATE publish_jobs SET risk_flags = ? WHERE id = ?", (risk_flags, job_id))
        connection.commit()
    scheduler = PublishScheduler(
        executor=lambda *_args, **_kwargs: pytest.fail("风险字段结构错误时不得调用 Publisher")
    )

    result = scheduler.execute_job(job_id)

    assert result["status"] == "need_review"
    assert _raw(job_id)["status"] == "NEED_REVIEW"


def test_manual_publish_url_validates_hostname_not_substring(tmp_path):
    job_id = _seed_job(tmp_path, status="NEED_REVIEW")

    with pytest.raises(ValueError, match="有效的 douyin.com"):
        PublishScheduler().mark_published_manually(
            job_id, "https://attacker.example/?next=https://douyin.com/video/1"
        )

    assert _raw(job_id)["status"] == "NEED_REVIEW"


@pytest.mark.parametrize(
    "value",
    ["../escape", r"..\escape", r"C:\escape", "a/b", "a:b", ".", "..", "CON", "con.txt", "bad.", "bad value", "bad\nvalue"],
)
def test_worker_identifier_rejects_windows_unsafe_values(value):
    with pytest.raises(PublishValidationError):
        validate_worker_identifier(value, "execution_id", max_length=160)


@pytest.fixture
def isolated_worker_state(tmp_path):
    original = settings.publish_worker_state_dir
    object.__setattr__(settings, "publish_worker_state_dir", tmp_path)
    try:
        yield tmp_path
    finally:
        object.__setattr__(settings, "publish_worker_state_dir", original)


def _worker_payload(*, execution_id: str = "execution-idempotent", account_id: str = "account-1") -> dict:
    return {
        "job_id": "job-1",
        "execution_id": execution_id,
        "platform": "douyin",
        "account_id": account_id,
        "title": "测试标题",
        "caption": "测试正文",
        "video_path": "video.mp4",
    }


def _worker_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def test_worker_same_execution_is_idempotent_under_concurrent_requests(
    monkeypatch, isolated_worker_state
):
    calls = 0
    calls_lock = threading.Lock()

    class Publisher:
        def publish(self, _values):
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.1)
            return PublishResult(
                outcome=PublishOutcome.PUBLISHED,
                message="投稿成功",
                remote_video_id="video-1",
                published_at=_iso(),
                provider_response={"token": "secret-value"},
            )

    monkeypatch.setattr(worker_module, "get_platform_publisher", lambda *_args, **_kwargs: Publisher())
    monkeypatch.setattr(
        worker_module,
        "_resolve_media_path",
        lambda raw_value, *, required: str(raw_value or "") if required else "",
    )

    def invoke():
        return TestClient(worker_module.create_worker_app(token="test-token")).post(
            "/v1/publish", headers=_worker_headers(), json=_worker_payload()
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: invoke(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert {response.json()["outcome"] for response in responses} == {"PUBLISHED"}
    assert calls == 1
    stored = worker_module.ExecutionJournal("execution-idempotent").read()
    assert stored["identity"] == {
        "job_id": "job-1",
        "platform": "douyin",
        "account_id": "account-1",
    }
    assert stored["details"]["provider_response"]["token"] == "[REDACTED]"


def test_worker_serializes_different_executions_for_same_job(
    monkeypatch, isolated_worker_state
):
    calls = 0

    class Publisher:
        def publish(self, _values):
            nonlocal calls
            calls += 1
            time.sleep(0.05)
            return PublishResult(
                outcome=PublishOutcome.PUBLISHED,
                message="投稿成功",
                published_at=_iso(),
            )

    monkeypatch.setattr(worker_module, "get_platform_publisher", lambda *_args, **_kwargs: Publisher())
    monkeypatch.setattr(
        worker_module,
        "_resolve_media_path",
        lambda raw_value, *, required: str(raw_value or "") if required else "",
    )

    def invoke(execution_id: str):
        return TestClient(worker_module.create_worker_app(token="test-token")).post(
            "/v1/publish",
            headers=_worker_headers(),
            json=_worker_payload(execution_id=execution_id),
        ).json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(invoke, ["execution-job-a", "execution-job-b"]))

    assert calls == 1
    assert {response["outcome"] for response in responses} == {"PUBLISHED", "NEED_REVIEW"}
    assert "job_execution_conflict" in {response["error_code"] for response in responses}


def test_worker_cross_process_lock_fails_closed_without_calling_publisher(
    monkeypatch, isolated_worker_state
):
    journal = worker_module.ExecutionJournal("execution-cross-process")
    token = worker_module._try_create_lock_file(journal.lock.path)
    assert token
    monkeypatch.setattr(
        worker_module,
        "get_platform_publisher",
        lambda *_args, **_kwargs: pytest.fail("跨进程锁占用时不得调用 Publisher"),
    )
    try:
        response = TestClient(worker_module.create_worker_app(token="test-token")).post(
            "/v1/publish",
            headers=_worker_headers(),
            json=_worker_payload(execution_id="execution-cross-process"),
        )
    finally:
        worker_module._release_lock_file(journal.lock.path, token)

    assert response.status_code == 200
    assert response.json()["outcome"] == "NEED_REVIEW"
    assert response.json()["error_code"] == "execution_in_progress"


def test_worker_execution_lock_blocks_a_real_second_process(
    monkeypatch, isolated_worker_state
):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_execution_lock_in_child,
        args=(str(isolated_worker_state), "execution-real-process", ready, release),
    )
    process.start()
    try:
        assert ready.wait(timeout=8)
        monkeypatch.setattr(
            worker_module,
            "get_platform_publisher",
            lambda *_args, **_kwargs: pytest.fail("另一进程持锁时不得调用 Publisher"),
        )
        response = TestClient(worker_module.create_worker_app(token="test-token")).post(
            "/v1/publish",
            headers=_worker_headers(),
            json=_worker_payload(execution_id="execution-real-process"),
        )
        assert response.json()["outcome"] == "NEED_REVIEW"
        assert response.json()["error_code"] == "execution_in_progress"
    finally:
        release.set()
        process.join(timeout=8)
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
    assert process.exitcode == 0


def test_worker_reclaims_lock_left_by_dead_process(isolated_worker_state):
    journal = worker_module.ExecutionJournal("execution-stale-lock")
    journal.lock.path.parent.mkdir(parents=True, exist_ok=True)
    journal.lock.path.write_text("2147483646:dead-process", encoding="utf-8")

    token = worker_module._try_create_lock_file(journal.lock.path)
    try:
        assert token
        token.handle.seek(0)
        assert token.handle.read().decode("utf-8") == token
    finally:
        worker_module._release_lock_file(journal.lock.path, token)


def test_two_processes_cannot_both_reclaim_same_inactive_lock(isolated_worker_state):
    context = multiprocessing.get_context("spawn")
    lock_path = Path(isolated_worker_state) / "locks" / "race.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("2147483646:dead-process", encoding="utf-8")
    start = context.Event()
    release = context.Event()
    ready = [context.Event(), context.Event()]
    results = context.Queue()
    processes = [
        context.Process(
            target=_contend_lock_in_child,
            args=(str(lock_path), ready[index], start, release, results),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    try:
        assert all(event.wait(timeout=10) for event in ready)
        start.set()
        outcomes = [results.get(timeout=10), results.get(timeout=10)]
        assert [item["error"] for item in outcomes] == ["", ""]
        assert sorted(item["acquired"] for item in outcomes) == [False, True]
    finally:
        release.set()
        for process in processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
    assert [process.exitcode for process in processes] == [0, 0]
    assert worker_module._lock_file_is_active(lock_path) is False


def test_execution_lock_releases_thread_lock_when_file_lock_creation_fails(
    monkeypatch, isolated_worker_state
):
    lock = worker_module._execution_lock("execution-lock-error")
    original = worker_module._try_create_lock_file
    monkeypatch.setattr(
        worker_module,
        "_try_create_lock_file",
        lambda _path: (_ for _ in ()).throw(PublishValidationError("锁失败", "lock_failed")),
    )
    with pytest.raises(PublishValidationError):
        with lock:
            pytest.fail("锁创建失败时不得进入临界区")

    monkeypatch.setattr(worker_module, "_try_create_lock_file", original)
    with lock:
        assert lock.acquired is True


def test_worker_rejects_execution_identity_conflict_without_republish(
    monkeypatch, isolated_worker_state
):
    calls = 0

    class Publisher:
        def publish(self, _values):
            nonlocal calls
            calls += 1
            return PublishResult(
                outcome=PublishOutcome.PUBLISHED,
                message="投稿成功",
                published_at=_iso(),
            )

    monkeypatch.setattr(worker_module, "get_platform_publisher", lambda *_args, **_kwargs: Publisher())
    monkeypatch.setattr(worker_module, "_resolve_media_path", lambda value, *, required: str(value or ""))
    client = TestClient(worker_module.create_worker_app(token="test-token"))

    first = client.post("/v1/publish", headers=_worker_headers(), json=_worker_payload())
    conflict = client.post(
        "/v1/publish",
        headers=_worker_headers(),
        json=_worker_payload(account_id="account-2"),
    )

    assert first.json()["outcome"] == "PUBLISHED"
    assert conflict.json()["outcome"] == "NEED_REVIEW"
    assert conflict.json()["error_code"] == "execution_identity_conflict"
    assert calls == 1


def test_worker_terminal_replay_requires_matching_identity(monkeypatch, isolated_worker_state):
    journal = worker_module.ExecutionJournal("execution-missing-identity")
    journal.path.write_text(
        json.dumps({
            "execution_id": "execution-missing-identity",
            "phase": "confirmed_success",
            "details": PublishResult(
                outcome=PublishOutcome.PUBLISHED,
                message="旧日志声称成功",
            ).as_dict(),
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        worker_module,
        "get_platform_publisher",
        lambda *_args, **_kwargs: pytest.fail("缺少身份的终态不得重放或再次投稿"),
    )

    response = TestClient(worker_module.create_worker_app(token="test-token")).post(
        "/v1/publish",
        headers=_worker_headers(),
        json=_worker_payload(execution_id="execution-missing-identity"),
    )

    assert response.json()["outcome"] == "NEED_REVIEW"
    assert response.json()["error_code"] == "execution_identity_missing"


def test_worker_rejects_new_execution_after_same_job_reached_upload(
    monkeypatch, isolated_worker_state
):
    old = worker_module.ExecutionJournal("execution-old-upload")
    old.update(
        "upload_started",
        {"message": "已开始上传"},
        identity={"job_id": "job-1", "platform": "douyin", "account_id": "account-1"},
    )
    monkeypatch.setattr(
        worker_module,
        "get_platform_publisher",
        lambda *_args, **_kwargs: pytest.fail("同一 job 的新 execution 不得重复投稿"),
    )

    response = TestClient(worker_module.create_worker_app(token="test-token")).post(
        "/v1/publish",
        headers=_worker_headers(),
        json=_worker_payload(execution_id="execution-new-upload"),
    )

    assert response.json()["outcome"] == "NEED_REVIEW"
    assert response.json()["error_code"] == "job_execution_conflict"


def test_worker_keeps_post_upload_script_failure_in_manual_review(
    monkeypatch, isolated_worker_state
):
    class Publisher:
        def __init__(self, runtime):
            self.runtime = runtime

        def publish(self, _values):
            self.runtime.phase("upload_started", {"message": "开始上传"})
            self.runtime.phase("title_filled", {"message": "上传后填写标题"})
            raise PublishError("页面脚本异常", "platform_form_changed")

    monkeypatch.setattr(
        worker_module,
        "get_platform_publisher",
        lambda *_args, **kwargs: Publisher(kwargs["runtime"]),
    )
    monkeypatch.setattr(
        worker_module,
        "_resolve_media_path",
        lambda raw_value, *, required: str(raw_value or "") if required else "",
    )
    client = TestClient(worker_module.create_worker_app(token="test-token"))

    first = client.post(
        "/v1/publish",
        headers=_worker_headers(),
        json=_worker_payload(execution_id="execution-post-upload"),
    )
    second = client.post(
        "/v1/publish",
        headers=_worker_headers(),
        json=_worker_payload(execution_id="execution-post-upload-retry"),
    )

    assert first.json()["outcome"] == "NEED_REVIEW"
    assert first.json()["needs_manual_review"] is True
    assert second.json()["error_code"] == "job_execution_conflict"


def test_worker_does_not_resume_after_upload_started(monkeypatch, isolated_worker_state):
    journal = worker_module.ExecutionJournal("execution-unsafe")
    journal.update(
        "upload_started",
        {"message": "上传中"},
        identity={"job_id": "job-1", "platform": "douyin", "account_id": "account-1"},
    )
    monkeypatch.setattr(
        worker_module,
        "get_platform_publisher",
        lambda *_args, **_kwargs: pytest.fail("危险阶段不得再次调用 Publisher"),
    )
    response = TestClient(worker_module.create_worker_app(token="test-token")).post(
        "/v1/publish",
        headers=_worker_headers(),
        json=_worker_payload(execution_id="execution-unsafe"),
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "NEED_REVIEW"
    assert response.json()["error_code"] == "execution_resume_unsafe"


def test_corrupt_worker_journal_fails_closed(monkeypatch, isolated_worker_state):
    journal = worker_module.ExecutionJournal("execution-corrupt")
    journal.path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(
        worker_module,
        "get_platform_publisher",
        lambda *_args, **_kwargs: pytest.fail("损坏 journal 不得调用 Publisher"),
    )
    response = TestClient(worker_module.create_worker_app(token="test-token")).post(
        "/v1/publish",
        headers=_worker_headers(),
        json=_worker_payload(execution_id="execution-corrupt"),
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "NEED_REVIEW"
    assert response.json()["error_code"] == "execution_journal_corrupt"


def test_inconsistent_terminal_worker_journal_fails_closed(monkeypatch, isolated_worker_state):
    journal = worker_module.ExecutionJournal("execution-inconsistent")
    journal.update(
        "confirmed_success",
        {"outcome": "FAILED", "message": "不一致的旧日志"},
        identity={"job_id": "job-1", "platform": "douyin", "account_id": "account-1"},
    )
    monkeypatch.setattr(
        worker_module,
        "get_platform_publisher",
        lambda *_args, **_kwargs: pytest.fail("不一致终态不得再次调用 Publisher"),
    )

    response = TestClient(worker_module.create_worker_app(token="test-token")).post(
        "/v1/publish",
        headers=_worker_headers(),
        json=_worker_payload(execution_id="execution-inconsistent"),
    )

    assert response.json()["outcome"] == "NEED_REVIEW"
    assert response.json()["error_code"] == "execution_terminal_result_inconsistent"


def test_worker_login_reserves_account_lock_before_background_task(
    monkeypatch, isolated_worker_state
):
    opened = threading.Event()
    release = threading.Event()

    class Publisher:
        @staticmethod
        def open_login(_account_id):
            opened.set()
            assert release.wait(timeout=3)

    monkeypatch.setattr(worker_module, "get_platform_publisher", lambda *_args, **_kwargs: Publisher())

    def first_login():
        return TestClient(worker_module.create_worker_app(token="test-token")).post(
            "/v1/accounts/login",
            headers=_worker_headers(),
            json={"platform": "douyin", "account_id": "account-login"},
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        first_future = pool.submit(first_login)
        assert opened.wait(timeout=3)
        second = TestClient(worker_module.create_worker_app(token="test-token")).post(
            "/v1/accounts/open-center",
            headers=_worker_headers(),
            json={"platform": "douyin", "account_id": "account-login"},
        )
        release.set()
        first = first_future.result(timeout=3)

    assert first.status_code == 202
    assert second.status_code == 409


def test_provider_response_redacts_camel_case_secret_keys():
    sanitized = sanitize_provider_response({
        "accessToken": "a",
        "refresh-token": "b",
        "session_token": "c",
        "csrfToken": "d",
        "nested": {"clientSecret": "e", "apiKey": "f", "private-key": "g"},
    })

    assert sanitized == {
        "accessToken": "[REDACTED]",
        "refresh-token": "[REDACTED]",
        "session_token": "[REDACTED]",
        "csrfToken": "[REDACTED]",
        "nested": {
            "clientSecret": "[REDACTED]",
            "apiKey": "[REDACTED]",
            "private-key": "[REDACTED]",
        },
    }


def test_manual_export_rejects_path_components_outside_export_root(tmp_path):
    publisher = ManualExportPublisher(export_dir=tmp_path / "exports")

    with pytest.raises(PublishValidationError, match="不安全路径"):
        publisher.build_package_dir({"task_id": "../outside", "clip_id": "clip-1"})


@pytest.mark.parametrize("component", ["CON", "con.txt", "trailing.", "trailing ", "bad*name"])
def test_manual_export_rejects_windows_unsafe_components(tmp_path, component):
    publisher = ManualExportPublisher(export_dir=tmp_path / "exports")

    with pytest.raises(PublishValidationError, match="不安全路径"):
        publisher.build_package_dir({"task_id": component, "clip_id": "clip-1"})


def test_manual_export_failure_keeps_previous_complete_package(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"new-video")
    publisher = ManualExportPublisher(export_dir=tmp_path / "exports")
    job = {
        "id": "job-1",
        "task_id": "task-1",
        "clip_id": "clip-1",
        "title": "测试标题",
        "caption": "测试正文",
        "video_path": str(video),
    }
    package_dir = publisher.build_package_dir(job)
    package_dir.mkdir(parents=True)
    (package_dir / "sentinel.txt").write_text("old-complete", encoding="utf-8")
    original_write_json = manual_export_module._write_json
    calls = 0

    def fail_second_json(path, value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("磁盘写入失败")
        return original_write_json(path, value)

    monkeypatch.setattr(manual_export_module, "_write_json", fail_second_json)

    with pytest.raises(OSError, match="磁盘写入失败"):
        publisher.publish(job)

    assert (package_dir / "sentinel.txt").read_text(encoding="utf-8") == "old-complete"
    assert not list(package_dir.parent.glob(".clip-1.staging-*"))


def test_worker_http_boundary_rejects_reserved_account_and_execution_ids(isolated_worker_state):
    client = TestClient(worker_module.create_worker_app(token="test-token"))

    account = client.post(
        "/v1/accounts/check",
        headers=_worker_headers(),
        json={"platform": "douyin", "account_id": "CON"},
    )
    execution = client.get("/v1/executions/a:b", headers=_worker_headers())

    assert account.status_code == 422
    assert execution.status_code == 422
