"""只回收托管视觉 JPG；保留结构证据、manifest、原片与发布封面。"""

from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import stat
import time

from app.db.database import get_connection
from app.services.storage_service import get_task_directory
from app.services.visual_cache_files import locked_cache_files

logger = logging.getLogger(__name__)


def cache_directory(task, relative):
    if not re.fullmatch(r"analysis/visual/[a-f0-9]{32}", relative):
        raise ValueError("不是托管视觉目录")
    root = get_task_directory(task["id"], task["task_dir_name"] or task["id"]).absolute()
    directory = root / relative
    if any(p.is_symlink() for p in (directory, directory.parent, directory.parent.parent)) or not directory.resolve().is_relative_to(root):
        raise ValueError("视觉目录路径异常")
    return directory


def _frame(row, task, index):
    frames = json.loads(row["request_json"])["sampling"]["frames"]
    if not 0 <= index < len(frames):
        raise ValueError("帧不存在")
    frame = frames[index]
    if not re.fullmatch(r"[a-f0-9]{32}\.jpg", frame["file_name"]):
        raise ValueError("帧路径无效")
    directory = cache_directory(task, row["cache_relative_dir"])
    with locked_cache_files(directory) as files:
        data = files.read(frame["file_name"], 2 * 1024 * 1024)
    if len(data) != frame["size_bytes"] or hashlib.sha256(data).hexdigest() != frame["sha256"]:
        raise ValueError("帧缓存内容校验失败")
    return data


def read_visual_frame(task_id, evidence_id, index):
    with get_connection() as connection:
        task = connection.execute("SELECT id,task_dir_name FROM tasks WHERE id=? AND COALESCE(is_deleted,0)=0", (task_id,)).fetchone()
        row = connection.execute("SELECT * FROM candidate_visual_evidence WHERE id=? AND task_id=?", (evidence_id, task_id)).fetchone()
        if not task or not row or row["cache_cleaned_at"]:
            raise ValueError("视觉缓存已清理或不存在；结构证据仍保留")
        return _frame(row, task, index)


def pin_visual(task_id, evidence_id, pinned):
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        task = connection.execute("SELECT id,task_dir_name FROM tasks WHERE id=? AND COALESCE(is_deleted,0)=0", (task_id,)).fetchone()
        row = connection.execute("SELECT * FROM candidate_visual_evidence WHERE id=? AND task_id=?", (evidence_id, task_id)).fetchone()
        if not task or not row:
            raise ValueError("视觉证据不存在")
        if pinned:
            frames = json.loads(row["request_json"])["sampling"]["frames"]
            if row["cache_cleaned_at"] or not frames:
                raise ValueError("图片已清理或从未产生，不能固定")
            for index in range(len(frames)):
                _frame(row, task, index)
        connection.execute("UPDATE candidate_visual_evidence SET pinned=? WHERE id=?", (int(pinned), evidence_id))
        connection.commit()
    return {"status": "ok", "pinned": bool(pinned)}


def _task_protected(connection, task_id):
    jobs = connection.execute("SELECT status,checkpoint_json FROM workflow_jobs WHERE task_id=?", (task_id,)).fetchall()
    for job in jobs:
        if job["status"] in {"queued", "running"}:
            return True
        try:
            namespaces = json.loads(job["checkpoint_json"] or "{}").get("_ai_analysis_units_v1", {}).get("namespaces", {})
            if any(unit.get("status") in {"running", "uncertain"} for key, ns in namespaces.items() if key.startswith("optional-visual") for unit in ns.get("units", {}).values()):
                return True
        except (ValueError, TypeError, AttributeError):
            return True
    return False


def _old(timestamp, now, days):
    try:
        stamp = datetime.fromisoformat(timestamp)
        return stamp.tzinfo is not None and (now - stamp).total_seconds() >= days * 86400
    except (ValueError, TypeError):
        return False


def _remove_images(directory, deadline):
    if not directory.exists():
        return 0
    removed = 0
    with locked_cache_files(directory) as files:
        for name, info in files.entries():
            if time.monotonic() >= deadline:
                raise TimeoutError("cleanup_time_budget_exhausted")
            if re.fullmatch(r"[a-f0-9]{32}\.jpg", name):
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("unsafe_cache_entry")
                files.remove(name)
                removed += 1
    return removed


