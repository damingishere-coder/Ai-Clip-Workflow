from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import database as db
from app.main import app
from app.models.material_batch import MaterialBatchCreate
from app.services import material_batch_service as batches, material_import_service as imports
from app.services import material_catalog_service as catalog, storage_service, job_service, job_worker
from app.services import media_preflight_service, content_profile_service as profiles, task_lifecycle_service as lifecycle
from tests.test_human_review import human_db as _human_db_fixture
from tests.test_material_catalog import source_folder, registration

human_db = _human_db_fixture


@pytest.fixture
def batch_db(human_db, tmp_path, monkeypatch):
    settings = replace(storage_service.settings, database_path=human_db, storage_root=tmp_path/'managed', tasks_dir=tmp_path/'managed')
    monkeypatch.setattr(storage_service, 'settings', settings)
    monkeypatch.setattr(media_preflight_service, 'settings', settings)
    return human_db


def batch_input(tmp_path, count=10, **overrides):
    folder = source_folder(tmp_path, count)
    ids = catalog.register_materials(registration(catalog.scan_directory(str(folder))))['material_ids']
    data = dict(material_ids=ids, settings={'selection_profile':'variety_comedy'}, request_key=uuid4(), confirmed=True)
    data.update(overrides)
    return MaterialBatchCreate(**data), folder


def stub_preflight(monkeypatch):
    monkeypatch.setattr(imports, 'preflight_media', lambda *a, **k:SimpleNamespace(to_dict=lambda:{'verified':'test-double'}))


def test_ten_batch_tasks_atomic_concurrent_and_explicit_reproduction(batch_db, tmp_path):
    payload, folder = batch_input(tmp_path)
    with pytest.raises(catalog.MaterialError, match='确认'):
        batches.create_batch(payload.model_copy(update={'confirmed':False}))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: batches.create_batch(payload), range(6)))
    assert len({r['id'] for r in results}) == 1
    assert sum(not r['reused'] for r in results) == 1
    with db.get_connection() as c:
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == 10
        assert c.execute("SELECT count(*) FROM workflow_jobs WHERE job_type='material_import' AND status='queued'").fetchone()[0] == 10
        assert not c.execute("SELECT 1 FROM tasks WHERE original_video_path IS NOT NULL OR auto_mode!=0").fetchone()
        assert c.execute('SELECT count(*) FROM publish_jobs').fetchone()[0] == 0
    assert not storage_service.settings.tasks_dir.exists()  # DB transaction creates no directories.
    with pytest.raises(catalog.MaterialError, match='另一份批次'):
        batches.create_batch(payload.model_copy(update={'material_ids':payload.material_ids[:1]}))
    with pytest.raises(catalog.MaterialError, match='已有生产任务'):
        batches.create_batch(payload.model_copy(update={'request_key':uuid4()}))
    repeat = batches.create_batch(payload.model_copy(update={'request_key':uuid4(), 'create_new_production':True}))
    assert len(repeat['items']) == 10 and repeat['id'] != results[0]['id']
    assert len(list(folder.glob('*.mp4'))) == 10


def test_failure_after_task_insert_rolls_back_batch_task_and_job(batch_db, tmp_path, monkeypatch):
    payload, _ = batch_input(tmp_path, 2)
    original = job_service.create_or_get_active_job_with_connection
    calls = []
    def fail(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 2:
            raise RuntimeError('crash-after-job')
        return result
    monkeypatch.setattr(job_service, 'create_or_get_active_job_with_connection', fail)
    with pytest.raises(RuntimeError, match='crash-after-job'):
        batches.create_batch(payload)
    with db.get_connection() as c:
        for table in ('material_batches','material_batch_items','tasks','task_generation_rules','workflow_jobs'):
            assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0


def test_copy_hash_commit_recovery_and_frozen_policy(batch_db, tmp_path, monkeypatch):
    payload, folder = batch_input(tmp_path, 1)
    batch = batches.create_batch(payload)
    item = batch['items'][0]
    stub_preflight(monkeypatch)
    original_bytes = (folder/'EP001.mp4').read_bytes()
    with monkeypatch.context() as scope:
        scope.setattr(job_service, 'mark_job_completed', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('lost-after-commit')))
        with pytest.raises(RuntimeError, match='lost-after-commit'):
            job_worker.execute_job(item['job_id'])
    with db.get_connection() as c:
        proof = dict(c.execute('SELECT * FROM material_imports').fetchone())
        assert proof['source_sha256'] == hashlib.sha256(original_bytes).hexdigest()
        assert json.loads(proof['evidence_json'])['source_sha256'] == proof['source_sha256']
    stored = Path(proof['stored_path'])
    before = (stored.stat().st_mtime_ns, stored.read_bytes())
    job_service.retry_job(item['job_id'])
    result = job_worker.execute_job(item['job_id'])
    assert result['status'] == 'completed' and result['result_json']['recovered']
    assert (stored.stat().st_mtime_ns, stored.read_bytes()) == before
    assert (folder/'EP001.mp4').read_bytes() == original_bytes
    ai = job_service.create_job(item['task_id'], 'ai_analysis')
    with db.get_connection() as c:
        assert ai['payload_json'][profiles.JOB_SNAPSHOT_KEY] == batches.task_item(c, item['task_id'])['generation']
    assert profiles.read_job_snapshot(ai)
    with pytest.raises(ValueError, match='批次 Job'):
        profiles.read_job_snapshot({**ai, 'payload_json':{}})
    with pytest.raises(ValueError, match='批次冻结'):
        job_service.create_job(item['task_id'], 'ai_analysis', {'provider':'local'})
    with pytest.raises(ValueError, match='审核门槛'):
        job_service.create_job(item['task_id'], 'auto_pipeline')
    with pytest.raises(ValueError, match='已冻结'):
        lifecycle.update_task_candidate_clip_count(item['task_id'], 5)


