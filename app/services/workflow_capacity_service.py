"""One process per execution lane and task, including expired-lease orphans.

Use the same OS locking primitives as the existing publication Worker. Keep
its specialized lock untouched. This persistent local file is never unlinked:
deleting a live lock path would let another process lock a different inode.
"""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
import errno
from functools import wraps
import hashlib
import json
import os
import time

from app.db import database
from app.services import job_service

WAIT_SECONDS = 120
_held_task = ContextVar('workflow_execution_task', default=None)


def _require_slot_lease(job_id):
    active = job_service.require_active_job_lease()
    if not active or active[0] != job_id:
        raise job_service.JobLeaseLostError("重型执行槽位需要当前 Workflow Job 租约")
    if job_service.is_cancel_requested(job_id):
        job_service.mark_job_cancelled(job_id, "等待重型执行槽位时已取消，尚未开始处理")
        raise job_service.JobLeaseLostError("执行槽位等待已取消")
    return active


def try_lock(path, *, shared=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if shared:
                # Shared first-byte lock interoperates with the old CRT exclusive
                # lock. Keep it for the entire execution, without writing metadata.
                import ctypes
                from ctypes import wintypes
                class Overlapped(ctypes.Structure):
                    _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                                ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
                api = ctypes.WinDLL('kernel32', use_last_error=True).LockFileEx
                api.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                wintypes.DWORD, ctypes.POINTER(Overlapped)]
                api.restype = wintypes.BOOL
                overlap = Overlapped()
                if not api(msvcrt.get_osfhandle(handle.fileno()), 1, 0, 1, 0, ctypes.byref(overlap)):
                    error = ctypes.get_last_error()
                    if error == 33:  # ERROR_LOCK_VIOLATION
                        handle.close()
                        return None
                    raise ctypes.WinError(error)
            else:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return None
        raise
    return handle


@contextmanager
def _locked_slot(job_id, path, *, shared=False, cut_lane=False):
    started, last_report, handle = time.monotonic(), float("-inf"), None
    try:
        while handle is None:
            _require_slot_lease(job_id)
            handle = try_lock(path, shared=shared)
            if handle is not None:
                break
            # A healthy cut (including automatic pre-cut) may take longer than
            # orphan recovery's timeout. Keep waiting while its lease is live.
            if cut_lane:
                with database.get_connection() as connection:
                    live = connection.execute(
                        """SELECT 1 FROM workflow_jobs WHERE id<>? AND status='running'
                           AND lease_expires_at>? AND job_type IN ('video_cut','auto_pipeline')""",
                        (job_id, job_service._now_iso()),
                    ).fetchone()
                if live:
                    started = time.monotonic()
            if time.monotonic() - started >= WAIT_SECONDS:
                raise RuntimeError("旧执行进程仍占有重型处理槽位，尚未执行本任务；请检查旧进程退出后重试")
            if time.monotonic() - last_report >= 5:
                job_service.update_job_progress(job_id, 10, "等待旧执行进程释放重型处理槽位，尚未开始处理")
                last_report = time.monotonic()
            time.sleep(.1)
        _require_slot_lease(job_id)
        # Metadata is diagnostic only. A stale PID never grants lock ownership.
        if not shared:
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps({"pid": os.getpid(), "job_id": job_id}, separators=(",", ":")).encode())
            handle.flush()
        yield
    finally:
        if handle is not None:
            handle.close()  # OS also releases the lock if this executor dies.


@contextmanager
def execution_slot(job_id):
    _require_slot_lease(job_id)
    job = job_service.get_job(job_id)
    with ExitStack() as stack:
        lane = job_service.execution_lane(job["job_type"])
        for index, (path, shared) in enumerate(_slot_paths(job["task_id"] or job_id, lane)):
            stack.enter_context(_locked_slot(job_id, path, shared=shared, cut_lane=lane == 'cut' and index == 1))
        _require_slot_lease(job_id)
        token = _held_task.set(job["task_id"])
        try:
            yield
        finally:
            _held_task.reset(token)


def _slot_paths(task_id, lane):
    db_path = database.settings.database_path.resolve()
    task_key = hashlib.sha256(str(task_id).encode()).hexdigest()
    return [(db_path.with_name(db_path.name+".workflow.lock"), True),
            (db_path.with_name(db_path.name+f".workflow-{lane}.lock"), False),
            (db_path.with_name(db_path.name+".workflow-tasks") / (task_key+".lock"), False)]


def cut_execution_boundary(function):
    """Legacy synchronous callers use the same locks as queued cuts."""
    @wraps(function)
    def guarded(task_id, *args, **kwargs):
        lease = job_service.current_job_lease()
        if lease:
            job_service.require_active_job_lease()
            job = job_service.get_job(lease[0])
            if job['task_id'] != task_id:
                raise job_service.JobLeaseLostError('切片任务与执行租约不一致')
            if _held_task.get() == task_id:
                if job_service.execution_lane(job['job_type']) == 'cut':
                    return function(task_id, *args, **kwargs)
                # Automatic pipelines own the background/task slots already.
                # Their actual cut stage shares the same physical cut limit.
                path, _ = _slot_paths(task_id, 'cut')[1]
                with _locked_slot(lease[0], path, cut_lane=True):
                    return function(task_id, *args, **kwargs)
            with execution_slot(lease[0]):
                return guarded(task_id, *args, **kwargs)
        with ExitStack() as stack:
            for path, shared in _slot_paths(task_id, 'cut'):
                handle = try_lock(path, shared=shared)
                if handle is None:
                    raise ValueError('切片通道或这条素材正在处理，请使用“生成切片”加入独立队列')
                stack.callback(handle.close)
            with database.get_connection() as connection:
                if connection.execute("SELECT 1 FROM workflow_jobs WHERE task_id=? AND status='running' AND lease_expires_at>?",
                                      (task_id, job_service._now_iso())).fetchone():
                    raise ValueError('这条素材正在处理，请等待本任务结束后切片')
            return function(task_id, *args, **kwargs)
    return guarded
