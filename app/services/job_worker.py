"""轻量本地工作流任务队列 —— Job Worker

负责在后台执行 job 记录对应的长任务。
第一轮仅接入自动切片（video_cut），后续再逐步迁移其他流程。
"""

from app.services import job_service
from app.services import task_service


def execute_job(job_id: str) -> dict:
    """根据 job 记录执行对应的后台任务

    这是一个同步函数，调用方应自行决定放在线程池还是 BackgroundTasks 中运行。
    返回最终的 job 记录。
    """
    job = job_service.get_job(job_id)
    if not job:
        raise ValueError(f"job 不存在：{job_id}")
    if job.get("status") == job_service.JOB_STATUS_CANCELLED:
        return job

    job_type = job.get("job_type")
    task_id = job.get("task_id")

    # 标记开始执行
    job = job_service.mark_job_running(job_id)
    if not job:
        raise RuntimeError(f"无法更新 job 状态：{job_id}")

    try:
        if job_type == job_service.JOB_TYPE_VIDEO_CUT:
            _execute_video_cut(job_id, task_id)
        else:
            raise ValueError(f"暂不支持的 job 类型：{job_type}")
    except Exception as exc:
        error = str(exc)
        job_service.mark_job_failed(job_id, error)
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
