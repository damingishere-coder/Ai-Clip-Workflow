from datetime import datetime, timedelta, timezone
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import database as db
from app.main import app
from app.services import production_workbench_service as work, production_review_service as review
from app.services import job_service, job_worker, material_catalog_service as catalog, material_batch_service as batches
from tests.test_production_review import output_batch as _output_batch, batch_db as _batch_db, human_db as _human_db, consent, insert_publish
from tests.test_cut_atomicity import _insert_candidate
from tests.test_material_catalog import source_folder, registration
from tests.test_material_batch import batch_input, stub_preflight

output_batch, batch_db, human_db = _output_batch, _batch_db, _human_db


def legacy(task, status='pending_review', count=2):
    with db.get_connection() as c:
        c.execute("INSERT INTO tasks(id,task_name,task_dir_name,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                  (task,task,task,status,'2026-09-15T01:00:00+00:00','2026-09-15T01:00:00+00:00'))
        c.commit()
    for i in range(count):
        _insert_candidate(task,f'{task}-{i}')
    with db.get_connection() as c:
        c.execute('UPDATE clip_candidates SET reviewed=1 WHERE task_id=?',(task,))
        c.commit()


def test_batch_review_subtitle_prepare_and_invalid_epoch_projection(output_batch):
    task,candidate,output = output_batch
    assert work.inbox()['counts']['clips']['count'] == 1
    review.confirm(task,consent(task,'subtitled'))
    assert work.inbox()['counts']['subtitles']['count'] == 1
    assert work.inbox()['counts']['prepare']['count'] == 0
    review.confirm(task,consent(task))
    assert work.inbox()['counts']['prepare']['count'] == 1
    assert work.inbox()['counts']['clips']['count'] == 0
    insert_publish(task,output)
    assert work.inbox()['counts']['prepare']['count'] == 0
    with db.get_connection() as c:
        c.execute("UPDATE clip_candidates SET end_time='00:00:04' WHERE id=?",(candidate,))
        c.commit()
    assert work.inbox()['counts']['clips']['count'] == 1


def test_busy_task_hidden_and_failed_job_not_double_counted(output_batch):
    task,_,_ = output_batch
    job = job_service.create_job(task,'video_cut')
    assert work.inbox()['total'] == 0
    with db.get_connection() as c:
        c.execute("UPDATE workflow_jobs SET status='failed',error_message='isolated failure' WHERE id=?",(job['id'],))
        c.execute("UPDATE tasks SET status='FAILED_VIDEO_CUTTING' WHERE id=?",(task,))
        c.commit()
    result = work.inbox()
    assert result['total'] == 1 and result['counts']['errors']['count'] == 1
    assert result['counts']['clips']['count'] == 0


def test_legacy_auto_review_flag_pagination_escaping_and_readonly(human_db, monkeypatch):
    for i in range(23):
        legacy(f'legacy-{i}')
    legacy('completed-legacy','completed')
    legacy('deleted')
    with db.get_connection() as c:
        c.execute("UPDATE tasks SET is_deleted=1 WHERE id='deleted'")
        c.execute("UPDATE tasks SET task_name='<script>alert(1)</script>' WHERE id='legacy-0'")
        c.commit()
    with sqlite3.connect(human_db) as c:
        before = '\n'.join(c.iterdump())
    # No media inspection or detailed task loading on this aggregate page.
    from app.services import task_service
    monkeypatch.setattr(task_service,'get_task',lambda *a,**k:pytest.fail('N+1 detailed task load'))
    result = work.inbox('clips',2)
    assert result['total'] == 23 and len(result['items']) == 3
    assert result['counts']['clips'] == dict(label='待审片',unit='片段',count=46,tasks=23)
    client = TestClient(app)
    for page in (1,2):
        response = client.get('/review-inbox',params={'category':'clips','page':page})
        assert response.status_code == 200 and '<script>alert(1)</script>' not in response.text
    assert client.get('/review-inbox?category=unknown').status_code == 400
    assert client.get('/review-inbox?page=0').status_code == 422
    with sqlite3.connect(human_db) as c:
        assert '\n'.join(c.iterdump()) == before


def test_publish_review_and_shanghai_today_count(output_batch):
    task,_,output = output_batch
    review.confirm(task,consent(task))
    first = insert_publish(task,output,'NEED_REVIEW')
    assert work.inbox('publish')['items'][0]['id'] == 'publish:'+first
    with db.get_connection() as c:
        c.execute("UPDATE publish_jobs SET status='FAILED' WHERE id=?",(first,))
        c.commit()
    assert work.inbox('publish')['counts']['publish']['count'] == 1
    assert '重试' in work.inbox('publish')['items'][0]['message']
    second = first
    with db.get_connection() as c:
        c.execute("UPDATE publish_jobs SET status='SCHEDULED',scheduled_at=? WHERE id=?",('2026-09-14T16:30:00+00:00',second))
        c.commit()
    assert work.inbox('publish')['total'] == 0
    cards = {x['label']:x for x in work.dashboard(now=datetime(2026,9,15,0,0,tzinfo=timezone.utc))['cards']}
    assert cards['今日排期']['value'] == 1
    assert cards['可复盘作品']['value'] == 0
    assert cards['可复盘作品']['note'] == '暂无已确认官方作品数据'


def test_manual_import_waiting_for_explicit_processing_is_visible(batch_db,tmp_path,monkeypatch):
    payload,_ = batch_input(tmp_path,1)
    item = batches.create_batch(payload)['items'][0]
    stub_preflight(monkeypatch)
    job_worker.execute_job(item['job_id'])
    assert work.inbox('start')['items'][0]['task_id'] == item['task_id']
    assert work.inbox('start')['counts']['start']['count'] == 1
    cards = {x['label']:x for x in work.dashboard()['cards']}
    assert '1 个待继续处理' in cards['处理中']['note']
    with db.get_connection() as c:
        c.execute("UPDATE workflow_jobs SET status='failed' WHERE id=?",(item['job_id'],))
        c.commit()
    assert work.inbox('errors')['items'][0]['url'] == '/materials'


def test_reviewable_uses_account_scope_and_existing_evidence_threshold(human_db,monkeypatch):
    from app.services import content_review_service as metrics
    with db.get_connection() as c:
        c.execute("INSERT INTO publish_accounts(id,platform,account_name,created_at,updated_at) VALUES('one','douyin','one','test','test')")
        c.execute("INSERT INTO publish_accounts(id,platform,account_name,created_at,updated_at) VALUES('two','douyin','two','test','test')")
        c.commit()
    calls = []
    def rows(c,account):
        calls.append(account)
        valid = dict(publish_job_id=account,match_status=next(iter(metrics.MATCHED_STATUSES)),**{k:1 for k in metrics.DIAGNOSIS_CORE_METRICS})
        return [valid,{**valid,'watch_ratio':None},{**valid,'match_status':'ambiguous'}]
    monkeypatch.setattr(metrics,'_latest_diagnosis_rows',rows)
    cards = {x['label']:x for x in work.dashboard()['cards']}
    assert cards['可复盘作品']['value'] == 2 and sorted(calls) == ['one','two']


def test_expired_preview_cleanup_protects_confirmed_and_is_bounded(human_db,tmp_path):
    folder = source_folder(tmp_path,1)
    confirmed = catalog.scan_directory(str(folder))
    catalog.register_materials(registration(confirmed))
    others = [catalog.scan_directory(str(folder)) for _ in range(3)]
    now = datetime.now(timezone.utc)+timedelta(days=8)
    assert catalog.cleanup_expired_scans(now=now,limit=2) == 2
    assert catalog.cleanup_expired_scans(now=now,limit=2) == 1
    with db.get_connection() as c:
        assert c.execute('SELECT id FROM material_scans').fetchone()[0] == confirmed['id']
        assert c.execute('SELECT count(*) FROM source_materials').fetchone()[0] == 1
    fresh = catalog.scan_directory(str(folder))
    assert catalog.cleanup_expired_scans() == 0 and fresh['id'] not in {x['id'] for x in others}
    assert (folder/'EP001.mp4').exists()


def test_full_hash_hints_distinct_materials_not_names_or_reproductions(batch_db,tmp_path,monkeypatch):
    payload,folder = batch_input(tmp_path,2)
    # A third path with identical bytes is a distinct material, shown as a hint after verified import.
    (folder/'copy.mp4').write_bytes((folder/'EP001.mp4').read_bytes())
    ids = catalog.register_materials(registration(catalog.scan_directory(str(folder))))['material_ids']
    payload = payload.model_copy(update={'material_ids':ids})
    first = batches.create_batch(payload)
    stub_preflight(monkeypatch)
    for item in first['items']:
        job_worker.execute_job(item['job_id'])
    second = batches.create_batch(payload.model_copy(update={'request_key':uuid4(),'create_new_production':True}))
    for item in second['items']:
        job_worker.execute_job(item['job_id'])
    hints = {m['file_name']:m for m in catalog.list_materials()['materials']}
    assert hints['EP001.mp4']['duplicate_material_count'] == hints['copy.mp4']['duplicate_material_count'] == 1
    assert hints['EP002.mp4']['duplicate_material_count'] == 0
    assert len(first['items']) == len(second['items']) == 3
