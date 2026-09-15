"""Copy registered bytes under a fenced task Job, then activate verified media."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time

from app.db.database import get_connection
from app.services import job_service, storage_service
from app.services.material_batch_service import task_item
from app.services.material_catalog_service import MaterialError, _check_local_path, _read_material, _stamp, canonical, digest, validate_source
from app.services.media_preflight_service import MIN_SAFETY_MARGIN_BYTES, preflight_media

CHUNK_BYTES = 4 * 1024 * 1024


def _handle_stamp(handle):
    info = os.fstat(handle.fileno())
    if not stat.S_ISREG(info.st_mode):
        raise MaterialError("已打开的素材不是普通文件")
    return {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "device": info.st_dev, "inode": info.st_ino}


def _read_hash(handle, expected_size, destination=None):
    hashed, total = hashlib.sha256(), 0
    last_report = time.monotonic()
    while chunk := handle.read(CHUNK_BYTES):
        job_service.require_active_job_lease()
        total += len(chunk)
        if total > expected_size:
            raise MaterialError("复制期间素材大小发生变化")
        hashed.update(chunk)
        if destination is not None:
            destination.write(chunk)
        if time.monotonic() - last_report >= 5:
            job_id = job_service.current_job_lease()[0]
            job_service.update_job_progress(job_id, min(60, 10+int(total/expected_size*45)),
                f"{'复制' if destination is not None else '校验'}原片：{total // 1048576} / {expected_size // 1048576} MiB")
            last_report = time.monotonic()
    if total != expected_size:
        raise MaterialError("素材字节数不一致，未激活任务")
    return hashed.hexdigest()


def _hash_file(path, size):
    with path.open("rb") as handle:
        return _read_hash(handle, size)


def _context(c, job_id, task_id, payload):
    job = job_service.require_job_lease_with_connection(c, task_id=task_id,
        allowed_job_types={job_service.JOB_TYPE_MATERIAL_IMPORT})
    item = task_item(c, task_id)
    task = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not item or not task or task["is_deleted"]:
        raise MaterialError("导入任务不存在或已删除")
    material = _read_material(c, item["material_id"])
    expected = {"batch_id": item["batch_id"], "item_id": item["id"], "material_id": material["id"],
                "source_key": material["source_key"], "generation_sha256": item["generation_sha256"]}
    if job["id"] != job_id or item["job_id"] != job_id or job["payload_json"] != expected or payload != expected:
        raise MaterialError("导入 Job 与批次输入不一致，不能使用当前配置猜测恢复")
    return item, dict(task), material


def _committed(c, item, task, source_dir):
    row = c.execute("SELECT * FROM material_imports WHERE item_id=?", (item["id"],)).fetchone()
    if not row:
        return None
    record = dict(row)
    evidence = json.loads(record["evidence_json"])
    path = Path(record["stored_path"])
    if (digest(evidence) != record["evidence_sha256"] or evidence.get("stored_path") != str(path)
            or evidence.get("source_sha256") != record["source_sha256"]
            or evidence.get("size_bytes") != record["size_bytes"] or evidence.get("item_id") != item["id"]
            or path.parent != source_dir or task["original_video_path"] != str(path)):
        raise MaterialError("已提交导入证据不一致，拒绝重复复制或覆盖")
    return {"path": path, "evidence": evidence, "size": record["size_bytes"], "sha256": record["source_sha256"]}


def _cleanup_abandoned(job_id, task_id, payload, source_dir, token):
    with get_connection() as c:
        c.execute("BEGIN IMMEDIATE")
        _context(c, job_id, task_id, payload)
        protected = {r[0] for r in c.execute("SELECT stored_path FROM material_imports")}
        for path in source_dir.glob("import-*"):
            match = re.fullmatch(r"import-([a-f0-9]{32})(?:\.part)?\.(mp4|mov|mkv|avi|flv|webm|m4v|ts)", path.name)
            if not match or match[1] == token or str(path) in protected:
                continue
            _check_local_path(path)
            try:
                path.unlink()  # Previous claim only; never an active referenced source.
            except PermissionError:
                pass  # A terminated Windows child may still hold the file; retry on recovery.
        c.commit()


def execute_import(job_id, task_id, payload):
    with get_connection() as c:
        item, task, material = _context(c, job_id, task_id, payload)
    task_dir = storage_service.get_task_directory(task_id, task["task_dir_name"])
    source_dir = task_dir / "source"
    with get_connection() as c:
        previous = _committed(c, item, task, source_dir)
    if previous:
        _check_local_path(previous["path"])
        if _hash_file(previous["path"], previous["size"]) != previous["sha256"]:
            raise MaterialError("已提交的原片副本已损坏，请人工检查；不会自动覆盖")
        return {"task_id": task_id, "source_sha256": previous["sha256"], "recovered": True}

    original = validate_source(material["source"])
    job_service.require_active_job_lease()
    storage_service.create_task_directory(task_id, task["task_dir_name"])
    _check_local_path(source_dir)
    lease = job_service.current_job_lease()
    token = lease[2]
    _cleanup_abandoned(job_id, task_id, payload, source_dir, token)
    if shutil.disk_usage(source_dir).free < material["size_bytes"] + MIN_SAFETY_MARGIN_BYTES:
        raise MaterialError("任务存储空间不足以复制原片并保留安全余量")
    temp = source_dir / f"import-{token}.part{original.suffix.lower()}"
    final = source_dir / f"import-{token}{original.suffix.lower()}"
    try:
        job_service.update_job_progress(job_id, 5, "正在复制原片，外部源文件保持不动")
        with original.open("rb") as source, temp.open("xb") as destination:
            # A swapped path must not grant access to a different opened file.
            opened_stamp = _handle_stamp(source)
            expected_stamp = material["source"]["stamp"]
            # Windows Python can report different ctime semantics for stat and
            # fstat after copy2. Compare ctime within each API, never across them.
            identity_keys = ("size", "mtime_ns", "device", "inode") if os.name == "nt" else tuple(expected_stamp)
            if any(opened_stamp.get(key) != expected_stamp[key] for key in identity_keys):
                raise MaterialError("打开的原片句柄与登记版本不同")
            validate_source(material["source"])
            source_sha = _read_hash(source, material["size_bytes"], destination)
            destination.flush()
            os.fsync(destination.fileno())
            source.seek(0)
            if _read_hash(source, material["size_bytes"]) != source_sha or _handle_stamp(source) != opened_stamp:
                raise MaterialError("复制期间原片内容发生变化")
            validate_source(material["source"])
        job_service.update_job_progress(job_id, 65, "正在核对完整内容哈希与媒体解码")
        if _hash_file(temp, material["size_bytes"]) != source_sha:
            raise MaterialError("原片副本完整内容哈希不一致")
        copied_stamp = _stamp(temp)
        limit = task["highlight_total_limit"] if task["selection_profile"] == "long_live_talk" else task["candidate_clip_count"]
        preflight = preflight_media(temp, total_output_limit=limit).to_dict()
        now = datetime.now(timezone.utc).isoformat()
        evidence = {"schema": "material-import-v1", "item_id": item["id"], "source_key": material["source_key"],
                    "source_sha256": source_sha, "size_bytes": material["size_bytes"], "stored_path": str(final),
                    "job_id": job_id, "lease_token": token, "preflight": {**preflight, "path": str(final)}, "imported_at": now}
        with get_connection() as c:
            c.execute("BEGIN IMMEDIATE")
            _context(c, job_id, task_id, payload)
            if c.execute("SELECT 1 FROM material_imports WHERE item_id=?", (item["id"],)).fetchone():
                raise MaterialError("导入已由其他执行提交，拒绝覆盖")
            _check_local_path(source_dir)
            _check_local_path(temp)
            if _stamp(temp) != copied_stamp:
                raise MaterialError("已校验的临时副本发生变化，拒绝激活")
            if final.exists():
                raise MaterialError("本次导入目标已存在，拒绝覆盖")
            # Token-specific paths keep late workers from replacing another claim's bytes.
            os.replace(temp, final)
            c.execute("INSERT INTO material_imports(item_id,source_sha256,stored_path,size_bytes,evidence_json,evidence_sha256,created_at) VALUES(?,?,?,?,?,?,?)",
                      (item["id"], source_sha, str(final), material["size_bytes"], canonical(evidence), digest(evidence), now))
            columns = {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
            legacy = ",source_path=?" if "source_path" in columns else ""
            values = [str(final), now] + ([str(final)] if legacy else []) + [task_id]
            c.execute(f"UPDATE tasks SET original_video_path=?,status='pending_processing',progress=0,error_message=NULL,last_error=NULL,updated_at=?{legacy} WHERE id=?", values)
            c.commit()
        return {"task_id": task_id, "source_sha256": source_sha, "recovered": False}
    finally:
        # Only this claim's incomplete owned file is eligible; external source is never removed.
        if temp.exists():
            _check_local_path(temp)
            temp.unlink()
