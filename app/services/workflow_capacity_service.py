"""One execution process per SQLite database, including expired-lease orphans.

Use the same OS locking primitives as the existing publication Worker. Keep
its specialized lock untouched. This persistent local file is never unlinked:
deleting a live lock path would let another process lock a different inode.
"""
from contextlib import contextmanager
import errno
import json
import os
import time

from app.db import database
from app.services import job_service

WAIT_SECONDS = 120


def _require_slot_lease(job_id):
    active = job_service.require_active_job_lease()
    if not active or active[0] != job_id:
        raise job_service.JobLeaseLostError("重型执行槽位需要当前 Workflow Job 租约")
    if job_service.is_cancel_requested(job_id):
        job_service.mark_job_cancelled(job_id, "等待重型执行槽位时已取消，尚未开始处理")
        raise job_service.JobLeaseLostError("执行槽位等待已取消")
    return active


def try_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return None
        raise
    return handle


@contextmanager
def execution_slot(job_id):
    _require_slot_lease(job_id)
    db_path = database.settings.database_path.resolve()
    path = db_path.with_name(db_path.name+".workflow.lock")
    started, last_report, handle = time.monotonic(), float("-inf"), None
    try:
        while handle is None:
            _require_slot_lease(job_id)
            handle = try_lock(path)
            if handle is not None:
                break
            if time.monotonic() - started >= WAIT_SECONDS:
                raise RuntimeError("旧执行进程仍占有重型处理槽位，尚未执行本任务；请检查旧进程退出后重试")
            if time.monotonic() - last_report >= 5:
                job_service.update_job_progress(job_id, 10, "等待旧执行进程释放重型处理槽位，尚未开始处理")
                last_report = time.monotonic()
            time.sleep(.1)
        _require_slot_lease(job_id)
        # Metadata is diagnostic only. A stale PID never grants lock ownership.
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "job_id": job_id}, separators=(",", ":")).encode())
        handle.flush()
        yield
    finally:
        if handle is not None:
            handle.close()  # OS also releases the lock if this executor dies.
