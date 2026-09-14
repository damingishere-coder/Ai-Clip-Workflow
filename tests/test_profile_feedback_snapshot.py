"""Queued feedback is evidence, not a live mutable learning policy."""
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.database import get_connection
from app.models.task import TaskCreate, TaskStatus, AIClipAnalysisResult
from app.services import content_profile_service as profiles, job_service
from app.services import ai_analysis_workflow_service as workflow
from app.services.ai_prompt_preset_service import get_task_ai_prompt_snapshot
from app.services.clip_feedback_service import list_recent_feedback_context
from app.services.storage_service import get_artifact_paths
from app.services.task_lifecycle_service import create_task_record, update_task_status


@pytest.fixture
def task_id():
    tid = 'feedback-snapshot-' + uuid4().hex[:12]
    create_task_record(TaskCreate(task_name='反馈冻结隔离', selection_profile='variety_comedy'), task_id=tid)
    yield tid
    with get_connection() as c:
        for table in ('clip_feedback', 'workflow_jobs', 'clip_candidates', 'ai_analysis_runs', 'task_generation_rules'):
            c.execute(f'DELETE FROM {table} WHERE task_id=?', (tid,))
        c.execute('DELETE FROM tasks WHERE id=?', (tid,))
        c.commit()


def insert_feedback(tid, note):
    with get_connection() as c:
        c.execute('''INSERT INTO clip_feedback
            (id,task_id,clip_candidate_id,selection_profile,decision,reason_code,decision_source,
             note,title_snapshot,summary_snapshot,start_time,end_time,created_at)
            VALUES (?,?,?,'variety_comedy','reject','fragmented','explicit_feedback',?,'隔离标题','','00:00:00','00:01:00',?)''',
            (uuid4().hex, tid, tid+'-source', note, '2099-01-01T00:00:00+00:00'))
        c.commit()


def test_queue_run_and_retry_preserve_feedback_despite_later_decisions(task_id, monkeypatch):
    insert_feedback(task_id, '排队时的反馈')
    paths = get_artifact_paths(task_id)
    paths['transcript_path'].parent.mkdir(parents=True, exist_ok=True)
    paths['transcript_path'].write_text('00:00:00 - 00:01:00 隔离转写', encoding='utf-8')
    update_task_status(task_id, TaskStatus.pending_ai)
    job, _ = workflow.queue_task_ai_analysis(task_id)
    frozen = profiles.read_job_snapshot(job)['feedback_context']
    assert frozen['items'][0]['note'] == '排队时的反馈'
    insert_feedback(task_id, '排队后新增的反馈')
    assert list_recent_feedback_context('variety_comedy')[0]['note'] == '排队后新增的反馈'
    observed = []
    def fake_analyze(request):
        observed.append(request.feedback_context)
        return AIClipAnalysisResult(task_id=task_id, analysis_summary='隔离结果', clips=[], analysis_meta={
            'schema_version': 2, 'expected_units': 1, 'completed_units': 1, 'failed_units': 0,
            'coverage_ratio': 1, 'coverage_percent': 100, 'analysis_incomplete': False,
            'quality_degraded': False, 'failed_stages': [], 'coverage_basis': 'recall_and_expansion_units',
        })
    monkeypatch.setattr(workflow, 'analyze_variety_comedy', fake_analyze)
    claimed = job_service.claim_job(job['id'], 'feedback-owner')
    with job_service.job_lease_context(job['id'], 'feedback-owner', claimed['lease_token']):
        workflow.process_task_ai_analysis(task_id)
        job_service.mark_job_failed(job['id'], '模拟提交后进程退出')
    assert observed == [frozen['items']]
    run = workflow.get_latest_ai_analysis_run(task_id)
    assert run['analysis_meta']['feedback_context'] == frozen
    retried = job_service.retry_job(job['id'])
    assert retried['status'] == job_service.JOB_STATUS_QUEUED
    assert profiles.read_job_snapshot(retried)['feedback_context'] == frozen


