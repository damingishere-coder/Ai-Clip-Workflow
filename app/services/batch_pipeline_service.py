"""Batch execution policy reuses Workflow Job and the existing pipeline steps."""
import json

from app.db.database import get_connection
from app.services import job_service
from app.services.material_batch_service import task_item, require_task_source
from app.services.material_catalog_service import digest, MaterialError

PRE_CUT_STEPS = ('PREPARING_SOURCE', 'TRANSCRIBING', 'AI_ANALYZING', 'CLIP_SELECTING', 'VIDEO_CUTTING')


def configuration(connection, task_id):
    item = task_item(connection, task_id)
    if not item:
        return None
    row = connection.execute('SELECT config_json,config_sha256 FROM material_batches WHERE id=?',(item['batch_id'],)).fetchone()
    config = json.loads(row['config_json']) if row else None
    if not isinstance(config, dict) or digest(config) != row['config_sha256']:
        raise MaterialError('批次配置证据不一致')
    return config


def require_start(config, step):
    if not config.get('auto_production'):
        raise MaterialError('该批次未确认自动生产，请逐步处理；需要自动生产时请明确创建新生产任务')
    if step and step not in PRE_CUT_STEPS:
        raise MaterialError('批次自动生产只到预切审片，不能从字幕、内容准备或排期步骤启动')


def validate_execution(task_id, step, job_id):
    with get_connection() as c:
        config = configuration(c, task_id)
    if config is None:
        return None
    require_start(config, step)
    require_task_source(task_id)
    lease = job_service.require_active_job_lease()
    if not job_id or lease[0] != job_id:
        raise MaterialError('批次自动生产必须通过持久队列执行')
    from app.services.content_profile_service import read_job_snapshot
    job = job_service.get_job(job_id)
    if job['job_type'] != 'auto_pipeline':
        raise MaterialError('批次执行 Job 类型不正确')
    read_job_snapshot(job)
    return config


def finish_import(job_id, task_id, result):
    with get_connection() as c:
        config = configuration(c, task_id)
    if config and config.get('auto_production'):
        return job_service.mark_job_completed_with_followup(job_id, result,
            followup_task_id=task_id, followup_job_type='auto_pipeline',
            followup_payload={'retry':False, 'start_step':None})[0]
    return job_service.mark_job_completed(job_id, result)


def pause_for_review(task_id):
    from app.models.task import TaskStatus
    from app.services.production_review_service import manifest
    from app.services.pipeline_checkpoint_service import PipelineCheckpointError
    from app.services.task_service import update_task_status, get_task
    try:
        with get_connection() as c:
            proof = manifest(c, task_id)
        if not proof or not proof['outputs']:
            raise ValueError('预切没有完整成片证据')
    except (ValueError, OSError) as exc:
        raise PipelineCheckpointError(str(exc)) from exc
    update_task_status(task_id, TaskStatus.pending_review)
    return {'status':'pending_review', 'message':'批次预切完成，等待人工检查实际成片并决定字幕方式',
            'cut_run_id':proof['cut_run_id'], 'task':get_task(task_id, include_video_probe=False)}
