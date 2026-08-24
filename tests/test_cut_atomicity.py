"""P1.3：切片批次编号、结果提交和版本激活原子性。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.database import get_connection
from app.models.task import TaskCreate, TaskStatus
from app.services.task_lifecycle_service import create_task_record
from app.services.video_cut_service import CutResult


@pytest.fixture(autouse=True)
def cleanup_cut_data():
    yield
    with get_connection() as connection:
        connection.execute("DELETE FROM workflow_jobs WHERE task_id LIKE 'test-atomic-%'")
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE 'test-atomic-%'")
        connection.execute("DELETE FROM cut_runs WHERE task_id LIKE 'test-atomic-%'")
        connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE 'test-atomic-%'")
        connection.execute("DELETE FROM tasks WHERE id LIKE 'test-atomic-%'")
        connection.commit()


def _create_task(task_id: str) -> None:
    create_task_record(
        TaskCreate(
            task_name=task_id,
            source_type="upload",
            platform="general",
            selection_profile="general",
        ),
        task_id=task_id,
        task_dir_name=task_id,
    )


def _insert_candidate(task_id: str, clip_id: str) -> None:
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO clip_candidates (
                id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                summary, reason, highlight_reason, spread_value, suggested_editing,
                confidence_score, selected_by_default, enabled, reviewed, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '00:00:01', '00:00:03', 2, '', '', '', '', '', 0.8, 1, 1, 0, ?, ?)
            """,
            (clip_id, task_id, clip_id, clip_id, now, now),
        )
        connection.commit()


def _result(clip_id: str, filename: str) -> CutResult:
    return CutResult(clip_id, f"C:/tmp/{filename}", filename, "completed")


def test_concurrent_cut_run_numbers_are_unique():
    from app.services.video_cut_workflow_service import _create_cut_run

    task_id = "test-atomic-run-number"
    _create_task(task_id)

    with ThreadPoolExecutor(max_workers=4) as executor:
        runs = list(executor.map(lambda _index: _create_cut_run(task_id), range(8)))

    numbers = sorted(run["run_number"] for run in runs)
    assert numbers == list(range(1, 9))


def test_cut_result_batch_rolls_back_on_second_insert(monkeypatch):
    import app.services.video_cut_workflow_service as workflow

    task_id = "test-atomic-rollback"
    _create_task(task_id)
    _insert_candidate(task_id, "test-atomic-clip-1")
    _insert_candidate(task_id, "test-atomic-clip-2")
    run = workflow._create_cut_run(task_id)
    original_insert = workflow._insert_output_clip_record
    calls = 0

    def fail_second_insert(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("模拟第二条写入失败")
        return original_insert(*args, **kwargs)

    monkeypatch.setattr(workflow, "_insert_output_clip_record", fail_second_insert)

    with pytest.raises(RuntimeError, match="第二条写入失败"):
        workflow._commit_cut_run_results(
            task_id,
            run["id"],
            [_result("test-atomic-clip-1", "one.mp4"), _result("test-atomic-clip-2", "two.mp4")],
            source_fingerprint="fingerprint",
        )

    with get_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS total FROM output_clip WHERE cut_run_id = ?", (run["id"],)
        ).fetchone()["total"]
        run_row = connection.execute("SELECT status, is_active FROM cut_runs WHERE id = ?", (run["id"],)).fetchone()
    assert count == 0
    assert dict(run_row) == {"status": "failed", "is_active": 0}


