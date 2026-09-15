from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import hashlib
import threading
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from app.db import database as db
from app.services import job_service, job_worker, workflow_capacity_service as capacity
from app.services.managed_process_service import popen_process_group, terminate_process_tree
from tests.test_human_review import human_db as _human_db_fixture

human_db = _human_db_fixture


def queued_jobs(count=10, job_type="video_cut"):
    jobs = []
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for index in range(count):
        task_id = uuid4().hex
        with db.get_connection() as c:
            c.execute("INSERT INTO tasks(id,task_name,task_dir_name,selection_profile,created_at,updated_at) VALUES(?,?,?,'general',?,?)",
                      (task_id,f'EP{index:03d}',task_id,now,now))
            c.commit()
        jobs.append(job_service.create_job(task_id,job_type))
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


def test_mixed_lanes_claim_at_most_one_each_and_cut_skips_background_backlog(human_db):
    background = queued_jobs(5, 'transcript')
    cuts = queued_jobs(5)
    jobs = background + cuts
    with ThreadPoolExecutor(max_workers=10) as pool:
        claimed = list(pool.map(lambda j: job_service.claim_job(j['id'], j['id']), jobs))
    winners = [j for j in claimed if j]
    assert sorted(j['job_type'] for j in winners) == ['transcript', 'video_cut']
    cut = next(j for j in winners if j['job_type'] == 'video_cut')
    finish(cut)
    following = job_service.claim_next_job('cut-worker', lane='cut')
    assert following and following['job_type'] == 'video_cut'
    assert job_service.claim_next_job('background-worker', lane='background') is None


def test_same_task_waits_but_other_cut_can_proceed_and_cancel_does_not_free_early(human_db):
    original = queued_jobs(1, 'transcript')[0]
    same_task = job_service.create_job(original['task_id'], 'video_cut')
    other_task = queued_jobs(1)[0]
    busy = job_service.claim_job(original['id'], 'background')
    assert '这条素材仍在处理' in job_service.get_job(same_task['id'])['queue_hint']
    assert job_service.claim_job(same_task['id'], 'direct') is None
    cut = job_service.claim_next_job('cut', lane='cut')
    assert cut['id'] == other_task['id']
    job_service.request_job_cancel(busy['id'])
    finish(cut)
    assert job_service.claim_next_job('cut', lane='cut') is None
    with job_service.job_lease_context(busy['id'], busy['lease_owner'], busy['lease_token']):
        job_service.mark_job_cancelled(busy['id'])
    assert job_service.claim_next_job('cut', lane='cut')['id'] == same_task['id']


def test_runner_dispatches_cut_while_background_is_blocked_and_stops_both(human_db, monkeypatch):
    background = queued_jobs(1, 'transcript')[0]
    cut = queued_jobs(1)[0]
    began = {'transcript': threading.Event(), 'video_cut': threading.Event()}
    release = threading.Event()
    runner = job_worker.WorkflowJobRunner(poll_seconds=.02)

    def execute(job_id):
        job = job_service.get_job(job_id)
        with job_service.job_lease_context(job_id, runner.owner, job['lease_token']):
            with capacity.execution_slot(job_id):
                began[job['job_type']].set()
                assert release.wait(10)
                job_service.mark_job_completed(job_id)

    monkeypatch.setattr(runner, '_run_job_subprocess', execute)
    runner._next_visual_cleanup = float('inf')
    try:
        runner.start()
        runner.start()  # No duplicate thread per lane.
        assert began['transcript'].wait(5) and began['video_cut'].wait(5)
        assert job_service.get_job(background['id'])['status'] == 'running'
        assert job_service.get_job(cut['id'])['status'] == 'running'
    finally:
        release.set()
        runner.stop()
    assert not runner._thread.is_alive() and not runner._cut_thread.is_alive()
    assert job_service.get_job(background['id'])['status'] == 'completed'
    assert job_service.get_job(cut['id'])['status'] == 'completed'


def test_stop_terminates_both_executors_before_releasing_their_leases(human_db, monkeypatch):
    jobs = queued_jobs(1, 'transcript') + queued_jobs(1)
    processes = []
    started = threading.Event()
    runner = job_worker.WorkflowJobRunner(poll_seconds=.02)
    runner._next_visual_cleanup = float('inf')

    class Process:
        stopped = False
        def poll(self):
            return 0 if self.stopped else None

    def spawn(*_args, **_kwargs):
        process = Process()
        processes.append(process)
        if len(processes) == 2:
            started.set()
        return process

    def terminate(process):
        # Each parent still owns its DB lease when terminating the child tree.
        assert any(job_service.get_job(j['id'])['status'] == 'running' for j in jobs)
        process.stopped = True

    monkeypatch.setattr(job_worker, 'popen_process_group', spawn)
    monkeypatch.setattr(job_worker, 'terminate_process_tree', terminate)
    try:
        runner.start()
        assert started.wait(5)
    finally:
        runner.stop()
    assert len(processes) == 2 and all(p.stopped for p in processes)
    for job in jobs:
        restored = job_service.get_job(job['id'])
        assert restored['status'] == 'queued' and restored['lease_token'] is None


