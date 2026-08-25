"""视频切割工作流服务

从 task_service 中拆分出来的视频切片流程函数。
支持 cut_run 版本化：每次切片创建新的 run，成功后才切换 active，失败保留旧结果。
"""

from pathlib import Path
import shutil
from sqlite3 import Connection
from uuid import uuid4

from app.core.config import settings
from app.models.task import TaskStatus
from app.services.storage_service import get_artifact_paths, get_source_video_path, validate_source_video_path
from app.services.task_log_service import append_task_log
from app.services.video_cut_service import CutResult, cut_clips, parse_time_to_seconds


# ---------- Cut Run 数据库操作 ----------

def _next_cut_run_number(task_id: str, connection: Connection | None = None) -> int:
    from app.db.database import get_connection

    if connection is not None:
        row = connection.execute(
            "SELECT COALESCE(MAX(run_number), 0) AS max_run_number FROM cut_runs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return int(row["max_run_number"] or 0) + 1
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(run_number), 0) AS max_run_number FROM cut_runs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    return int(row["max_run_number"] or 0) + 1


def _create_cut_run(task_id: str) -> dict:
    """创建新的切割 run，状态为 processing，is_active=0"""
    from app.db.database import get_connection
    from app.services import job_service
    from app.services.task_service import _now_iso, STATUS_PROGRESS

    run_id = uuid4().hex[:12]
    active_lease = job_service.current_job_lease()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = _now_iso()
        if active_lease:
            workflow_job_id, lease_owner, lease_token = active_lease
            valid_lease = connection.execute(
                """
                SELECT 1 FROM workflow_jobs
                WHERE id = ? AND task_id = ? AND status = 'running'
                  AND lease_owner = ? AND lease_token = ?
                  AND lease_expires_at > ? AND cancel_requested = 0
                """,
                (workflow_job_id, task_id, lease_owner, lease_token, now),
            ).fetchone()
            if not valid_lease:
                connection.rollback()
                raise job_service.JobLeaseLostError(
                    f"Workflow Job 租约已失效，拒绝创建切片批次：{workflow_job_id}"
                )
        run_number = _next_cut_run_number(task_id, connection)
        connection.execute(
            """
            INSERT INTO cut_runs (id, task_id, run_number, status, is_active, created_at, updated_at)
            VALUES (?, ?, ?, 'processing', 0, ?, ?)
            """,
            (run_id, task_id, run_number, now, now),
        )
        final_now = _now_iso()
        lease_condition = ""
        lease_params: tuple[str, ...] = ()
        if active_lease:
            workflow_job_id, lease_owner, lease_token = active_lease
            lease_condition = """
                AND EXISTS (
                    SELECT 1 FROM workflow_jobs
                    WHERE id = ? AND task_id = ? AND status = 'running'
                      AND lease_owner = ? AND lease_token = ?
                      AND lease_expires_at > ? AND cancel_requested = 0
                )
            """
            lease_params = (workflow_job_id, task_id, lease_owner, lease_token, final_now)
        cursor = connection.execute(
            f"""
            UPDATE tasks
            SET status = ?, progress = ?, error_message = NULL, last_error = NULL, updated_at = ?
            WHERE id = ? AND COALESCE(is_deleted, 0) = 0 AND status != ?
              {lease_condition}
            """,
            (
                TaskStatus.cutting.value,
                STATUS_PROGRESS.get(TaskStatus.cutting.value, 0),
                final_now,
                task_id,
                TaskStatus.CANCELLED.value,
                *lease_params,
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise RuntimeError("任务已取消、删除或不存在，不能创建新的切片批次")
        connection.commit()
    return {"id": run_id, "run_number": run_number, "task_id": task_id}


def _cut_run_output_dir(clips_dir: Path, cut_run: dict) -> Path:
    """每个 cut_run 使用独立目录，重试或并发不会覆盖同名媒体。"""
    return clips_dir / f"run_{int(cut_run['run_number']):04d}_{cut_run['id']}"


def _activate_cut_run(task_id: str, run_id: str) -> None:
    """激活指定的 cut_run，同时将该 task 下其他 run 和旧 output_clip 标记为非活跃"""
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        # 将同 task 下所有其他 cut_run 设为非活跃
        connection.execute(
            "UPDATE cut_runs SET is_active = 0, updated_at = ? WHERE task_id = ? AND id != ?",
            (now, task_id, run_id),
        )
        # 激活当前 run
        connection.execute(
            "UPDATE cut_runs SET is_active = 1, status = 'completed', updated_at = ? WHERE id = ?",
            (now, run_id),
        )
        # 将同 task 下旧的 output_clip 设为非活跃
        connection.execute(
            "UPDATE output_clip SET is_active = 0 WHERE task_id = ? AND (cut_run_id IS NULL OR cut_run_id != ?)",
            (task_id, run_id),
        )
        connection.commit()


def _fail_cut_run(run_id: str, error_message: str = "") -> None:
    """将 cut_run 标记为失败"""
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE cut_runs
            SET status = 'failed', error_message = ?, updated_at = ?
            WHERE id = ? AND status = 'processing'
            """,
            (error_message, now, run_id),
        )
        connection.commit()


def _finalize_task_for_cut_run(
    task_id: str,
    cut_run_id: str,
    status: TaskStatus,
    error_message: str | None,
    *,
    connection: Connection | None = None,
) -> bool:
    """仅允许最新 cut_run 在有效 Workflow Job 代际内写回 Task 主状态。"""
    from app.db.database import get_connection
    from app.services import job_service
    from app.services.task_service import _now_iso, STATUS_PROGRESS

    if connection is None:
        with get_connection() as owned_connection:
            owned_connection.execute("BEGIN IMMEDIATE")
            try:
                finalized = _finalize_task_for_cut_run(
                    task_id,
                    cut_run_id,
                    status,
                    error_message,
                    connection=owned_connection,
                )
                owned_connection.commit()
                return finalized
            except Exception:
                owned_connection.rollback()
                raise

    now = _now_iso()
    active_lease = job_service.current_job_lease()
    lease_condition = ""
    lease_params: tuple[str, ...] = ()
    if active_lease:
        workflow_job_id, lease_owner, lease_token = active_lease
        lease_condition = """
            AND EXISTS (
                SELECT 1 FROM workflow_jobs
                WHERE id = ? AND task_id = ? AND status = 'running'
                  AND lease_owner = ? AND lease_token = ?
                  AND lease_expires_at > ? AND cancel_requested = 0
            )
        """
        lease_params = (workflow_job_id, task_id, lease_owner, lease_token, now)
    cursor = connection.execute(
            f"""
            UPDATE tasks
            SET status = ?, progress = ?, error_message = ?, last_error = ?, updated_at = ?
            WHERE id = ? AND COALESCE(is_deleted, 0) = 0 AND status = ?
              AND EXISTS (
                  SELECT 1 FROM cut_runs current_run
                  WHERE current_run.id = ? AND current_run.task_id = ?
                    AND NOT EXISTS (
                        SELECT 1 FROM cut_runs newer_run
                        WHERE newer_run.task_id = current_run.task_id
                          AND newer_run.run_number > current_run.run_number
                    )
              )
              {lease_condition}
            """,
            (
                status.value,
                STATUS_PROGRESS.get(status.value, 0),
                error_message,
                error_message,
                now,
                task_id,
                TaskStatus.cutting.value,
                cut_run_id,
                task_id,
                *lease_params,
            ),
        )
    return cursor.rowcount == 1


# ---------- Output Clip 数据库操作 ----------

def _insert_output_clip_record(
    task_id: str,
    cut_run_id: str,
    result: CutResult,
    *,
    source_fingerprint: str = "",
    connection: Connection | None = None,
    is_active: bool = True,
) -> None:
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    if connection is None:
        with get_connection() as owned_connection:
            _insert_output_clip_record_on_connection(
                owned_connection,
                task_id,
                cut_run_id,
                result,
                source_fingerprint=source_fingerprint,
                is_active=is_active,
                now=now,
            )
            owned_connection.commit()
        return
    _insert_output_clip_record_on_connection(
        connection,
        task_id,
        cut_run_id,
        result,
        source_fingerprint=source_fingerprint,
        is_active=is_active,
        now=now,
    )


def _insert_output_clip_record_on_connection(
    connection: Connection,
    task_id: str,
    cut_run_id: str,
    result: CutResult,
    *,
    source_fingerprint: str,
    is_active: bool,
    now: str,
) -> None:
    candidate = connection.execute(
        "SELECT start_time, end_time FROM clip_candidates WHERE id = ? AND task_id = ?",
        (result.clip_candidate_id, task_id),
    ).fetchone()
    source_start_ms = None
    source_end_ms = None
    snapshot_source = "legacy_inferred"
    if candidate:
        source_start_ms = round(parse_time_to_seconds(candidate["start_time"]) * 1000)
        source_end_ms = round(parse_time_to_seconds(candidate["end_time"]) * 1000)
        snapshot_source = "cut_commit"
    connection.execute(
        """
        INSERT INTO output_clip (
            id, task_id, clip_candidate_id, output_file_path, output_file_name,
            status, error_message, cut_run_id, is_active,
            source_start_ms, source_end_ms, source_duration_ms,
            source_fingerprint, snapshot_source, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid4().hex[:12],
            task_id,
            result.clip_candidate_id,
            result.output_file_path,
            result.output_file_name,
            result.status,
            result.error_message,
            cut_run_id,
            1 if is_active else 0,
            source_start_ms,
            source_end_ms,
            source_end_ms - source_start_ms if source_start_ms is not None and source_end_ms is not None else None,
            source_fingerprint,
            snapshot_source,
            now,
            now,
        ),
    )


def _commit_cut_run_results(
    task_id: str,
    cut_run_id: str,
    results: list[CutResult],
    *,
    source_fingerprint: str,
    error_message: str = "",
) -> dict[str, bool]:
    """原子写入一个批次的所有结果，并按 run_number 决定是否激活。"""
    from app.db.database import get_connection
    from app.services import job_service
    from app.services.task_service import _now_iso

    active_lease = job_service.current_job_lease()
    try:
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = _now_iso()
            if active_lease:
                workflow_job_id, lease_owner, lease_token = active_lease
                valid_lease = connection.execute(
                    """
                    SELECT 1 FROM workflow_jobs
                    WHERE id = ? AND task_id = ? AND status = 'running'
                      AND lease_owner = ? AND lease_token = ?
                      AND lease_expires_at > ? AND cancel_requested = 0
                    """,
                    (workflow_job_id, task_id, lease_owner, lease_token, now),
                ).fetchone()
                if not valid_lease:
                    raise job_service.JobLeaseLostError(
                        f"Workflow Job 租约已失效，拒绝提交切片批次：{workflow_job_id}"
                    )
            run = connection.execute(
                "SELECT run_number, status, is_active FROM cut_runs WHERE id = ? AND task_id = ?",
                (cut_run_id, task_id),
            ).fetchone()
            if not run:
                raise ValueError("切片批次不存在")
            if run["status"] in {"completed", "completed_with_errors"}:
                newer = connection.execute(
                    "SELECT 1 FROM cut_runs WHERE task_id = ? AND run_number > ? LIMIT 1",
                    (task_id, int(run["run_number"])),
                ).fetchone()
                connection.rollback()
                return {
                    "activated": bool(run["is_active"]),
                    "is_latest": newer is None,
                    "already_committed": True,
                }
            if run["status"] != "processing":
                raise RuntimeError(f"切片批次状态不是 processing：{run['status']}")

            for result in results:
                _insert_output_clip_record(
                    task_id,
                    cut_run_id,
                    result,
                    source_fingerprint=source_fingerprint,
                    connection=connection,
                    is_active=False,
                )

            has_success = any(result.status == "completed" for result in results)
            has_failures = any(result.status != "completed" for result in results)
            committed_status = (
                "completed_with_errors"
                if has_success and has_failures
                else "completed" if has_success else "failed"
            )
            newer = connection.execute(
                """
                SELECT 1
                FROM cut_runs
                WHERE task_id = ? AND run_number > ?
                LIMIT 1
                """,
                (task_id, int(run["run_number"])),
            ).fetchone()
            newer_completed = connection.execute(
                """
                SELECT 1
                FROM cut_runs
                WHERE task_id = ? AND run_number > ?
                  AND status IN ('completed', 'completed_with_errors')
                LIMIT 1
                """,
                (task_id, int(run["run_number"])),
            ).fetchone()
            is_latest = newer is None
            activated = has_success and newer_completed is None

            connection.execute(
                """
                UPDATE cut_runs
                SET status = ?, is_active = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    committed_status,
                    1 if activated else 0,
                    error_message or None,
                    now,
                    cut_run_id,
                ),
            )
            if activated:
                connection.execute(
                    "UPDATE cut_runs SET is_active = 0, updated_at = ? WHERE task_id = ? AND id != ?",
                    (now, task_id, cut_run_id),
                )
                connection.execute(
                    """
                    UPDATE output_clip
                    SET is_active = 0
                    WHERE task_id = ? AND (cut_run_id IS NULL OR cut_run_id != ?)
                    """,
                    (task_id, cut_run_id),
                )
                connection.execute(
                    "UPDATE output_clip SET is_active = 1 WHERE task_id = ? AND cut_run_id = ?",
                    (task_id, cut_run_id),
                )
            final_task_status = (
                TaskStatus.completed_with_errors
                if committed_status == "completed_with_errors"
                else TaskStatus.completed if committed_status == "completed" else TaskStatus.failed
            )
            task_finalized = _finalize_task_for_cut_run(
                task_id,
                cut_run_id,
                final_task_status,
                error_message or None,
                connection=connection,
            )
            connection.commit()
        return {
            "activated": activated,
            "is_latest": is_latest,
            "already_committed": False,
            "task_finalized": task_finalized,
        }
    except Exception as exc:
        try:
            _fail_cut_run(cut_run_id, str(exc))
        except Exception as fail_mark_error:
            raise RuntimeError(
                f"切片批次提交失败：{exc}；失败状态也未能写入：{fail_mark_error}"
            ) from exc
        raise


def _deactivate_output_clips(task_id: str, except_run_id: str | None = None) -> None:
    """将指定 task 下的 output_clip 标记为非活跃，可选择保留某个 run 的"""
    from app.db.database import get_connection

    with get_connection() as connection:
        if except_run_id:
            connection.execute(
                "UPDATE output_clip SET is_active = 0 WHERE task_id = ? AND (cut_run_id IS NULL OR cut_run_id != ?)",
                (task_id, except_run_id),
            )
        else:
            connection.execute(
                "UPDATE output_clip SET is_active = 0 WHERE task_id = ?",
                (task_id,),
            )
        connection.commit()


# ---------- 状态解析 ----------

def _resolve_final_cut_status(results: list[CutResult]) -> tuple[TaskStatus, str | None]:
    success_count = sum(1 for result in results if result.status == "completed")
    failed_results = [result for result in results if result.status == "failed"]

    if not failed_results:
        return TaskStatus.completed, None

    error_summary = "；".join(
        result.error_message or f"{result.clip_candidate_id} 切割失败"
        for result in failed_results[:3]
    )
    if success_count > 0:
        return TaskStatus.completed_with_errors, f"部分切片失败：{error_summary}"
    return TaskStatus.failed, f"全部切片失败：{error_summary}"


# ---------- 切片主流程 ----------

def process_task_video_cuts(task_id: str, *, sync_publish_jobs: bool = True) -> dict:
    from app.services.task_service import (
        get_status_label,
        get_task,
        list_enabled_clip_candidates,
        update_task_status,
    )

    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")

    from app.services.ai_analysis_workflow_service import (
        get_task_ai_analysis_meta,
        validate_ai_analysis_meta_for_cut,
    )

    meta = get_task_ai_analysis_meta(task_id)
    profile = str(task.get("selection_profile") or "general")
    if not meta:
        error = "AI 分析缺少可信的完整性元数据；请重新分析或恢复可信历史，当前不会生成切片。"
        append_task_log(task_id, f"视频切割已阻止：{error}")
        raise ValueError(error)
    meta = validate_ai_analysis_meta_for_cut(meta, profile)
    coverage = float(meta.get("coverage_percent") or 0)
    if profile == "long_live_talk" and (
        meta.get("analysis_incomplete") or float(meta.get("coverage_ratio") or 0) < 0.90
    ):
        error = (
            f"长直播分析覆盖率仅 {coverage:.2f}%，低于 90%；"
            "请先重试 AI 分析补齐缺失窗口，当前不会生成切片或同步发送中心。"
        )
        append_task_log(task_id, f"视频切割已阻止：{error}")
        raise ValueError(error)
    if meta.get("analysis_incomplete"):
        error = (
            f"{profile} AI 分析存在未完成单元，当前覆盖率 {coverage:.2f}%；"
            "请先重试 AI 分析补齐失败单元，当前不会生成切片或同步发送中心。"
        )
        append_task_log(task_id, f"视频切割已阻止：{error}")
        raise ValueError(error)
    if meta.get("quality_degraded"):
        error = (
            f"{profile} AI 分析质量评审未完整通过；"
            "候选可供人工检查，但当前不会生成切片或同步发送中心。"
        )
        append_task_log(task_id, f"视频切割已阻止：{error}")
        raise ValueError(error)

    source_path = get_source_video_path(task)
    valid, error_message = validate_source_video_path(str(source_path) if source_path else None)
    if not valid:
        update_task_status(task_id, TaskStatus.failed, error_message)
        append_task_log(task_id, f"视频切割失败：{error_message}")
        raise ValueError(error_message)

    enabled_clips = list_enabled_clip_candidates(task_id)
    if not enabled_clips:
        error = "没有任何启用片段，请先在片段审核页启用至少一条候选片段"
        update_task_status(task_id, TaskStatus.failed, error)
        append_task_log(task_id, f"视频切割失败：{error}")
        raise ValueError(error)

    paths = get_artifact_paths(task_id)
    # === 版本化：创建新的 cut_run，不删除旧记录 ===
    cut_run = _create_cut_run(task_id)
    cut_run_id = cut_run["id"]
    append_task_log(task_id, f"开始自动切割视频，启用片段数：{len(enabled_clips)}")
    append_task_log(task_id, f"创建切割批次：第 {cut_run['run_number']} 次切割")

    try:
        from app.services.transcription_checkpoint_service import fingerprint_file

        source_fingerprint = fingerprint_file(source_path)
        cut_output_dir = _cut_run_output_dir(paths["clips_dir"], cut_run)
        results = cut_clips(
            source_video=source_path,
            clips=enabled_clips,
            output_dir=cut_output_dir,
            strategy=settings.default_cut_strategy,
        )
    except Exception as exc:
        error = str(exc)
        # 失败时：标记 cut_run 为 failed，保留旧的 active output_clip
        _fail_cut_run(cut_run_id, error)
        _finalize_task_for_cut_run(task_id, cut_run_id, TaskStatus.failed, error)
        append_task_log(task_id, f"视频切割失败：{error}")
        raise

    final_status, final_error = _resolve_final_cut_status(results)
    try:
        commit_result = _commit_cut_run_results(
            task_id,
            cut_run_id,
            results,
            source_fingerprint=source_fingerprint,
            error_message=final_error or "",
        )
    except Exception as exc:
        error = f"切片文件已生成，但批次结果未能原子写入数据库：{exc}"
        try:
            if cut_output_dir.exists():
                shutil.rmtree(cut_output_dir)
        except OSError as cleanup_error:
            error = f"{error}；未提交的批次目录清理失败：{cleanup_error}"
        _finalize_task_for_cut_run(task_id, cut_run_id, TaskStatus.failed, error)
        append_task_log(task_id, error)
        raise RuntimeError(error) from exc

    for result in results:
        if result.status == "completed":
            append_task_log(task_id, f"切片完成：{result.output_file_name}")
        else:
            append_task_log(task_id, f"切片失败：{result.clip_candidate_id}，原因：{result.error_message}")

    publish_sync = None
    if final_status == TaskStatus.failed:
        append_task_log(task_id, "切割批次失败，旧切片结果保留不变")
    elif commit_result["activated"] and commit_result["task_finalized"]:
        append_task_log(task_id, f"自动切割结束：{get_status_label(final_status.value)}")
        if sync_publish_jobs:
            try:
                from app.services.publish_service import sync_task_publish_jobs as sync_publish

                publish_sync = sync_publish(
                    task_id,
                    prefer_subtitled=False,
                    restore_removed=False,
                )
            except Exception as exc:
                publish_sync = {
                    "status": "partial",
                    "message": f"切片已生成，但发送中心自动同步失败：{exc}",
                    "errors": [str(exc)],
                }
                append_task_log(task_id, f"切片完成后的发送中心同步失败：{exc}")
    else:
        append_task_log(task_id, "切割批次已完成，但任务已取消或已有更新批次，未覆盖当前主状态")

    return {
        "status": final_status.value,
        "status_label": get_status_label(final_status.value),
        "message": "视频切割流程已完成",
        "output_dir": str(cut_output_dir),
        "cut_run_id": cut_run_id,
        "cut_run_number": cut_run["run_number"],
        "results": [result.__dict__ for result in results],
        "publish_sync": publish_sync,
        "task": get_task(task_id),
    }