def test_older_run_cannot_replace_newer_completed_run():
    from app.services.video_cut_workflow_service import _commit_cut_run_results, _create_cut_run

    task_id = "test-atomic-order"
    _create_task(task_id)
    _insert_candidate(task_id, "test-atomic-order-clip")
    older = _create_cut_run(task_id)
    newer = _create_cut_run(task_id)
    newest_failed = _create_cut_run(task_id)

    newer_commit = _commit_cut_run_results(
        task_id,
        newer["id"],
        [_result("test-atomic-order-clip", "newer.mp4")],
        source_fingerprint="newer",
    )
    _commit_cut_run_results(
        task_id,
        newest_failed["id"],
        [
            CutResult(
                "test-atomic-order-clip",
                "C:/tmp/failed.mp4",
                "failed.mp4",
                "failed",
                "模拟失败",
            )
        ],
        source_fingerprint="failed",
        error_message="模拟失败",
    )
    older_commit = _commit_cut_run_results(
        task_id,
        older["id"],
        [_result("test-atomic-order-clip", "older.mp4")],
        source_fingerprint="older",
    )

    assert newer_commit["activated"] is True
    assert older_commit["activated"] is False
    with get_connection() as connection:
        active = connection.execute(
            "SELECT id FROM cut_runs WHERE task_id = ? AND is_active = 1", (task_id,)
        ).fetchall()
    assert [row["id"] for row in active] == [newer["id"]]


def test_older_run_cannot_finalize_task_after_newer_run_starts():
    from app.services.task_service import get_task
    from app.services.video_cut_workflow_service import _commit_cut_run_results, _create_cut_run

    task_id = "test-atomic-task-finalize-order"
    _create_task(task_id)
    _insert_candidate(task_id, "test-atomic-task-finalize-clip")
    older = _create_cut_run(task_id)
    newer = _create_cut_run(task_id)

    older_commit = _commit_cut_run_results(
        task_id,
        older["id"],
        [_result("test-atomic-task-finalize-clip", "older-finished.mp4")],
        source_fingerprint="older-finished",
    )

    assert older_commit["task_finalized"] is False
    assert get_task(task_id, include_video_probe=False)["status"] == TaskStatus.cutting.value

    newer_commit = _commit_cut_run_results(
        task_id,
        newer["id"],
        [_result("test-atomic-task-finalize-clip", "newer-finished.mp4")],
        source_fingerprint="newer-finished",
    )

    assert newer_commit["task_finalized"] is True
    assert get_task(task_id, include_video_probe=False)["status"] == TaskStatus.completed.value


def test_cut_commit_cannot_overwrite_task_status_changed_during_run():
    from app.services.task_lifecycle_service import update_task_status
    from app.services.task_service import get_task
    from app.services.video_cut_workflow_service import _commit_cut_run_results, _create_cut_run

    task_id = "test-atomic-task-status-cas"
    _create_task(task_id)
    _insert_candidate(task_id, "test-atomic-task-status-cas-clip")
    run = _create_cut_run(task_id)
    update_task_status(task_id, TaskStatus.failed, "外部流程已更新状态")

    committed = _commit_cut_run_results(
        task_id,
        run["id"],
        [_result("test-atomic-task-status-cas-clip", "status-cas.mp4")],
        source_fingerprint="status-cas",
    )

    assert committed["task_finalized"] is False
    task = get_task(task_id, include_video_probe=False)
    assert task["status"] == TaskStatus.failed.value
    assert task["last_error"] == "外部流程已更新状态"


def test_expired_worker_cannot_create_cut_run_or_write_cutting_state():
    from app.services import job_service
    from app.services.task_service import get_task
    from app.services.video_cut_workflow_service import _create_cut_run

    task_id = "test-atomic-expired-create"
    _create_task(task_id)
    job, _created = job_service.create_or_get_active_job(
        task_id=task_id,
        job_type=job_service.JOB_TYPE_VIDEO_CUT,
    )
    claimed = job_service.claim_job(job["id"], "expired-cut-owner")
    assert claimed is not None
    with get_connection() as connection:
        connection.execute(
            "UPDATE workflow_jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (job["id"],),
        )
        connection.commit()

    with job_service.job_lease_context(job["id"], "expired-cut-owner", claimed["lease_token"]):
        with pytest.raises(job_service.JobLeaseLostError):
            _create_cut_run(task_id)

    assert get_task(task_id, include_video_probe=False)["status"] == "pending_video"


