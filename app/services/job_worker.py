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
            elif job_type == job_service.JOB_TYPE_AI_ANALYSIS:
                _execute_ai_analysis(job_id, task_id, job.get("payload_json") or {})
            elif job_type == job_service.JOB_TYPE_AUTO_PIPELINE:
                _execute_auto_pipeline(job_id, task_id, job.get("payload_json") or {})
            elif job_type == job_service.JOB_TYPE_SUBTITLE:
                _execute_subtitle(job_id, task_id, job.get("payload_json") or {})
            else:
                raise ValueError(f"暂不支持的 job 类型：{job_type}")
        except job_service.JobLeaseLostError:
            raise
        except Exception as exc:
            if job_type == job_service.JOB_TYPE_SUBTITLE:
                from app.services.subtitle_auto_workflow_service import cleanup_interrupted_subtitle_job

                cleanup_interrupted_subtitle_job(
                    job_id,
                    lease_owner=owner,
                    lease_token=lease_token,
                    status="failed",
                    message=str(exc),
                )
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


def _execute_ai_analysis(job_id: str, task_id: str, payload: dict) -> None:
    job_service.update_job_progress(job_id, 5, "正在准备 AI 分析与恢复检查")
    result = task_service.process_task_ai_analysis(task_id, provider=payload.get("provider"))
    if job_service.is_cancel_requested(job_id):
        job_service.mark_job_cancelled(job_id, "AI 分析已取消")
        return
    job_service.update_job_progress(job_id, 95, "AI 结果已原子落库，正在完成 Job")
    meta = (result.get("analysis_run") or {}).get("analysis_meta") or {}
    job_service.mark_job_completed(
        job_id,
        {
            "task_id": task_id,
            "message": result.get("message") or "AI 分析完成",
            "analysis_run_id": result.get("analysis_run_id") or "",
            "clip_count": len(result.get("clips") or []),
            "review_url": result.get("review_url") or f"/tasks/{task_id}/clips/review",
            "analysis_incomplete": bool(meta.get("analysis_incomplete")),
            "coverage_percent": float(meta.get("coverage_percent") or 0),
        },
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
    if result.get("resume_requested"):
        from app.models.task import TaskStatus

        _completed_job, resume_job, created = job_service.mark_job_completed_with_followup(
            job_id,
            result,
            followup_task_id=task_id,
            followup_job_type=job_service.JOB_TYPE_AUTO_PIPELINE,
            followup_payload={"retry": False, "start_step": TaskStatus.METADATA_GENERATING.value},
            result_followup_key="resume_job_id",
        )
        from app.services.task_log_service import append_task_log

        append_task_log(
            task_id,
            "字幕成片全部验证通过，已排队恢复自动文案与发送中心流程"
            if created
            else f"字幕成片全部验证通过，已复用自动流水线恢复 Job：{resume_job['id']}",
        )
        return
    job_service.mark_job_completed(job_id, result)


def _job_no_progress_timeout(job: dict) -> int:
    if job.get("job_type") == job_service.JOB_TYPE_SUBTITLE:
        return max(30, int(settings.ffmpeg_subtitle_timeout))
    return max(
        30,
        int(settings.workflow_job_no_progress_timeout_seconds),
        int(settings.ffmpeg_audio_extract_timeout),
        int(settings.ffmpeg_cut_timeout),
        int(settings.volcengine_asr_timeout_seconds),
    )


def _job_progress_state(job: dict) -> tuple[int, str, str]:
    """返回真正代表业务进展的标记；Worker 自己的 heartbeat 不计入进展。"""
    return (
        int(job.get("progress") or 0),
        str(job.get("message") or ""),
        str(job.get("checkpoint_updated_at") or ""),
    )


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
            self._thread.join(timeout=25)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            job = job_service.claim_next_job(self.owner)
            if not job:
                self._stop_event.wait(self.poll_seconds)
                continue
            try:
                self._run_job_subprocess(job["id"])
            except Exception as exc:
                current = job_service.get_job(job["id"])
                if (
                    current
                    and current.get("status") == job_service.JOB_STATUS_RUNNING
                    and current.get("lease_owner") == self.owner
                    and current.get("lease_token")
                ):
                    try:
                        job_service.mark_job_failed(
                            job["id"],
                            f"Worker 收尾异常：{exc}",
                            lease_owner=self.owner,
                            lease_token=str(current["lease_token"]),
                        )
                    except job_service.JobLeaseLostError:
                        pass
                self._stop_event.wait(self.poll_seconds)

    def _run_job_subprocess(self, job_id: str) -> None:
        job_before_start = job_service.get_job(job_id) or {}
        lease_token = str(job_before_start.get("lease_token") or "")
        if job_before_start.get("lease_owner") != self.owner or not lease_token:
            return
        try:
            process = popen_process_group(
                [sys.executable, "-m", "app.services.job_worker_process", job_id, self.owner, lease_token],
                cwd=str(settings.project_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            try:
                job_service.mark_job_failed(
                    job_id,
                    f"无法启动 Job 子进程：{exc}",
                    lease_owner=self.owner,
                    lease_token=lease_token,
                )
            except job_service.JobLeaseLostError:
                pass
            return
        last_heartbeat = 0.0
        last_progress_at = time.monotonic()
        previous_progress: tuple[int, str, str] | None = None
        no_progress_timeout = _job_no_progress_timeout(job_before_start)
        while process.poll() is None:
            if self._stop_event.wait(1):
                if not self._terminate_process(process, job_id, lease_token, "应用停止"):
                    time.sleep(1)
                    continue
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
                self._terminate_process(process, job_id, lease_token, "Job 记录已消失")
                return
            if (
                job.get("status") != job_service.JOB_STATUS_RUNNING
                or job.get("lease_owner") != self.owner
                or job.get("lease_token") != lease_token
            ):
                if self._terminate_process(process, job_id, lease_token, "Workflow Job 租约已改变"):
                    return
                time.sleep(1)
                continue
            if job_service.is_cancel_requested(job_id):
                if not self._terminate_process(process, job_id, lease_token, "取消任务"):
                    time.sleep(1)
                    continue
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
            progress_state = _job_progress_state(job)
            if previous_progress != progress_state:
                previous_progress = progress_state
                last_progress_at = time.monotonic()
            if time.monotonic() - last_progress_at > no_progress_timeout:
                if not self._terminate_process(process, job_id, lease_token, "任务无进展超时"):
                    time.sleep(1)
                    continue
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
                    if self._terminate_process(process, job_id, lease_token, "Workflow Job 心跳租约已失效"):
                        return
                    time.sleep(1)
                    continue
                last_heartbeat = time.monotonic()
        final_job = job_service.validate_job_lease(job_id, self.owner, lease_token)
        owns_final_lease = bool(final_job)
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
                    message="字幕 Job 子进程正常退出但没有写入终态",
                )
                if not cleaned:
                    return
            job_service.mark_job_failed(
                job_id,
                "Job 子进程已退出但没有写入终态",
                lease_owner=self.owner,
                lease_token=lease_token,
            )

    def _terminate_process(
        self,
        process: subprocess.Popen,
        job_id: str,
        lease_token: str,
        reason: str,
    ) -> bool:
        """终止失败时保留 Job/lease 并继续重试，不能让 Worker 主线程退出。"""
        try:
            terminate_process_tree(process)
            return True
        except Exception as exc:
            try:
                job_service.update_job_progress(
                    job_id,
                    int((job_service.get_job(job_id) or {}).get("progress") or 0),
                    f"{reason}时无法确认子进程已停止，将继续重试：{exc}",
                    lease_owner=self.owner,
                    lease_token=lease_token,
                )
            except (job_service.JobLeaseLostError, ValueError):
                pass
            return False
