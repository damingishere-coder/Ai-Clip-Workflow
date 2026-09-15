"""Explicit human consent for batch output versions, independent of AI feedback."""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from app.db.database import get_connection, ProductionReviewConflict
from app.db.production_review_migration import eligible_sql
from app.services import cut_evidence_service as cuts
from app.services.material_batch_service import task_item, require_imported_source


def _fail(message):
    raise ProductionReviewConflict(message)


def _idle(c, task_id):
    if c.execute("SELECT 1 FROM workflow_jobs WHERE task_id=? AND status IN ('queued','running')", (task_id,)).fetchone():
        _fail("任务仍在后台处理，请等待结束后确认成片")
    if c.execute("SELECT 1 FROM publish_jobs WHERE task_id=? AND status='PUBLISHING'", (task_id,)).fetchone():
        _fail("任务正在发布，请等待结果后再更改确认")


def prepared_jobs(c, task_id):
    return bool(c.execute("SELECT 1 FROM publish_jobs WHERE task_id=? AND status NOT IN ('PUBLISHED','EXPORTED','CANCELLED') LIMIT 1", (task_id,)).fetchone())


def manifest(c, task_id):
    if not task_item(c, task_id):
        return None
    require_imported_source(c, task_id)
    epoch = c.execute("SELECT revision FROM production_review_epochs WHERE task_id=?", (task_id,)).fetchone()
    run = c.execute("SELECT id,status FROM cut_runs WHERE task_id=? AND is_active=1 ORDER BY run_number DESC LIMIT 1", (task_id,)).fetchone()
    if not run or run["status"] != "completed":
        _fail("请先通过队列生成完整成片，再确认实际视频")
    receipt = cuts.read_evidence(c, run["id"])
    if not receipt:
        _fail("当前成片没有执行证据，请重新生成；不会推断历史确认")
    evidence = receipt["evidence"]
    if evidence["task_id"] != task_id or evidence["inputs"]["selection"] != cuts.selection_snapshot(c, task_id):
        _fail("分析版本或候选选择已变化，请重新生成成片")
    if cuts.source_stamp(evidence["inputs"]["source"]["path"]) != evidence["inputs"]["source"]:
        _fail("原片文件已变化，请重新校验并生成成片")
    expected = {r["clip_candidate_id"]: r for r in evidence["results"]}
    rows = c.execute("SELECT * FROM output_clip WHERE task_id=? AND is_active=1 ORDER BY id", (task_id,)).fetchall()
    if len(rows) != len(expected) or {r["clip_candidate_id"] for r in rows} != set(expected):
        _fail("当前成片与执行证据的选集不同")
    outputs = []
    for row in rows:
        result = expected[row["clip_candidate_id"]]
        if (row["cut_run_id"] != run["id"] or row["snapshot_source"] != "cut_plan_v1"
                or row["status"] != "completed" or result["status"] != "completed"
                or row["output_file_path"] != result["output_file_path"]
                or row["output_file_name"] != result["output_file_name"]
                or row["source_duration_ms"] != result["source_end_ms"] - result["source_start_ms"]
                or (row["source_start_ms"], row["source_end_ms"]) != (result["source_start_ms"], result["source_end_ms"])):
            _fail("成片记录与执行边界不一致，请重新生成")
        path = Path(row["output_file_path"])
        if not path.is_file() or path.stat().st_size <= 0:
            _fail("成片文件缺失或为空，请重新生成")
        outputs.append({"id": row["id"], "candidate_id": row["clip_candidate_id"],
                        "name": row["output_file_name"], "path": str(path),
                        "start_ms": row["source_start_ms"], "end_ms": row["source_end_ms"],
                        "file": cuts.source_stamp(path)})
    return {"schema": "production-review-v1", "task_id": task_id, "revision": epoch[0] if epoch else 0,
            "cut_run_id": run["id"], "cut_evidence_sha256": receipt["sha256"], "outputs": outputs}