@pytest.mark.parametrize('mode', ['changed','handle','copy','space','corrupt'])
def test_import_failure_never_activates_source(batch_db, tmp_path, monkeypatch, mode):
    payload, folder = batch_input(tmp_path, 1)
    item = batches.create_batch(payload)['items'][0]
    stub_preflight(monkeypatch)
    original = folder/'EP001.mp4'
    if mode == 'changed':
        original.write_bytes(b'changed-after-enqueue')
    elif mode == 'handle':
        monkeypatch.setattr(imports, '_handle_stamp', lambda h:{'inode':'different-open-file'})
    elif mode == 'copy':
        def fail(handle, expected_size, destination=None):
            if destination:
                destination.write(b'partial')
            raise OSError('copy-interrupted')
        monkeypatch.setattr(imports, '_read_hash', fail)
    elif mode == 'space':
        monkeypatch.setattr(imports.shutil, 'disk_usage', lambda p:SimpleNamespace(free=0))
    else:
        monkeypatch.setattr(imports, '_hash_file', lambda *a:'bad-destination-hash')
    with pytest.raises((ValueError, OSError)):
        job_worker.execute_job(item['job_id'])
    with db.get_connection() as c:
        assert not c.execute('SELECT 1 FROM material_imports').fetchone()
        assert c.execute('SELECT original_video_path FROM tasks WHERE id=?',(item['task_id'],)).fetchone()[0] is None
    assert job_service.get_job(item['job_id'])['status'] == 'failed'
    assert original.exists()
    assert not list(storage_service.settings.tasks_dir.rglob('*.part.mp4'))


def test_lost_lease_cannot_promote_and_following_task_can_run(batch_db, tmp_path, monkeypatch):
    payload, _ = batch_input(tmp_path, 2)
    items = batches.create_batch(payload)['items']
    first = items[0]
    def lose_lease(*a, **k):
        with db.get_connection() as c:
            c.execute("UPDATE workflow_jobs SET lease_token='new-generation' WHERE id=?", (first['job_id'],))
            c.commit()
        return SimpleNamespace(to_dict=lambda:{})
    with monkeypatch.context() as scope:
        scope.setattr(imports, 'preflight_media', lose_lease)
        with pytest.raises(job_service.JobLeaseLostError):
            job_worker.execute_job(first['job_id'])
    with db.get_connection() as c:
        assert not c.execute('SELECT 1 FROM material_imports').fetchone()
        # The replacement owner is still live: the serial queue must wait.
    assert job_service.claim_job(items[1]['job_id'], 'other-runner') is None
    with db.get_connection() as c:
        c.execute("UPDATE workflow_jobs SET status='failed',lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL WHERE id=?", (first['job_id'],))
        c.commit()  # Simulate the replacement execution finishing with failure.
    stub_preflight(monkeypatch)
    assert job_worker.execute_job(items[1]['job_id'])['status'] == 'completed'