def test_duplicate_commit_does_not_downgrade_completed_run():
    from app.services.video_cut_workflow_service import _commit_cut_run_results, _create_cut_run

    task_id = "test-atomic-idempotent"
    _create_task(task_id)
    _insert_candidate(task_id, "test-atomic-idempotent-clip")
    run = _create_cut_run(task_id)
    results = [_result("test-atomic-idempotent-clip", "once.mp4")]

    first = _commit_cut_run_results(task_id, run["id"], results, source_fingerprint="same")
    second = _commit_cut_run_results(task_id, run["id"], results, source_fingerprint="same")

    assert first["already_committed"] is False
    assert second["already_committed"] is True
    with get_connection() as connection:
        run_row = connection.execute("SELECT status FROM cut_runs WHERE id = ?", (run["id"],)).fetchone()
        count = connection.execute(
            "SELECT COUNT(*) AS total FROM output_clip WHERE cut_run_id = ?", (run["id"],)
        ).fetchone()["total"]
    assert run_row["status"] == "completed"
    assert count == 1


def test_partial_success_is_preserved_in_cut_run_status():
    from app.services.video_cut_workflow_service import _commit_cut_run_results, _create_cut_run

    task_id = "test-atomic-partial"
    _create_task(task_id)
    _insert_candidate(task_id, "test-atomic-partial-ok")
    _insert_candidate(task_id, "test-atomic-partial-failed")
    run = _create_cut_run(task_id)

    committed = _commit_cut_run_results(
        task_id,
        run["id"],
        [
            _result("test-atomic-partial-ok", "ok.mp4"),
            CutResult(
                "test-atomic-partial-failed",
                "C:/tmp/failed.mp4",
                "failed.mp4",
                "failed",
                "模拟失败",
            ),
        ],
        source_fingerprint="partial",
        error_message="部分失败",
    )

    assert committed["activated"] is True
    with get_connection() as connection:
        run_row = connection.execute(
            "SELECT status, error_message FROM cut_runs WHERE id = ?", (run["id"],)
        ).fetchone()
    assert dict(run_row) == {"status": "completed_with_errors", "error_message": "部分失败"}


def test_each_cut_run_uses_a_distinct_output_directory(tmp_path):
    from app.services.video_cut_workflow_service import _cut_run_output_dir

    first = {"id": "abc123", "run_number": 1}
    second = {"id": "def456", "run_number": 2}

    assert _cut_run_output_dir(tmp_path, first) != _cut_run_output_dir(tmp_path, second)
    assert _cut_run_output_dir(tmp_path, first).parent == tmp_path


def test_process_cut_commits_batch_and_returns_run_directory(monkeypatch):
    import app.services.task_service as task_service
    import app.services.video_cut_workflow_service as workflow

    task_id = "test-atomic-process"
    _create_task(task_id)
    _insert_candidate(task_id, "test-atomic-process-clip")
    source = settings.tasks_dir / "_test_inputs" / "test-atomic-process.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"fake-video")
    with get_connection() as connection:
        connection.execute(
            "UPDATE tasks SET original_video_path = ?, source_type = 'upload' WHERE id = ?",
            (str(source), task_id),
        )
        connection.commit()

    original_get_task = task_service.get_task
    monkeypatch.setattr(
        task_service,
        "get_task",
        lambda value, include_video_probe=True: original_get_task(value, include_video_probe=False),
    )

    def fake_cut_clips(*, source_video, clips, output_dir, strategy):
        del source_video, clips, strategy
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "clip.mp4"
        output.write_bytes(b"clip")
        return [CutResult("test-atomic-process-clip", str(output), output.name, "completed")]

    monkeypatch.setattr(workflow, "cut_clips", fake_cut_clips)

    result = workflow.process_task_video_cuts(task_id, sync_publish_jobs=False)

    assert Path(result["output_dir"]).name.startswith("run_0001_")
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT cut_run_id, is_active FROM output_clip WHERE task_id = ?",
            (task_id,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["cut_run_id"] == result["cut_run_id"]
    assert rows[0]["is_active"] == 1
