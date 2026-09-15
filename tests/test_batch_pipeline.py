import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.db import database as db
from app.services import material_batch_service as batches, job_service, job_worker
from app.services import batch_pipeline_service as batch_pipeline, production_review_service as review
from app.services import video_cut_workflow_service as cut_workflow
from app.services.pipeline_engine import PipelineEngine
from app.services.storage_service import get_artifact_paths
from app.services.video_cut_service import CutResult
from tests.test_material_batch import batch_db as _batch_db, human_db as _human_db, batch_input, stub_preflight
from tests.test_cut_atomicity import _insert_candidate

batch_db, human_db = _batch_db, _human_db


@pytest.fixture
def stage_doubles(monkeypatch):
    """Only external transcription/AI/FFmpeg are simulated; queue/checkpoints/commits are real."""
    calls = {'transcript':[], 'ai':[], 'cut':[]}
    def transcript(self, task, context):
        calls['transcript'].append(task)
        path = get_artifact_paths(task)['transcript_path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('00:00:01 - 00:00:03 测试材料\n', encoding='utf-8')
        return {'source':'test-double','transcript_path':str(path)}
    def ai(self, task, context):
        calls['ai'].append(task)
        run = uuid4().hex
        meta = dict(schema_version=2, selection_profile='general',analysis_incomplete=False,quality_degraded=False,
            coverage_ratio=1.0,coverage_percent=100.0,invalid_item_count=0,expected_units=1,completed_units=1,
            failed_units=0,failed_stages=[])
        clips = []
        for _ in range(3):
            key = uuid4().hex
            _insert_candidate(task,key)
            clips.append({'clip_id':key,'title':key,'start_time':'00:00:01','end_time':'00:00:03'})
        payload = {'task_id':task,'clips':clips,'analysis_meta':meta}
        raw = json.dumps(payload,ensure_ascii=False,indent=2)
        with db.get_connection() as c:
            c.execute("""INSERT INTO ai_analysis_runs(id,task_id,run_number,provider,provider_label,model,
                requested_clip_count,clip_count,analysis_payload_json,is_active,created_at)
                VALUES(?,?,1,'test','test','test',3,3,?,1,'test')""",(run,task,raw))
            c.execute('UPDATE clip_candidates SET source_analysis_run_id=? WHERE task_id=?',(run,task))
            c.commit()
        path = get_artifact_paths(task)['analysis_path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw,encoding='utf-8')
        return {'analysis_run_id':run,'clip_count':3,'analysis_meta':meta}
    def cut(*,source_video, clips, output_dir, strategy):
        calls['cut'].append(str(source_video))
        output_dir.mkdir(parents=True,exist_ok=True)
        rows=[]
        for clip in clips:
            file = output_dir/f"{clip['id']}.mp4"
            file.write_bytes(b'isolated-cut-result')
            rows.append(CutResult(clip['id'],str(file),file.name,'completed',source_start_ms=1000,source_end_ms=3000))
        return rows
    monkeypatch.setattr(PipelineEngine,'_transcribe_or_read_text',transcript)
    monkeypatch.setattr(PipelineEngine,'_run_ai_analysis',ai)
    monkeypatch.setattr(cut_workflow,'cut_clips',cut)
    for handler in ('_prepare_subtitle_drafts','_generate_metadata','_create_schedule','_create_publish_jobs'):
        monkeypatch.setattr(PipelineEngine,handler,lambda *a:pytest.fail('批次不得自动越过成片审核'))
    return calls


def automatic_batch(tmp_path,count=1):
    payload,folder = batch_input(tmp_path,count,settings={'selection_profile':'general','auto_production':True,
        'candidate_clip_count':3,'final_clip_target':2,'subtitle_strategy':'review'})
    return batches.create_batch(payload),payload,folder


def import_jobs(batch,monkeypatch):
    stub_preflight(monkeypatch)
    result=[]
    for item in batch['items']:
        imported = job_worker.execute_job(item['job_id'])
        result.append(imported['result_json']['followup_job_id'])
    return result


def test_ten_jobs_pre_cut_pause_and_one_failure_does_not_block(batch_db,tmp_path,monkeypatch,stage_doubles):
    batch,payload,folder = automatic_batch(tmp_path,10)
    jobs = import_jobs(batch,monkeypatch)
    assert batches.create_batch(payload)['id'] == batch['id']
    failed_task = batch['items'][0]['task_id']
    original = PipelineEngine._transcribe_or_read_text
    def fail_one(self,task,context):
        if task == failed_task:
            raise TimeoutError('transcription-timeout-test')
        return original(self,task,context)
    monkeypatch.setattr(PipelineEngine,'_transcribe_or_read_text',fail_one)
    with pytest.raises(RuntimeError, match='transcription-timeout'):
        job_worker.execute_job(jobs[0])
    for job in jobs[1:]:
        result = job_worker.execute_job(job)
        assert result['status'] == 'completed'
        assert result['result_json']['status'] == 'pending_review'
        assert result['checkpoint_json']['completed_steps'] == list(batch_pipeline.PRE_CUT_STEPS)
    with db.get_connection() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE status='pending_review'").fetchone()[0] == 9
        assert c.execute('SELECT count(*) FROM output_clip WHERE is_active=1').fetchone()[0] == 18
        for table in ('production_reviews','publish_jobs','subtitle_jobs'):
            assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    for item in batch['items'][1:]:
        assert review.state(item['task_id'])['can_confirm']
        assert not review.state(item['task_id'])['approved']
    assert len(stage_doubles['ai']) == 9 and len(stage_doubles['cut']) == 9
    assert len(list(folder.glob('*.mp4'))) == 10


def test_import_followup_failure_rolls_back_then_recovers_without_recopy(batch_db,tmp_path,monkeypatch):
    batch,_,_ = automatic_batch(tmp_path)
    stub_preflight(monkeypatch)
    item=batch['items'][0]
    create = job_service.create_or_get_active_job_with_connection
    def fail_after_insert(*args, **kwargs):
        result = create(*args, **kwargs)
        if kwargs.get('job_type') == 'auto_pipeline':
            raise RuntimeError('before-followup-commit')
        return result
    with monkeypatch.context() as scope:
        scope.setattr(job_service,'create_or_get_active_job_with_connection',fail_after_insert)
        with pytest.raises(RuntimeError,match='before-followup'):
            job_worker.execute_job(item['job_id'])
    with db.get_connection() as c:
        stored = Path(c.execute('SELECT stored_path FROM material_imports').fetchone()[0])
        assert not c.execute("SELECT 1 FROM workflow_jobs WHERE job_type='auto_pipeline'").fetchone()
    stamp = stored.stat().st_mtime_ns
    job_service.retry_job(item['job_id'])
    result = job_worker.execute_job(item['job_id'])
    assert result['result_json']['recovered']
    assert stored.stat().st_mtime_ns == stamp
    again = job_worker.execute_job(item['job_id'])
    assert again['result_json']['followup_job_id'] == result['result_json']['followup_job_id']
    with db.get_connection() as c:
        assert c.execute("SELECT count(*) FROM workflow_jobs WHERE job_type='auto_pipeline'").fetchone()[0] == 1


def test_partial_cut_stays_failed_and_cannot_be_confirmed(batch_db,tmp_path,monkeypatch,stage_doubles):
    batch,_,_ = automatic_batch(tmp_path)
    job=import_jobs(batch,monkeypatch)[0]
    original = cut_workflow.cut_clips
    def partial(**kwargs):
        results = original(**kwargs)
        return [results[0], CutResult(results[1].clip_candidate_id, '', '', 'failed', 'encoder-test-error')]
    monkeypatch.setattr(cut_workflow,'cut_clips',partial)
    with pytest.raises(RuntimeError,match='完整成片'):
        job_worker.execute_job(job)
    task=batch['items'][0]['task_id']
    assert not review.state(task)['can_confirm']
    assert job_service.get_job(job)['status'] == 'failed'
    with db.get_connection() as c:
        assert not c.execute('SELECT 1 FROM publish_jobs').fetchone()


def test_restart_after_cut_checkpoint_does_not_repeat_ai_or_cut(batch_db,tmp_path,monkeypatch,stage_doubles):
    batch,_,_ = automatic_batch(tmp_path)
    job=import_jobs(batch,monkeypatch)[0]
    with monkeypatch.context() as scope:
        scope.setattr(batch_pipeline,'pause_for_review',lambda *a:(_ for _ in ()).throw(SystemExit('simulated-worker-exit')))
        with pytest.raises(SystemExit):
            job_worker.execute_job(job)
    with db.get_connection() as c:
        c.execute("UPDATE workflow_jobs SET status='failed',lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL WHERE id=?",(job,))
        c.commit()  # Reaper persisted a failed worker; retained checkpoints are authoritative.
    before = {k:list(v) for k,v in stage_doubles.items()}
    job_service.retry_job(job)
    result = job_worker.execute_job(job)
    assert result['status'] == 'completed' and result['result_json']['status'] == 'pending_review'
    assert stage_doubles == before


@pytest.mark.parametrize('step',['SUBTITLE_DRAFTING','METADATA_GENERATING','SCHEDULE_CREATING','PUBLISH_JOB_CREATING'])
def test_forbidden_batch_resume_cannot_enqueue_or_execute(batch_db,tmp_path,monkeypatch,step):
    batch,_,_ = automatic_batch(tmp_path)
    job=import_jobs(batch,monkeypatch)[0]
    task=batch['items'][0]['task_id']
    with pytest.raises(ValueError,match='只到预切'):
        job_service.create_job(task,'auto_pipeline',{'start_step':step})
    with pytest.raises(ValueError,match='只到预切'):
        PipelineEngine().run(task,start_step=step,job_id=job)


@pytest.mark.parametrize('strategy,mode', [('original','original'),('review','subtitled')])
def test_batch_subtitle_choice_reaches_review_without_bypassing_human_gate(
        strategy,mode,batch_db,tmp_path,monkeypatch,stage_doubles):
    from app.models.production_review import ProductionReviewConfirm
    payload,_ = batch_input(tmp_path,1,settings={'selection_profile':'general','auto_production':True,
        'candidate_clip_count':3,'final_clip_target':2,'subtitle_strategy':strategy})
    batch = batches.create_batch(payload)
    task = batch['items'][0]['task_id']
    job = import_jobs(batch,monkeypatch)[0]
    assert job_worker.execute_job(job)['result_json']['status'] == 'pending_review'
    state = review.state(task)
    assert state['suggested_delivery_mode'] == mode
    assert not state['approved'] and not state['ready']
    assert len(stage_doubles['transcript']) == 1  # Skipping subtitles still permits AI transcription.
    with pytest.raises(ValueError,match='人工确认'):
        review.check_preparation(task)
    review.confirm(task,ProductionReviewConfirm(request_key=uuid4(),manifest_sha256=state['manifest_sha256'],
        delivery_mode=mode,confirmed=True))
    assert review.state(task)['ready'] == (strategy == 'original')
    with db.get_connection() as c:
        assert c.execute('SELECT count(*) FROM subtitle_jobs').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM publish_jobs').fetchone()[0] == 0