def test_empty_snapshot_is_distinct_from_legacy_missing_context(task_id, monkeypatch):
    from app.services import clip_feedback_service
    monkeypatch.setattr(clip_feedback_service, 'list_recent_feedback_context_with_connection', lambda *_: [])
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AI_ANALYSIS)
    record = job['payload_json'][profiles.JOB_SNAPSHOT_KEY]
    assert profiles.read_job_snapshot(job)['feedback_context']['items'] == []
    captured = []
    monkeypatch.setattr(workflow, 'analyze_variety_comedy', lambda request: captured.append(request.feedback_context))
    prompt = get_task_ai_prompt_snapshot(task_id)
    task = {'selection_profile': 'variety_comedy', 'candidate_clip_count': 12, 'final_clip_target': 5,
            '_analysis_feedback_context': []}
    paths = {'transcript_path': Path('unused'), 'audio_path': Path('unused')}
    workflow._analyze_with_provider(task_id, task, paths, 'codex', prompt)
    record['snapshot'].pop('feedback_context')
    record['sha256'] = profiles._snapshot_hash(record['snapshot'])
    old_checkpoint = json.dumps(job['checkpoint_json'])
    assert 'feedback_context' not in profiles.read_job_snapshot(job)
    task.pop('_analysis_feedback_context')
    workflow._analyze_with_provider(task_id, task, paths, 'codex', prompt)
    assert captured == [[], None]
    assert json.dumps(job['checkpoint_json']) == old_checkpoint


@pytest.mark.parametrize('value', [None, [], {'source':'clip_feedback','query_version':'recent-final-decisions-v1','items':[None]}])
def test_invalid_feedback_snapshot_does_not_fall_back_to_live_feedback(task_id, value):
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AI_ANALYSIS)
    record = job['payload_json'][profiles.JOB_SNAPSHOT_KEY]
    record['snapshot']['feedback_context'] = value
    record['sha256'] = profiles._snapshot_hash(record['snapshot'])
    with pytest.raises(ValueError, match='反馈快照损坏'):
        profiles.read_job_snapshot(job)


@pytest.mark.parametrize('legacy_snapshot', [False, True])
def test_existing_followup_is_not_resnapshotted_after_feedback_or_schema_changes(task_id, monkeypatch, legacy_snapshot):
    from app.services import clip_feedback_service
    parent = job_service.create_job(task_id, job_service.JOB_TYPE_SUBTITLE)
    payload = {'retry': False, 'start_step': TaskStatus.METADATA_GENERATING.value}
    child = job_service.create_job(task_id, job_service.JOB_TYPE_AUTO_PIPELINE, payload)
    if legacy_snapshot:
        record = child['payload_json'][profiles.JOB_SNAPSHOT_KEY]
        record['snapshot'].pop('feedback_context')
        record['snapshot'].pop('provider_identity')
        record['sha256'] = profiles._snapshot_hash(record['snapshot'])
        with get_connection() as c:
            c.execute('UPDATE workflow_jobs SET payload_json=? WHERE id=?', (json.dumps(child['payload_json']), child['id']))
            c.commit()
    before = child['payload_json']
    insert_feedback(task_id, '后续任务排队后新增反馈')
    monkeypatch.setattr(clip_feedback_service, 'list_recent_feedback_context_with_connection',
                        lambda *_: pytest.fail('复用后续 Job 不得重新冻结策略'))
    claimed = job_service.claim_job(parent['id'], 'followup-owner')
    with job_service.job_lease_context(parent['id'], 'followup-owner', claimed['lease_token']):
        completed, following, created = job_service.mark_job_completed_with_followup(
            parent['id'], {}, followup_task_id=task_id, followup_job_type=job_service.JOB_TYPE_AUTO_PIPELINE,
            followup_payload=payload)
    assert not created and following['id'] == child['id']
    assert completed['status'] == job_service.JOB_STATUS_COMPLETED
    assert following['payload_json'] == before
