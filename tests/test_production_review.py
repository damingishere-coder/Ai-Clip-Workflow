from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import database as db
from app.main import app
from app.models.production_review import ProductionReviewConfirm
from app.services import production_review_service as review, cut_evidence_service as evidence
from app.services import video_cut_workflow_service as cuts, job_service, job_worker
from app.services.video_cut_service import CutResult
from tests.test_material_batch import batch_db as _batch_db, human_db as _human_db, batch_input, stub_preflight
from tests.test_cut_atomicity import _insert_candidate


batch_db, human_db = _batch_db, _human_db


@pytest.fixture
def output_batch(batch_db, tmp_path, monkeypatch):
    from app.services import material_batch_service as batches
    payload, _ = batch_input(tmp_path, 1)
    item = batches.create_batch(payload)['items'][0]
    stub_preflight(monkeypatch)
    job_worker.execute_job(item['job_id'])
    task = item['task_id']
    candidate = uuid4().hex
    _insert_candidate(task, candidate)
    run = cuts._create_cut_run(task)
    with db.get_connection() as c:
        selection = evidence.selection_snapshot(c, task)
    path = tmp_path/'actual-output.mp4'
    path.write_bytes(b'fake-media-for-isolated-boundary-tests')
    inputs = {'selection':selection, 'source':evidence.source_stamp(selection['source_path'])}
    result = CutResult(candidate, str(path), path.name, 'completed', source_start_ms=1000, source_end_ms=3000)
    cuts._commit_cut_run_results(task, run['id'], [result], source_fingerprint='test', cut_inputs=inputs)
    with db.get_connection() as c:
        output = dict(c.execute('SELECT * FROM output_clip WHERE task_id=? AND is_active=1',(task,)).fetchone())
    return task, candidate, output


def consent(task, mode='original'):
    return ProductionReviewConfirm(request_key=uuid4(), manifest_sha256=review.state(task)['manifest_sha256'],
                                   delivery_mode=mode, confirmed=True)


def test_completed_batch_cut_points_to_actual_review_before_publish(output_batch):
    from app.services.task_service import get_task_live_status
    task, _, _ = output_batch
    with db.get_connection() as c:
        c.execute("UPDATE tasks SET auto_mode=1,status='completed',progress=100 WHERE id=?", (task,))
        c.commit()
    live = get_task_live_status(task)
    assert live['actions']['primary'] == 'production_review'
    assert live['status_label'] == '待确认实际成片'
    assert live['progress'] < 100
    review.confirm(task, consent(task))
    live = get_task_live_status(task)
    assert live['actions']['primary'] == 'production_review'
    assert live['status_label'] == '待进入内容准备'


def test_recut_batch_records_pending_review_atomically(output_batch, tmp_path):
    task, candidate, _ = output_batch
    run = cuts._create_cut_run(task)
    with db.get_connection() as c:
        selection = evidence.selection_snapshot(c, task)
        c.execute("UPDATE tasks SET status='cutting' WHERE id=?", (task,))
        c.commit()
    path = tmp_path/'recut.mp4'
    path.write_bytes(b'new-isolated-cut')
    result = CutResult(candidate, str(path), path.name, 'completed', source_start_ms=1000, source_end_ms=3000)
    outcome = cuts._commit_cut_run_results(task, run['id'], [result], source_fingerprint='test',
        cut_inputs={'selection':selection, 'source':evidence.source_stamp(selection['source_path'])})
    assert outcome['task_finalized']
    with db.get_connection() as c:
        assert c.execute('SELECT status FROM tasks WHERE id=?', (task,)).fetchone()[0] == 'pending_review'
    assert not review.state(task)['approved']


def insert_publish(task, output, status='DRAFT', *, mode='original'):
    key = uuid4().hex
    with db.get_connection() as c:
        c.execute('''INSERT INTO publish_jobs(id,task_id,output_clip_id,platform,video_source,video_file_path,
            video_path,status,created_at,updated_at) VALUES(?,?,?,'douyin',?,?,?,?, 'test','test')''',
            (key, task, output['id'], mode, output['output_file_path'], output['output_file_path'], status))
        c.commit()
    return key


def test_explicit_confirmation_idempotence_does_not_publish_or_create_feedback(output_batch):
    task,_,output = output_batch
    assert review.state(task)['can_confirm'] and not review.state(task)['approved']
    with pytest.raises(ValueError, match='人工确认'):
        review.check_preparation(task)
    payload = consent(task)
    with pytest.raises(ValueError, match='明确确认'):
        review.confirm(task, payload.model_copy(update={'confirmed':False}))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _:review.confirm(task,payload),range(4)))
    assert len({r['review_id'] for r in results}) == 1
    assert sum(not r['reused'] for r in results) == 1
    assert review.state(task)['ready']
    with db.get_connection() as c:
        assert c.execute('SELECT count(*) FROM clip_feedback').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM publish_jobs').fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            c.execute("UPDATE production_reviews SET delivery_mode='subtitled'")
    insert_publish(task,output)
    with pytest.raises(ValueError, match='另一份'):
        review.confirm(task,payload.model_copy(update={'delivery_mode':'subtitled'}))