def cleanup_visual_cache(*, now=None, seconds=3, limit=100, task_cursor=""):
    """低频闲置维护；每组在写锁下复查并删除，pin/领取/恢复不能穿插。"""
    now = now or datetime.now(timezone.utc)
    deadline = time.monotonic() + min(max(seconds, 0.01), 10)
    result = {"groups": 0, "orphans": 0, "images": 0, "failures": 0, "next_task_cursor": task_cursor}
    with get_connection() as connection:
        groups = connection.execute("""SELECT task_id,cache_relative_dir FROM candidate_visual_evidence
            GROUP BY task_id,cache_relative_dir HAVING SUM(cache_cleaned_at IS NULL)>0
            AND SUM(pinned)=0 AND SUM(status='pending' OR call_status IN ('pending','uncertain'))=0
            ORDER BY MAX(updated_at) LIMIT ?""", (min(max(int(limit), 1), 1000),)).fetchall()
    for group in groups:
        if time.monotonic() >= deadline:
            break
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute("SELECT id,task_dir_name FROM tasks WHERE id=? AND COALESCE(is_deleted,0)=0", (group[0],)).fetchone()
            rows = connection.execute("SELECT * FROM candidate_visual_evidence WHERE task_id=? AND cache_relative_dir=?", tuple(group)).fetchall()
            if not task or _task_protected(connection, group[0]) or any(r["pinned"] or r["status"] == "pending" or r["call_status"] in {"pending","uncertain"} or not _old(r["updated_at"], now, 7 if r["status"] in {"completed","partial"} else 1) for r in rows):
                continue
            try:
                result["images"] += _remove_images(cache_directory(task, group[1]), deadline)
                connection.execute("UPDATE candidate_visual_evidence SET cache_cleaned_at=?,cache_cleanup_error='' WHERE task_id=? AND cache_relative_dir=?", (now.isoformat(), *tuple(group)))
                result["groups"] += 1
            except (OSError, ValueError) as exc:
                connection.execute("UPDATE candidate_visual_evidence SET cache_cleanup_error=? WHERE task_id=? AND cache_relative_dir=?", (type(exc).__name__, *tuple(group)))
                result["failures"] += 1
            connection.commit()
    # 进程可能在采样后、证据落库前退出；只看直属视觉目录，不递归扫描素材池。
    with get_connection() as connection:
        tasks = connection.execute("SELECT id,task_dir_name FROM tasks WHERE COALESCE(is_deleted,0)=0 AND id>? ORDER BY id LIMIT ?", (task_cursor, min(max(int(limit), 1), 1000))).fetchall()
    if not tasks:
        result["next_task_cursor"] = ""
    for task in tasks:
        if time.monotonic() >= deadline:
            break
        result["next_task_cursor"] = task["id"]
        parent = get_task_directory(task["id"], task["task_dir_name"] or task["id"]) / "analysis" / "visual"
        if not parent.is_dir() or parent.is_symlink() or parent.parent.is_symlink():
            continue
        for directory in parent.iterdir():
            if time.monotonic() >= deadline:
                break
            if not re.fullmatch(r"[a-f0-9]{32}", directory.name) or directory.is_symlink() or not directory.is_dir():
                continue
            relative = "analysis/visual/" + directory.name
            with get_connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if _task_protected(connection, task["id"]) or connection.execute("SELECT 1 FROM candidate_visual_evidence WHERE task_id=? AND cache_relative_dir=?", (task["id"],relative)).fetchone():
                    continue
                try:
                    directory = cache_directory(task, relative)
                    files = list(directory.iterdir())
                    if not any(re.fullmatch(r"[a-f0-9]{32}\.jpg", p.name) for p in files) or any(p.is_symlink() or now.timestamp() - p.stat().st_mtime < 86400 for p in files):
                        continue
                    removed = _remove_images(directory, deadline)
                    result["images"] += removed
                    result["orphans"] += bool(removed)
                except (OSError, ValueError):
                    result["failures"] += 1
                connection.commit()
    if result["groups"] or result["orphans"] or result["failures"]:
        logger.info("Visual cache maintenance: %s", result)
    return result


def list_visual_evidence(task_id, *, run_id=None, evidence_id=None, offset=0, limit=50):
    with get_connection() as connection:
        task = connection.execute("SELECT id FROM tasks WHERE id=? AND COALESCE(is_deleted,0)=0", (task_id,)).fetchone()
        if not task:
            raise ValueError("任务不存在")
        clause, params = "task_id=?", [task_id]
        if run_id:
            clause += " AND analysis_run_id=?"
            params.append(run_id)
        if evidence_id:
            clause += " AND id=?"
            params.append(evidence_id)
        total = connection.execute(f"SELECT COUNT(*) FROM candidate_visual_evidence WHERE {clause}", params).fetchone()[0]
        rows = connection.execute(f"SELECT * FROM candidate_visual_evidence WHERE {clause} ORDER BY created_at DESC,id LIMIT ? OFFSET ?", (*params,limit,offset)).fetchall()
    items = []
    for row in rows:
        request = json.loads(row["request_json"])
        payload = json.loads(row["evidence_json"]) if row["evidence_json"] else {}
        items.append({**{k: row[k] for k in ("id","candidate_key","analysis_run_id","workflow_job_id","provider","model","status","call_status","failure_reason","pinned","cache_cleaned_at","cache_cleanup_error","request_fingerprint","evidence_sha256","created_at")},
            "sampling": {k:v for k,v in request["sampling"].items() if k not in {"manifest_file_name","frames"}},
            "frames": [{**{k:v for k,v in frame.items() if k != "file_name"}, "url": f"/api/tasks/{task_id}/visual-evidence/{row['id']}/frames/{i}"} for i,frame in enumerate(request["sampling"]["frames"])],
            "response": payload.get("response"), "raw_response_sha256": payload.get("raw_response_sha256")})
    return {"items": items, "total": total, "offset": offset, "limit": limit}
