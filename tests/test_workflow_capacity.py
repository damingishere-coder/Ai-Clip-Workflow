from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from app.db import database as db
from app.services import job_service, job_worker, workflow_capacity_service as capacity
from app.services.managed_process_service import terminate_process_tree
from tests.test_human_review import human_db as _human_db_fixture

human_db = _human_db_fixture


def queued_jobs(count=10):
    jobs = []
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for index in range(count):
        task_id = uuid4().hex
        with db.get_connection() as c:
            c.execute("INSERT INTO tasks(id,task_name,task_dir_name,selection_profile,created_at,updated_at) VALUES(?,?,?,'general',?,?)",
                      (task_id,f'EP{index:03d}',task_id,now,now))
            c.commit()
        jobs.append(job_service.create_job(task_id,'video_cut'))
    return jobs


def finish(job, *, failed=False):
    with job_service.job_lease_context(job['id'],job['lease_owner'],job['lease_token']):
        job_service.mark_job_failed(job['id'],'simulated-item-failure') if failed else job_service.mark_job_completed(job['id'])


def test_mixed_direct_and_next_claims_never_exceed_one_slot(human_db):
    jobs = queued_jobs()
    def claim(i):
        return job_service.claim_job(jobs[i]['id'],f'direct-{i}') if i % 2 else job_service.claim_next_job(f'runner-{i}')
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(claim, range(10)))
    winners = [r for r in results if r]
    assert len(winners) == 1
    with db.get_connection() as c:
        assert c.execute("SELECT count(*) FROM workflow_jobs WHERE status='running'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM workflow_jobs WHERE status='queued' AND attempt_count=0").fetchone()[0] == 9
    finish(winners[0], failed=True)
    assert job_service.claim_next_job('following-item') is not None


def test_fifo_same_timestamp_backoff_cancel_and_restart(human_db):
    jobs = queued_jobs(3)
    first = job_service.claim_next_job('old-web')
    assert first['id'] == jobs[0]['id']
    job_service.request_job_cancel(first['id'])
    assert job_service.claim_next_job('another-web') is None  # Live cancelled executor still owns capacity.
    with job_service.job_lease_context(first['id'], first['lease_owner'], first['lease_token']):
        job_service.mark_job_cancelled(first['id'])
    second = job_service.claim_next_job('web')
    assert second['id'] == jobs[1]['id']
    assert job_service.release_job_lease(second['id'],second['lease_owner'],second['lease_token'])
    db.init_db()
    resumed = job_service.claim_next_job('restarted-web')
    assert resumed['id'] == second['id'] and resumed['lease_token'] != second['lease_token']
    finish(resumed)
    later = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    with db.get_connection() as c:
        c.execute('UPDATE workflow_jobs SET next_attempt_at=? WHERE id=?',(later,jobs[2]['id']))
        c.commit()
    assert job_service.claim_job(jobs[2]['id'],'direct') is None
    assert job_service.claim_next_job('queued') is None


def hold_in_child(path):
    script = """from pathlib import Path
import sys
from app.services.workflow_capacity_service import try_lock
lock=try_lock(Path(sys.argv[1]))
print('held' if lock else 'busy',flush=True)
sys.stdin.readline()
if lock: lock.close()
"""
    child = subprocess.Popen([sys.executable,'-c',script,str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             text=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert child.stdout.readline().strip() == 'held'
    return child


def stop_child(child):
    if child.poll() is None:
        # Windows venv redirectors can own an actual Python child. Stop the
        # whole test-owned execution tree, not just its launcher.
        terminate_process_tree(child)
    child.wait(timeout=10)
    for stream in (child.stdin,child.stdout,child.stderr):
        stream.close()


def test_os_lock_survives_stale_metadata_and_releases_after_process_death(tmp_path):
    path = tmp_path/'workflow.lock'
    path.write_text(json.dumps({'pid':999999999,'job_id':'stale'}))
    child = hold_in_child(path)
    try:
        assert capacity.try_lock(path) is None
    finally:
        stop_child(child)
    assert path.exists()  # Persistent inode, not PID-file deletion/recreation.
    lock = capacity.try_lock(path)
    assert lock is not None
    lock.close()


def test_new_executor_waits_for_orphan_and_runs_after_it_exits(human_db, monkeypatch):
    job = queued_jobs(1)[0]
    path = human_db.with_name(human_db.name+'.workflow.lock')
    orphan = hold_in_child(path)
    called = []
    def execute(job_id, task_id):
        called.append(task_id)
        job_service.mark_job_completed(job_id)
    monkeypatch.setattr(job_worker,'_execute_video_cut',execute)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(job_worker.execute_job,job['id'])
            deadline = time.monotonic()+10
            while '等待旧执行' not in job_service.get_job(job['id'])['message'] and time.monotonic()<deadline:
                time.sleep(.02)
            assert '等待旧执行' in job_service.get_job(job['id'])['message']
            assert not called and not result.done()
            stop_child(orphan)
            assert result.result(timeout=10)['status'] == 'completed'
            assert called == [job['task_id']]
    finally:
        if orphan.poll() is None:
            stop_child(orphan)


@pytest.mark.parametrize('cancel',[False,True])
def test_occupied_slot_timeout_or_cancel_never_dispatches(human_db, monkeypatch, cancel):
    job = queued_jobs(1)[0]
    lock = capacity.try_lock(human_db.with_name(human_db.name+'.workflow.lock'))
    called = []
    monkeypatch.setattr(job_worker,'_execute_video_cut',lambda *a:called.append(a))
    monkeypatch.setattr(capacity,'WAIT_SECONDS',.15)
    try:
        if cancel:
            claimed = job_service.claim_job(job['id'],'manual')
            job_service.request_job_cancel(job['id'])
            with pytest.raises(job_service.JobLeaseLostError,match='取消'):
                job_worker.execute_job(job['id'],lease_owner='manual',lease_token=claimed['lease_token'],already_claimed=True)
        else:
            with pytest.raises(RuntimeError,match='仍占有'):
                job_worker.execute_job(job['id'])
        assert not called
        assert job_service.get_job(job['id'])['status'] == ('cancelled' if cancel else 'failed')
    finally:
        lock.close()