def hold_in_child(path):
    script = """from pathlib import Path
import sys
from app.services.workflow_capacity_service import try_lock
lock=try_lock(Path(sys.argv[1]))
print('held' if lock else 'busy',flush=True)
sys.stdin.readline()
if lock: lock.close()
"""
    child = popen_process_group([sys.executable,'-c',script,str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
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


def test_expired_other_lane_executor_keeps_same_task_locked(human_db, monkeypatch):
    job = queued_jobs(1)[0]
    task_key = hashlib.sha256(job['task_id'].encode()).hexdigest()
    path = human_db.with_name(human_db.name+'.workflow-tasks') / (task_key+'.lock')
    orphan = hold_in_child(path)
    monkeypatch.setattr(capacity, 'WAIT_SECONDS', .15)
    called = []
    monkeypatch.setattr(job_worker, '_execute_video_cut', lambda *a: called.append(a))
    try:
        with pytest.raises(RuntimeError, match='仍占有'):
            job_worker.execute_job(job['id'])
        assert not called
    finally:
        stop_child(orphan)


def test_cut_os_slot_does_not_wait_for_other_tasks_background_slot(human_db, monkeypatch):
    job = queued_jobs(1)[0]
    orphan = hold_in_child(human_db.with_name(human_db.name+'.workflow-background.lock'))
    monkeypatch.setattr(job_worker, '_execute_video_cut', lambda jid, _: job_service.mark_job_completed(jid))
    try:
        assert job_worker.execute_job(job['id'])['status'] == 'completed'
    finally:
        stop_child(orphan)


@pytest.mark.parametrize('job_type', ['video_cut', 'transcript'])
def test_both_lanes_wait_for_legacy_exclusive_executor(human_db, monkeypatch, job_type):
    job = queued_jobs(1, job_type)[0]
    orphan = hold_in_child(human_db.with_name(human_db.name+'.workflow.lock'))
    monkeypatch.setattr(capacity, 'WAIT_SECONDS', .15)
    monkeypatch.setattr(job_worker, '_execute_video_cut', lambda *_: pytest.fail('旧进程退出前不可切片'))
    monkeypatch.setattr(job_worker, '_execute_transcript', lambda *_: pytest.fail('旧进程退出前不可转写'))
    try:
        with pytest.raises(RuntimeError, match='仍占有'):
            job_worker.execute_job(job['id'])
    finally:
        stop_child(orphan)


def test_shared_legacy_guard_excludes_old_process_without_blocking_new_lanes(tmp_path):
    path = tmp_path/'legacy.lock'
    first = capacity.try_lock(path, shared=True)
    second = capacity.try_lock(path, shared=True)
    assert first and second
    try:
        assert capacity.try_lock(path) is None
    finally:
        first.close()
        second.close()
    exclusive = capacity.try_lock(path)
    assert exclusive
    exclusive.close()


def test_sync_cut_cannot_bypass_queued_task_or_lane_lock(human_db):
    job = queued_jobs(1)[0]
    calls = []
    guarded = capacity.cut_execution_boundary(lambda tid: calls.append(tid))
    lane = capacity.try_lock(human_db.with_name(human_db.name+'.workflow-cut.lock'))
    try:
        with pytest.raises(ValueError, match='独立队列'):
            guarded(job['task_id'])
        assert not calls
    finally:
        lane.close()
    claimed = job_service.claim_job(job['id'], 'queue')
    with pytest.raises(ValueError, match='本任务结束'):
        guarded(job['task_id'])
    assert not calls
    finish(claimed)
    guarded(job['task_id'])
    assert calls == [job['task_id']]


@pytest.mark.parametrize('outer_slot', [True, False])
def test_automatic_pipeline_cut_uses_physical_cut_limit(human_db, monkeypatch, outer_slot):
    job = job_service.claim_job(queued_jobs(1, 'auto_pipeline')[0]['id'], 'pipeline')
    calls = []
    guarded = capacity.cut_execution_boundary(lambda tid: calls.append(tid))
    monkeypatch.setattr(capacity, 'WAIT_SECONDS', .15)
    with job_service.job_lease_context(job['id'], job['lease_owner'], job['lease_token']):
        from contextlib import nullcontext
        with capacity.execution_slot(job['id']) if outer_slot else nullcontext():
            lock = capacity.try_lock(human_db.with_name(human_db.name+'.workflow-cut.lock'))
            assert lock is not None
            try:
                with pytest.raises(RuntimeError, match='仍占有'):
                    guarded(job['task_id'])
                assert not calls
            finally:
                lock.close()
            guarded(job['task_id'])
            assert calls == [job['task_id']]


def test_healthy_automatic_cut_does_not_hit_orphan_wait_timeout(human_db, monkeypatch):
    automatic = job_service.claim_job(queued_jobs(1, 'auto_pipeline')[0]['id'], 'automatic')
    manual = queued_jobs(1)[0]
    monkeypatch.setattr(capacity, 'WAIT_SECONDS', .1)
    called = []
    monkeypatch.setattr(job_worker, '_execute_video_cut',
                        lambda jid, tid: (called.append(tid), job_service.mark_job_completed(jid)))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with job_service.job_lease_context(automatic['id'], automatic['lease_owner'], automatic['lease_token']):
            with capacity._locked_slot(automatic['id'], human_db.with_name(human_db.name+'.workflow-cut.lock')):
                result = pool.submit(job_worker.execute_job, manual['id'])
                deadline = time.monotonic()+5
                while '等待旧执行' not in job_service.get_job(manual['id'])['message'] and time.monotonic() < deadline:
                    time.sleep(.02)
                assert '等待旧执行' in job_service.get_job(manual['id'])['message']
                time.sleep(.35)  # Several orphan timeouts, but the actual owner lease is live.
                assert not result.done() and not called
            finish(automatic)
        assert result.result(timeout=5)['status'] == 'completed'
        assert called == [manual['task_id']]


@pytest.mark.parametrize('stale_token', [False, True])
def test_unrelated_live_pipeline_does_not_hide_orphan_cut_lock(human_db, monkeypatch, stale_token):
    automatic = job_service.claim_job(queued_jobs(1, 'auto_pipeline')[0]['id'], 'automatic')
    manual = queued_jobs(1)[0]
    lock = capacity.try_lock(human_db.with_name(human_db.name+'.workflow-cut.lock'))
    try:
        if stale_token:
            lock.write(b' '+json.dumps({'job_id':automatic['id'], 'lease_token':'expired-token'}).encode())
            lock.flush()
        monkeypatch.setattr(capacity, 'WAIT_SECONDS', .1)
        monkeypatch.setattr(job_worker, '_execute_video_cut', lambda *_: pytest.fail('orphan still owns lock'))
        with pytest.raises(RuntimeError, match='仍占有'):
            job_worker.execute_job(manual['id'])
        assert job_service.get_job(automatic['id'])['status'] == 'running'
    finally:
        lock.close()


def test_healthy_legacy_sync_cut_keeps_waiting_queue_alive(human_db, monkeypatch):
    manual, legacy = queued_jobs(2)
    job_service.request_job_cancel(legacy['id'])  # This caller uses the old sync API.
    entered, release = threading.Event(), threading.Event()
    def old_cut(_):
        entered.set()
        assert release.wait(5)
    guarded = capacity.cut_execution_boundary(old_cut)
    monkeypatch.setattr(capacity, 'WAIT_SECONDS', .1)
    monkeypatch.setattr(job_worker, '_execute_video_cut', lambda jid, _: job_service.mark_job_completed(jid))
    with ThreadPoolExecutor(max_workers=2) as pool:
        synchronous = pool.submit(guarded, legacy['task_id'])
        try:
            assert entered.wait(5)
            queued = pool.submit(job_worker.execute_job, manual['id'])
            deadline = time.monotonic()+5
            while '等待旧执行' not in job_service.get_job(manual['id'])['message'] and time.monotonic() < deadline:
                time.sleep(.02)
            assert '等待旧执行' in job_service.get_job(manual['id'])['message']
            time.sleep(.35)
            assert not queued.done()
        finally:
            release.set()
        synchronous.result(timeout=5)
        assert queued.result(timeout=5)['status'] == 'completed'


def test_new_executor_waits_for_orphan_and_runs_after_it_exits(human_db, monkeypatch):
    job = queued_jobs(1)[0]
    path = human_db.with_name(human_db.name+'.workflow-cut.lock')
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
    lock = capacity.try_lock(human_db.with_name(human_db.name+'.workflow-cut.lock'))
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
