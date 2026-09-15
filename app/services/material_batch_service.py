"""Atomic batch requests reuse task creation and the existing Workflow Job ledger."""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from app.db.database import get_connection
from app.models.material_batch import MaterialBatchCreate
from app.services import content_profile_service as profiles, job_service
from app.services.material_catalog_service import MaterialError, _read_material, canonical, digest, validate_source
from app.services.task_lifecycle_service import insert_task_record_with_connection


def task_item(connection, task_id):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='material_batch_items'").fetchone():
        return None  # Pre-migration fixtures and legacy initialization.
    row = connection.execute("SELECT * FROM material_batch_items WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    generation = json.loads(item.pop("generation_json"))
    if digest(generation) != item["generation_sha256"]:
        raise MaterialError("批次策略证据不一致，不能继续处理")
    return {**item, "generation": generation}


def require_editable_policy(connection, task_id):
    if task_item(connection, task_id):
        raise MaterialError("批次任务策略已冻结；更换策略请明确创建新生产任务")


def require_imported_source(connection, task_id):
    item = task_item(connection, task_id)
    if not item:
        return
    row = connection.execute("SELECT x.*,t.original_video_path,t.is_deleted FROM material_imports x JOIN tasks t ON t.id=? WHERE x.item_id=?",
                            (task_id, item["id"])).fetchone()
    if not row or row["is_deleted"]:
        raise MaterialError("素材尚未完成复制校验，请等待导入完成或重试失败的导入 Job")
    if row["stored_path"] != row["original_video_path"] or digest(json.loads(row["evidence_json"])) != row["evidence_sha256"]:
        raise MaterialError("任务原片与导入证据不一致，不能继续处理")


def require_task_source(task_id):
    with get_connection() as c:
        require_imported_source(c, task_id)
        if task_item(c, task_id):
            lease = job_service.require_active_job_lease()
            if not lease or not c.execute("SELECT 1 FROM workflow_jobs WHERE id=? AND task_id=?", (lease[0], task_id)).fetchone():
                raise MaterialError("批次素材请使用转写、AI 或切片队列处理，不通过旧同步入口执行")


def frozen_job_payload(connection, task_id, job_type, payload):
    item = task_item(connection, task_id)
    if not item:
        return None
    if job_type == "auto_pipeline":
        raise MaterialError("批量自动生产将在人工审核门槛接入后启用；当前请逐步处理")
    generation = item["generation"]
    if payload.get("provider") and payload["provider"] != generation["snapshot"]["provider"]:
        raise MaterialError("AI Provider 与批次冻结配置不同，请使用原配置或另建生产任务")
    return {**payload, profiles.JOB_SNAPSHOT_KEY: generation}


def create_batch(payload: MaterialBatchCreate):
    if not payload.confirmed:
        raise MaterialError("请核对素材和批次配置后确认创建任务", 400)
    request = payload.model_dump(mode="json")
    request["material_ids"] = sorted(request["material_ids"])
    request_hash = digest(request)
    with get_connection() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute("SELECT id,request_sha256 FROM material_batches WHERE request_key=?", (str(payload.request_key),)).fetchone()
        if existing:
            if existing["request_sha256"] != request_hash:
                raise MaterialError("该请求编号已确认另一份批次，不能更换素材或配置")
            return _get_batch(c, existing["id"], reused=True)
        materials = [_read_material(c, mid) for mid in request["material_ids"]]
        materials.sort(key=lambda material: (material["file_name"].casefold(), material["id"]))
        for material in materials:
            validate_source(material["source"])
            if not payload.create_new_production:
                previous = c.execute("SELECT task_id FROM material_batch_items WHERE material_id=? LIMIT 1", (material["id"],)).fetchone()
                if previous:
                    raise MaterialError(f"素材 {material['file_name']} 已有生产任务 {previous[0]}；若需再次生产，请明确选择创建新生产任务")
        batch_id, now = uuid4().hex, datetime.now(timezone.utc).isoformat()
        config = request["settings"]
        c.execute("INSERT INTO material_batches(id,request_key,request_sha256,config_json,config_sha256,created_at) VALUES(?,?,?,?,?,?)",
                  (batch_id, str(payload.request_key), request_hash, canonical(config), digest(config), now))
        for material in materials:
            task_id, item_id = uuid4().hex[:12], uuid4().hex
            task_payload = payload.settings.task_payload(Path(material["file_name"]).stem)
            # No file creation in this transaction. The Job allocates its owned directory.
            insert_task_record_with_connection(c, task_payload, task_id=task_id, task_dir_name=f"batch-{task_id}")
            generation = profiles.freeze_new_job_payload(c, task_id, "ai_analysis", {})[profiles.JOB_SNAPSHOT_KEY]
            job_id, created = job_service.create_or_get_active_job_with_connection(c, task_id=task_id,
                job_type=job_service.JOB_TYPE_MATERIAL_IMPORT,
                payload={"batch_id": batch_id, "item_id": item_id, "material_id": material["id"],
                         "source_key": material["source_key"], "generation_sha256": digest(generation)})
            if not created:
                raise MaterialError("新任务已存在导入 Job，批次未提交")
            c.execute("INSERT INTO material_batch_items(id,batch_id,material_id,task_id,job_id,is_repeat,generation_json,generation_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (item_id, batch_id, material["id"], task_id, job_id, int(payload.create_new_production), canonical(generation), digest(generation), now))
        result = _get_batch(c, batch_id)
        c.commit()
        return result


def _get_batch(c, batch_id, *, reused=False):
    row = c.execute("SELECT * FROM material_batches WHERE id=?", (batch_id,)).fetchone()
    if not row:
        raise MaterialError("批次不存在", 404)
    batch = dict(row)
    config = json.loads(batch.pop("config_json"))
    if digest(config) != batch["config_sha256"]:
        raise MaterialError("批次配置证据不一致")
    items = c.execute("""SELECT i.id,i.material_id,i.task_id,i.job_id,m.file_name,
        j.status AS job_status,j.progress,j.message,j.error_message,t.status AS task_status,
        t.is_deleted,x.source_sha256,x.stored_path
        FROM material_batch_items i JOIN source_materials m ON m.id=i.material_id
        JOIN tasks t ON t.id=i.task_id JOIN workflow_jobs j ON j.id=i.job_id
        LEFT JOIN material_imports x ON x.item_id=i.id WHERE i.batch_id=? ORDER BY m.file_name,i.id""", (batch_id,)).fetchall()
    return {**batch, "config": config, "items": [dict(r) for r in items], "reused": reused}


def get_batch(batch_id):
    with get_connection() as c:
        return _get_batch(c, batch_id)


def list_batches(limit=20, offset=0):
    with get_connection() as c:
        ids = c.execute("SELECT id FROM material_batches ORDER BY created_at DESC,id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return {"batches": [_get_batch(c, r[0]) for r in ids], "total": c.execute("SELECT count(*) FROM material_batches").fetchone()[0]}