def test_real_video_copy_ten_jobs_and_api_restart_receipt(batch_db, tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg / FFprobe unavailable')
    folder = tmp_path/'real-originals'
    folder.mkdir()
    first = folder/'EP001.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x90:r=10',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=16000','-t','1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(first)],
        check=True, capture_output=True, timeout=30)
    for i in range(2,11):
        shutil.copy2(first, folder/f'EP{i:03d}.mp4')
    original = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    ids = catalog.register_materials(registration(catalog.scan_directory(str(folder))))['material_ids']
    request = dict(material_ids=ids,settings={'selection_profile':'general'},request_key=str(uuid4()),confirmed=True)
    with TestClient(app) as client:
        denied = client.post('/api/material-batches',json=request,headers={'Origin':'https://evil.example'})
        assert denied.status_code == 403
        result = client.post('/api/material-batches',json=request)
        assert result.status_code == 200, result.text
        batch = result.json()
    db.init_db()  # Restart initialization with original request persisted by the caller.
    with TestClient(app) as client:
        assert client.post('/api/material-batches',json=request).json()['id'] == batch['id']
    for item in batch['items']:
        assert job_worker.execute_job(item['job_id'])['status'] == 'completed'
    current = batches.get_batch(batch['id'])
    assert len(current['items']) == 10
    for item in current['items']:
        assert item['source_sha256'] == original[item['file_name']]
        assert hashlib.sha256(Path(item['stored_path']).read_bytes()).hexdigest() == item['source_sha256']
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()} == original
    with db.get_connection() as c:
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == 10
        assert c.execute('SELECT count(*) FROM publish_jobs').fetchone()[0] == 0
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_pending_import_blocks_processing_without_changing_task(batch_db, tmp_path):
    from app.services import transcript_workflow_service as transcript, video_cut_workflow_service as cut
    payload, _ = batch_input(tmp_path, 1)
    item = batches.create_batch(payload)['items'][0]
    for action in (transcript.process_task_audio, transcript.process_task_transcript,
                   transcript.process_task_transcript_workflow, cut.process_task_video_cuts):
        with pytest.raises(ValueError, match='尚未完成复制'):
            action(item['task_id'])
    for kind in ('transcript','video_cut','ai_analysis','auto_pipeline'):
        with pytest.raises(ValueError, match='尚未完成复制'):
            job_service.create_job(item['task_id'], kind)
    response = TestClient(app).post(f"/api/tasks/{item['task_id']}/process/cuts-async")
    assert response.status_code == 409 and '尚未完成复制' in response.json()['detail']
    with db.get_connection() as c:
        assert c.execute('SELECT status FROM tasks WHERE id=?',(item['task_id'],)).fetchone()[0] == 'pending_video'
        assert c.execute('SELECT count(*) FROM workflow_jobs').fetchone()[0] == 1


def test_reclaimed_import_cleans_only_owned_orphans(batch_db, tmp_path, monkeypatch):
    payload, _ = batch_input(tmp_path, 1)
    item = batches.create_batch(payload)['items'][0]
    job = job_service.claim_job(item['job_id'], 'old-process')
    root = storage_service.create_task_directory(item['task_id'], f"batch-{item['task_id']}")/'source'
    partial = root/f"import-{job['lease_token']}.part.mp4"
    orphan = root/f"import-{job['lease_token']}.mp4"
    partial.write_bytes(b'interrupted')
    orphan.write_bytes(b'promoted-before-transaction-commit')
    unrelated = root/'manual-keep.mp4'
    unrelated.write_bytes(b'user-file')
    assert job_service.release_job_lease(job['id'], 'old-process', job['lease_token'])
    stub_preflight(monkeypatch)
    # A killed copy may have filled the disk. Reclaim its unreferenced bytes
    # before checking free space, otherwise the same job can never recover.
    monkeypatch.setattr(imports.shutil, 'disk_usage', lambda p:SimpleNamespace(free=0 if partial.exists() else 10**12))
    result = job_worker.execute_job(item['job_id'])
    assert result['status'] == 'completed'
    assert not partial.exists() and not orphan.exists() and unrelated.read_bytes() == b'user-file'


def test_reparse_point_replacement_is_rejected(batch_db, tmp_path, monkeypatch):
    payload, folder = batch_input(tmp_path, 1)
    item = batches.create_batch(payload)['items'][0]
    original_lstat = Path.lstat
    def reparse(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == folder:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, 'lstat', reparse)
    with pytest.raises(ValueError, match='重解析点'):
        job_worker.execute_job(item['job_id'])
    with db.get_connection() as c:
        assert not c.execute('SELECT 1 FROM material_imports').fetchone()
