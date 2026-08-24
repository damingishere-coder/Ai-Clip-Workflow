"""单进程持久化工作流 Worker。

数据库 lease 让应用重启后可接管过期任务；默认只运行一个本地重型任务。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4

from app.services import job_service
from app.services import task_service
from app.core.config import settings
from app.services.managed_process_service import popen_process_group, terminate_process_tree


def execute_job(
    job_id: str,
    *,
    lease_owner: str | None = None,
    lease_token: str | None = None,
    already_claimed: bool = False,
) -> dict:
    """根据 job 记录执行对应的后台任务

    这是一个同步函数，调用方应自行决定放在线程池还是 BackgroundTasks 中运行。
    返回最终的 job 记录。
    """
    owner = lease_owner or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
    job = job_service.get_job(job_id)
    if not job:
        raise ValueError(f"job 不存在：{job_id}")
    if job.get("status") == job_service.JOB_STATUS_CANCELLED:
        return job

    if already_claimed:
        if not lease_owner or not lease_token:
            raise job_service.JobLeaseLostError("已领取的 Workflow Job 缺少 owner/token")
        job = job_service.validate_job_lease(job_id, lease_owner, lease_token)
        if not job:
            raise job_service.JobLeaseLostError(f"Workflow Job 启动前租约已失效：{job_id}")
    else:
        job = job_service.claim_job(job_id, owner)
        if not job:
            return job_service.get_job(job_id)
        lease_token = str(job.get("lease_token") or "")
        if not lease_token:
            raise job_service.JobLeaseLostError(f"Workflow Job claim 未生成 token：{job_id}")

    job_type = job.get("job_type")
    task_id = job.get("task_id")

    with job_service.job_lease_context(job_id, owner, lease_token):
        try:
            if job_type == job_service.JOB_TYPE_VIDEO_CUT:
                _execute_video_cut(job_id, task_id)
            elif job_type == job_service.JOB_TYPE_TRANSCRIPT:
                _execute_transcript(job_id, task_id, job.get("payload_json") or {})
            elif job_type == job_service.JOB_TYPE_AUTO_PIPELINE:
                _execute_auto_pipeline(job_id, task_id, job.get("payload_json") or {})
            elif job_type == job_service.JOB_TYPE_SUBTITLE:
                _execute_subtitle(job_id, task_id, job.get("payload_json") or {})
            else:
                raise ValueError(f"暂不支持的 job 类型：{job_type}")
        except job_service.JobLeaseLostError:
            raise
        except Exception as exc:
            job_service.mark_job_failed(job_id, str(exc))
            raise

    return job_service.get_job(job_id)


def _execute_video_cut(job_id: str, task_id: str) -> None:
    """执行自动切片 job"""
    job_service.update_job_progress(job_id, 20, "正在准备视频切割")

    try:
        result = task_service.process_task_video_cuts(task_id)
    except Exception as exc:
        job_service.update_job_progress(job_id, 20, f"切割过程出错：{exc}")
        raise

    job_service.update_job_progress(job_id, 90, "正在保存切片结果")

    # 提取关键结果信息
    result_summary = {
        "status": result.get("status"),
        "status_label": result.get("status_label"),
        "message": result.get("message"),
        "output_dir": result.get("output_dir"),
        "cut_count": len(result.get("results") or []),
        "publish_sync": result.get("publish_sync"),
    }

    job_service.mark_job_completed(job_id, result_summary)


def _execute_transcript(job_id: str, task_id: str, payload: dict) -> None:
    from app.services import transcript_workflow_service
    from app.services.storage_service import get_artifact_paths
    from app.services.transcript_service import read_transcript_progress

    job_service.update_job_progress(job_id, 5, "正在准备音频和转写 checkpoint")
    transcript_workflow_service.process_task_transcript_workflow(
        task_id,
        background_tasks=None,
        force=bool(payload.get("force")),
        provider=payload.get("provider"),
        job_id=job_id,
    )
    if job_service.is_cancel_requested(job_id):
        job_service.mark_job_cancelled(job_id, "转写已取消，已完成分块 checkpoint 会保留")
        return
    progress = read_transcript_progress(get_artifact_paths(task_id)["transcript_path"])
    if progress.get("status") == "failed":
        raise RuntimeError(str(progress.get("message") or "转写失败"))
    job_service.mark_job_completed(
        job_id,
        {"task_id": task_id, "transcript_status": progress.get("status"), "checkpoint_retained": True},
    )


def _execute_auto_pipeline(job_id: str, task_id: str, payload: dict) -> None:
    from app.services.pipeline_engine import run_auto_pipeline

    result = run_auto_pipeline(
        task_id,
        retry=bool(payload.get("retry")),
        start_step=payload.get("start_step"),
        job_id=job_id,
    )
    if job_service.is_cancel_requested(job_id):
        job_service.mark_job_cancelled(job_id, "全自动流水线已取消")
        return
    if result.get("status") == "cancelled":
        job_service.mark_job_cancelled(job_id, "全自动流水线已取消")
        return
    if result.get("status") == "failed":
        raise RuntimeError(str(result.get("last_error") or "全自动流水线失败"))
    job_service.mark_job_completed(job_id, result)


def _execute_subtitle(job_id: str, task_id: str, payload: dict) -> None:
    from app.services.subtitle_auto_workflow_service import execute_subtitle_render_job
    from app.services.subtitle_workflow_service import SubtitleRenderCancelled

    job_service.update_job_progress(job_id, 3, "正在核对已审核的字幕 revision")
    try:
        result = execute_subtitle_render_job(job_id, task_id, payload)
    except SubtitleRenderCancelled:
        job_service.mark_job_cancelled(job_id, "字幕批量烧录已取消")
        return
    if job_service.is_cancel_requested(job_id):
        job_service.mark_job_cancelled(job_id, "字幕批量烧录已取消")
        return
    job_service.mark_job_completed(job_id, result)


class WorkflowJobRunner:
    """应用生命周期内的单 worker 线程。"""

    def __init__(self, poll_seconds: float = 1.0) -> None:
        self.poll_seconds = poll_seconds
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="workflow-job-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            job = job_service.claim_next_job(self.owner)
            if not job:
                self._stop_event.wait(self.poll_seconds)
                continue
            self._run_job_subprocess(job["id"])

    def _run_job_subprocess(self, job_id: str) -> None:
        job_before_start = job_service.get_job(job_id) or {}
        lease_token = str(job_before_start.get("lease_token") or "")
        if job_before_start.get("lease_owner") != self.owner or not lease_token:
            return
        process = popen_process_group(
            [sys.executable, "-m", "app.services.job_worker_process", job_id, self.owner, lease_token],
            cwd=str(settings.project_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        last_heartbeat = 0.0
        last_progress_at = time.monotonic()
        previous_progress: tuple[int, str] | None = None
        no_progress_timeout = (
            settings.ffmpeg_subtitle_timeout
            if job_before_start.get("job_type") == job_service.JOB_TYPE_SUBTITLE
            else max(
                900,
                settings.ffmpeg_audio_extract_timeout,
                settings.ffmpeg_cut_timeout,
                settings.volcengine_asr_timeout_seconds,
            )
        )
        while process.poll() is None:
            if self._stop_event.wait(1):
                terminate_process_tree(process)
                if job_before_start.get("job_type") == job_service.JOB_TYPE_SUBTITLE:
                    from app.services.subtitle_auto_workflow_service import cleanup_interrupted_subtitle_job

                    try:
                        job_service.heartbeat_job(job_id, self.owner, lease_token)
                    except job_service.JobLeaseLostError:
                        return
                    cleaned = cleanup_interrupted_subtitle_job(
                        job_id,
                        lease_owner=self.owner,
                        lease_token=lease_token,
                        status="queued",
                        message="应用停止，等待恢复字幕烧录",
                    )
                    if not cleaned:
                        return
                job_service.release_job_lease(job_id, self.owner, lease_token)
                return
            job = job_service.get_job(job_id)
            if not job:
                terminate_process_tree(process)
                return
            if (
                job.get("status") != job_service.JOB_STATUS_RUNNING
                or job.get("lease_owner") != self.owner
                or job.get("lease_token") != lease_token
            ):
                terminate_process_tree(process)
                return
            if job_service.is_cancel_requested(job_id):
                terminate_process_tree(process)
                if job.get("job_type") == job_service.JOB_TYPE_SUBTITLE:
                    from app.services.subtitle_auto_workflow_service import cleanup_interrupted_subtitle_job

                    try:
                        job_service.heartbeat_job(job_id, self.owner, lease_token)
                    except job_service.JobLeaseLostError:
                        return
                    cleaned = cleanup_interrupted_subtitle_job(
                        job_id,
                        lease_owner=self.owner,
                        lease_token=lease_token,
                        status="cancelled",
                        message="用户已取消字幕烧录",
                    )
                    if not cleaned:
                        return
                job_service.mark_job_cancelled(
                    job_id,
                    "任务已取消，子进程树已终止",
                    lease_owner=self.owner,
                    lease_token=lease_token,
                )
                return
            progress_state = (int(job.get("progress") or 0), str(job.get("message") or ""))
            if previous_progress != progress_state:
                previous_progress = progress_state
                last_progress_at = time.monotonic()
            if time.monotonic() - last_progress_at > no_progress_timeout:
                terminate_process_tree(process)
                if job.get("job_type") == job_service.JOB_TYPE_SUBTITLE:
                    from app.services.subtitle_auto_workflow_service import cleanup_interrupted_subtitle_job

                    try:
                        job_service.heartbeat_job(job_id, self.owner, lease_token)
                    except job_service.JobLeaseLostError:
                        return
                    cleaned = cleanup_interrupted_subtitle_job(
                        job_id,
                        lease_owner=self.owner,
                        lease_token=lease_token,
                        status="failed",
                        message=f"字幕烧录连续 {no_progress_timeout} 秒没有进展",
                    )
                    if not cleaned:
                        return
                job_service.mark_job_failed(
                    job_id,
                    f"任务连续 {no_progress_timeout} 秒没有进展，已终止子进程树",
                    lease_owner=self.owner,
                    lease_token=lease_token,
                )
                return
            if time.monotonic() - last_heartbeat >= 20:
                try:
                    job_service.heartbeat_job(job_id, self.owner, lease_token)
                except job_service.JobLeaseLostError:
                    terminate_process_tree(process)
                    return
                last_heartbeat = time.monotonic()
        final_job = job_service.get_job(job_id)
        owns_final_lease = bool(
            final_job
            and final_job.get("status") == job_service.JOB_STATUS_RUNNING
            and final_job.get("lease_owner") == self.owner
            and final_job.get("lease_token") == lease_token
        )
        if owns_final_lease and int(final_job.get("cancel_requested") or 0):
            job_service.mark_job_cancelled(
                job_id,
                "任务已取消，子进程已退出",
                lease_owner=self.owner,
                lease_token=lease_token,
            )
        elif process.returncode != 0 and owns_final_lease:
            if final_job.get("job_type") == job_service.JOB_TYPE_SUBTITLE:
                from app.services.subtitle_auto_workflow_service import cleanup_interrupted_subtitle_job

                try:
                    job_service.heartbeat_job(job_id, self.owner, lease_token)
                except job_service.JobLeaseLostError:
                    return
                cleaned = cleanup_interrupted_subtitle_job(
                    job_id,
                    lease_owner=self.owner,
                    lease_token=lease_token,
                    status="failed",
                    message=f"字幕 Job 子进程异常退出，退出码：{process.returncode}",
                )
                if not cleaned:
                    return
            job_service.mark_job_failed(
                job_id,
                f"Job 子进程异常退出，退出码：{process.returncode}",
                lease_owner=self.owner,
                lease_token=lease_token,
            )
        elif process.returncode == 0 and owns_final_lease:
            job_service.mark_job_failed(
                job_id,
                "Job 子进程已退出但没有写入终态",
                lease_owner=self.owner,
                lease_token=lease_token,
            )