@pytest.mark.parametrize('status',['DRAFT','SCHEDULED','NEED_REVIEW','PUBLISHED'])
def test_no_creation_status_bypasses_review(output_batch,status):
    task,_,output = output_batch
    with pytest.raises(db.ProductionReviewConflict, match='尚未人工确认'):
        insert_publish(task,output,status)


@pytest.mark.parametrize('change',['selection','boundary','file','queued','output','source'])
def test_changed_inputs_invalidate_consent(output_batch,change):
    task,candidate,output = output_batch
    review.confirm(task,consent(task))
    key = insert_publish(task,output)
    with db.get_connection() as c:
        if change == 'selection':
            c.execute('UPDATE clip_candidates SET enabled=0 WHERE id=?',(candidate,))
        elif change == 'boundary':
            c.execute("UPDATE clip_candidates SET end_time='00:00:04' WHERE id=?",(candidate,))
        elif change == 'output':
            c.execute('UPDATE output_clip SET source_end_ms=4000 WHERE id=?',(output['id'],))
        elif change == 'source':
            c.execute("UPDATE tasks SET original_video_path='changed' WHERE id=?",(task,))
        c.commit()
    if change == 'file':
        Path(output['output_file_path']).write_bytes(b'changed')
    elif change == 'queued':
        job_service.create_job(task,'video_cut')
    assert not review.state(task)['ready']
    with pytest.raises((ValueError,OSError)):
        review.check_preparation(task)
    with db.get_connection() as c:
        job = dict(c.execute('SELECT * FROM publish_jobs WHERE id=?',(key,)).fetchone())
    assert review.readiness_issue(job)
    if change != 'file':  # Files are checked by readiness, SQLite guards data races.
        with pytest.raises(db.ProductionReviewConflict):
            with db.get_connection() as c:
                c.execute("UPDATE publish_jobs SET status='SCHEDULED' WHERE id=?",(key,))


def test_publishing_freezes_mutations_but_can_record_external_result(output_batch):
    task,candidate,output = output_batch
    review.confirm(task,consent(task))
    key = insert_publish(task,output,'PUBLISHING')
    for sql,args in [
        ('UPDATE clip_candidates SET enabled=0 WHERE id=?',(candidate,)),
        ('UPDATE output_clip SET is_active=0 WHERE id=?',(output['id'],)),
        ("UPDATE tasks SET original_video_path='new' WHERE id=?",(task,)),
    ]:
        with pytest.raises(db.ProductionReviewConflict, match='正在发布'):
            with db.get_connection() as c:
                c.execute(sql,args)
    with pytest.raises(db.ProductionReviewConflict, match='正在发布'):
        job_service.create_job(task,'video_cut')
    with db.get_connection() as c:
        c.execute("UPDATE publish_jobs SET status='PUBLISHED',remote_video_id='external-fact' WHERE id=?",(key,))
        c.commit()
    assert review.state(task)['ready']


def test_subtitle_decision_requires_verified_current_delivery(output_batch):
    task,_,output = output_batch
    value = review.confirm(task,consent(task,'subtitled'))
    assert review.state(task)['approved'] and not review.state(task)['ready']
    with pytest.raises(ValueError):
        insert_publish(task,output)
    assert review.subtitle_review(task)['id'] == value['review_id']
    with pytest.raises(ValueError, match='已失效'):
        review.subtitle_review(task,{'production_review_id':'wrong'})
    from app.services.subtitle_auto_workflow_service import skip_task_subtitles_to_review
    with pytest.raises(ValueError):
        skip_task_subtitles_to_review(task)


def test_old_tasks_keep_compatibility_and_new_api_requires_current_preview(output_batch):
    task,_,_ = output_batch
    with db.get_connection() as c:
        c.execute("INSERT INTO tasks(id,task_name,task_dir_name,created_at,updated_at) VALUES('legacy','旧任务','legacy','old','old')")
        c.commit()
    assert review.state('legacy') == {'required':False}
    assert review.check_preparation('legacy') is None
    client = TestClient(app)
    assert client.get('/api/production-review/missing').status_code == 404
    payload = consent(task).model_dump(mode='json')
    payload['manifest_sha256'] = '0'*64
    assert client.post(f'/api/production-review/{task}/confirm',json=payload).status_code == 409
    html = client.get(f'/tasks/{task}/clips')
    assert html.status_code == 200 and 'id="production-review"' in html.text
    assert 'data-sync-reviewed-clips="true"' not in html.text


def test_early_guards_prevent_metadata_and_cover_work(output_batch):
    from app.services import publish_service, auto_publish_service
    task,_,output = output_batch
    with pytest.raises(ValueError):
        publish_service.sync_task_publish_jobs(task)
    with pytest.raises(ValueError):
        auto_publish_service.create_auto_publish_jobs({'id':task}, [],subtitle_delivery_mode='original')
    with pytest.raises(ValueError):
        publish_service.generate_publish_metadata({'task_id':task,'id':output['id']})