def current_review(c, task_id):
    row = c.execute("SELECT * FROM production_reviews WHERE task_id=? ORDER BY rowid DESC LIMIT 1", (task_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    value = json.loads(result["manifest_json"])
    if cuts.digest(value) != result["manifest_sha256"] or value.get("task_id") != task_id:
        _fail("人工确认记录校验失败")
    return result


def require_cut_review(c, task_id):
    current = manifest(c, task_id)
    if current is None:
        return None
    review = current_review(c, task_id)
    if not review or cuts.digest(current) != review["manifest_sha256"] or current["revision"] != review["revision"]:
        _fail("请先查看并人工确认当前实际成片；旧确认不能批准新的版本")
    return review


def _delivery(c, task_id, output_id, mode):
    if mode == "original":
        row = c.execute("SELECT output_file_path AS path FROM output_clip WHERE id=? AND task_id=?", (output_id, task_id)).fetchone()
    else:
        row = c.execute("""SELECT sj.output_file_path AS path FROM subtitle_jobs sj
            JOIN subtitle_tracks st ON st.task_id=sj.task_id AND st.output_clip_id=sj.output_clip_id
              AND st.is_active=1 AND st.active_revision_id=sj.revision_id
            JOIN subtitle_revisions sr ON sr.id=sj.revision_id AND sr.track_id=st.id AND sr.status='approved'
            WHERE sj.task_id=? AND sj.output_clip_id=? AND sj.is_active=1
              AND sj.status='completed' AND sj.validation_status='verified' ORDER BY sj.updated_at DESC LIMIT 1""", (task_id, output_id)).fetchone()
    if not row or not Path(row["path"] or "").is_file():
        _fail("请先完成当前成片的字幕审核和验证")
    return row["path"]


def require_ready(c, task_id, output_id=None, mode=None, path=None):
    review = require_cut_review(c, task_id)
    if review is None:
        return None
    value = json.loads(review["manifest_json"])
    outputs = [o for o in value["outputs"] if not output_id or o["id"] == output_id]
    if not outputs:
        _fail("发布切片不属于当前人工确认的成片")
    mode = mode or review["delivery_mode"]
    for output in outputs:
        resolved_path = path or _delivery(c, task_id, output["id"], mode)
        query = "SELECT " + eligible_sql(":task", ":output", ":mode", ":path")
        if not c.execute(query, {"task": task_id, "output": output["id"], "mode": mode, "path": resolved_path}).fetchone()[0]:
            _fail("成片或字幕尚未人工确认，或确认版本已失效")
    return review


def check_preparation(task_id, output_id=None, mode=None):
    if not task_id:
        return None
    with get_connection() as c:
        return require_ready(c, task_id, output_id, mode)


def readiness_issue(job):
    try:
        with get_connection() as c:
            require_ready(c, job.get("task_id"), job.get("output_clip_id"), job.get("video_source") or "original", job.get("video_file_path"))
    except (ValueError, OSError) as exc:
        return str(exc)
    return None


def state(task_id):
    with get_connection() as c:
        if not task_item(c, task_id):
            return {"required": False}
        policy = {}
        try:
            mode, source = delivery_policy(c, task_id)
            policy = {"configured_delivery_mode": mode, "delivery_policy_source": source,
                      "suggested_delivery_mode": mode}
            _idle(c, task_id)
            value = manifest(c, task_id)
            review = current_review(c, task_id)
            approved = bool(review and review["manifest_sha256"] == cuts.digest(value))
            ready, message = False, "请逐条查看成片后确认"
            if approved:
                message = "成片已确认，等待字幕审核"
                try:
                    require_ready(c, task_id)
                    ready, message = True, "成片已确认，可进入内容准备"
                except (ValueError, OSError) as exc:
                    message = str(exc)
            if prepared_jobs(c, task_id):
                message += "；重新确认成片前，请先在发送中心取消或处理已有发布任务"
            return {**policy, "required": True, "can_confirm": not prepared_jobs(c, task_id), "approved": approved, "ready": ready,
                    "message": message, "manifest_sha256": cuts.digest(value),
                    "cut_run_id": value["cut_run_id"], "revision": value["revision"],
                    "delivery_mode": review["delivery_mode"] if approved else None,
                    "outputs": [{**o, "media_url": f"/media/tasks/{task_id}/output-clips/{o['id']}"} for o in value["outputs"]]}
        except (ValueError, OSError) as exc:
            processing = bool(c.execute("SELECT 1 FROM workflow_jobs WHERE task_id=? AND status IN ('queued','running')", (task_id,)).fetchone())
            return {**policy, "required": True, "can_confirm": False, "approved": False, "ready": False,
                    "processing": processing, "message": str(exc), "outputs": []}


def delivery_policy(c, task_id):
    """Use frozen creation settings; preserve explicit historical human decisions."""
    from app.services.batch_pipeline_service import configuration
    config = configuration(c, task_id)
    if config is None or config.get("subtitle_strategy") not in {"original", "review"}:
        _fail("任务字幕配置缺失，请先核对创建记录")
    mode = "subtitled" if config["subtitle_strategy"] == "review" else "original"
    previous = current_review(c, task_id)
    if previous and previous["delivery_mode"] != mode:
        return previous["delivery_mode"], "previous_review"
    return mode, "creation"


def confirm(task_id, payload):
    if not payload.confirmed:
        _fail("请查看实际成片并明确确认")
    request = {"task_id": task_id, **payload.model_dump(mode="json")}
    request_sha = cuts.digest(request)
    with get_connection() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute("SELECT id,request_sha256 FROM production_reviews WHERE request_key=?", (str(payload.request_key),)).fetchone()
        if existing:
            if existing["request_sha256"] != request_sha:
                _fail("该确认请求已用于另一份内容，不能覆盖")
            return {"review_id": existing["id"], "reused": True}
        _idle(c, task_id)
        if prepared_jobs(c, task_id):
            _fail("请先在发送中心取消或处理已有发布任务，再重新确认成片")
        value = manifest(c, task_id)
        if value is None:
            _fail("此入口仅适用于批次生产任务，旧任务继续使用原审核流程")
        if cuts.digest(value) != payload.manifest_sha256:
            _fail("预览后成片版本已变化，请刷新并重新核对")
        mode, _ = delivery_policy(c, task_id)
        if payload.delivery_mode is not None and payload.delivery_mode != mode:
            _fail("字幕方式已在创建任务时确定，审片确认不能更改；请刷新后重试")
        now, review_id = datetime.now(timezone.utc).isoformat(), uuid4().hex
        c.execute("INSERT INTO production_review_epochs(task_id,revision) VALUES(?,?) ON CONFLICT(task_id) DO NOTHING", (task_id, value["revision"]))
        c.execute("""INSERT INTO production_reviews(id,task_id,cut_run_id,revision,request_key,request_sha256,
            manifest_json,manifest_sha256,delivery_mode,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,'human_confirmation',?)""",
            (review_id, task_id, value["cut_run_id"], value["revision"], str(payload.request_key), request_sha,
             cuts.canonical(value), cuts.digest(value), mode, now))
        task = c.execute("SELECT auto_config_json FROM tasks WHERE id=?", (task_id,)).fetchone()
        config = json.loads(task["auto_config_json"] or "{}")
        config.update(subtitle_delivery_mode=mode, subtitle_decided_at=now)
        c.execute("UPDATE tasks SET auto_config_json=?,status=?,updated_at=? WHERE id=?",
                  (json.dumps(config, ensure_ascii=False), "PENDING_SUBTITLE_REVIEW" if mode == "subtitled" else "completed", now, task_id))
        c.commit()
    return {"review_id": review_id, "reused": False}


def prepare_subtitles(task_id):
    with get_connection() as c:
        review = require_cut_review(c, task_id)
        if not review or review["delivery_mode"] != "subtitled":
            _fail("请先确认成片；只有创建时选择新增字幕的任务需要准备字幕")
        _idle(c, task_id)
    from app.services.subtitle_auto_workflow_service import prepare_task_subtitle_review
    return prepare_task_subtitle_review(task_id)


def subtitle_review(task_id, payload=None):
    with get_connection() as c:
        review = require_cut_review(c, task_id)
    if review:
        if review["delivery_mode"] != "subtitled":
            _fail("当前任务使用原视频画面，无需新增字幕")
        if payload is not None and (payload.get("production_review_id") != review["id"]
                or payload.get("production_review_sha256") != review["manifest_sha256"]):
            _fail("字幕任务绑定的成片确认已失效，请重新审核")
    return review


def complete_subtitle_status(task_id, payload):
    with get_connection() as c:
        c.execute("BEGIN IMMEDIATE")
        if not task_item(c, task_id):
            return
        try:
            review = require_ready(c, task_id)
        except (ValueError, OSError):
            return  # Partial/changed subtitles stay in the review Inbox.
        if review and review["id"] == payload.get("production_review_id"):
            c.execute("UPDATE tasks SET status='completed',updated_at=? WHERE id=? AND status='PENDING_SUBTITLE_REVIEW'",
                      (datetime.now(timezone.utc).isoformat(), task_id))
            c.commit()