def test_batch_processing_requires_workflow_lease(output_batch):
    from app.services.material_batch_service import require_task_source
    task,_,_ = output_batch
    with pytest.raises(ValueError, match='队列'):
        require_task_source(task)


def test_subtitle_revision_and_render_are_bound_without_auto_resume(output_batch, monkeypatch, tmp_path):
    from app.services import subtitle_auto_workflow_service as subtitles
    task,_,output = output_batch
    confirmed = review.confirm(task,consent(task,'subtitled'))
    track,revision,sj = (uuid4().hex for _ in range(3))
    with db.get_connection() as c:
        c.execute("""INSERT INTO subtitle_tracks(id,task_id,track_type,output_clip_id,name,active_revision_id,created_at,updated_at)
            VALUES(?,?,'clip',?,'字幕',?,'test','test')""",(track,task,output['id'],revision))
        c.execute("""INSERT INTO subtitle_revisions(id,track_id,revision_number,origin,status,cue_count,checksum,created_at)
            VALUES(?,?,1,'manual','approved',1,'test','test')""",(revision,track))
        c.commit()
    rendered = tmp_path/'rendered.mp4'
    rendered.write_bytes(b'verified-render-test-double')
    def render(*args,**kwargs):
        with db.get_connection() as c:
            c.execute("""INSERT INTO subtitle_jobs(id,task_id,output_clip_id,revision_id,status,is_active,validation_status,
                output_file_path,created_at,updated_at) VALUES(?,?,?,?,'completed',1,'verified',?,'test','test')""",
                (sj,task,output['id'],revision,str(rendered)))
            c.commit()
        return {'job':{'id':sj,'output_file_path':str(rendered)}}
    monkeypatch.setattr(subtitles,'render_subtitles_for_output_clip',render)
    payload = {'items':[{'output_clip_id':output['id'],'revision_id':revision}], 'continue_pipeline':True,
        'production_review_id':confirmed['review_id'],'production_review_sha256':review.state(task)['manifest_sha256']}
    job = job_service.create_job(task,'subtitle',payload)
    result = job_worker.execute_job(job['id'])
    assert result['status'] == 'completed'
    assert result['result_json']['resume_requested'] is False
    assert review.state(task)['ready']
    with db.get_connection() as c:
        assert not c.execute("SELECT 1 FROM workflow_jobs WHERE job_type='auto_pipeline'").fetchone()
        assert c.execute('SELECT status FROM tasks WHERE id=?',(task,)).fetchone()[0] == 'completed'
    insert_publish(task,{**output,'output_file_path':str(rendered)},mode='subtitled')
    with db.get_connection() as c:
        c.execute("UPDATE subtitle_revisions SET status='draft' WHERE id=?",(revision,))
        c.commit()
    assert not review.state(task)['ready']
    with pytest.raises(db.ProductionReviewConflict):
        with db.get_connection() as c:
            c.execute("UPDATE publish_jobs SET status='PUBLISHING' WHERE task_id=?",(task,))


def test_prepared_jobs_require_cancellation_before_new_delivery_consent(output_batch):
    task,_,output = output_batch
    original = consent(task)
    review.confirm(task,original)
    key = insert_publish(task,output)
    assert review.state(task)['ready'] and not review.state(task)['can_confirm']
    assert review.confirm(task,original)['reused']  # Lost response still recovers the same receipt.
    replacement = consent(task,'subtitled')
    with pytest.raises(ValueError, match='取消或处理'):
        review.confirm(task,replacement)
    with db.get_connection() as c:
        c.execute("UPDATE publish_jobs SET status='CANCELLED' WHERE id=?",(key,))
        c.commit()
    review.confirm(task,replacement)
    assert review.state(task)['delivery_mode'] == 'subtitled'


def test_invalid_scheduled_batch_moves_to_review_and_does_not_dispatch(output_batch, monkeypatch):
    from app.services.publish_scheduler import PublishScheduler
    task,candidate,output = output_batch
    review.confirm(task,consent(task))
    key = insert_publish(task,output,'SCHEDULED')
    with db.get_connection() as c:
        c.execute("UPDATE publish_jobs SET publish_mode='manual_export' WHERE id=?",(key,))
        c.execute('UPDATE clip_candidates SET enabled=0 WHERE id=?',(candidate,))
        c.commit()
    scheduler = PublishScheduler()
    result = scheduler.execute_job(key)
    assert result['status'] == 'need_review'
    with db.get_connection() as c:
        row = c.execute('SELECT status,error_code FROM publish_jobs WHERE id=?',(key,)).fetchone()
        assert tuple(row) == ('NEED_REVIEW','production_review_required')
    assert scheduler.execute_job(key)['status'] == 'skipped'
