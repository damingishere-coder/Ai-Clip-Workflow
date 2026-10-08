"""Read-only user stages. These projections never grant production permission."""
from collections import Counter, defaultdict
import json
from pathlib import Path

from app.db.database import get_connection


STAGES = {
    "waiting": "待继续制作", "queued": "排队中", "processing": "制作中",
    "review": "待审片", "subtitles": "待字幕", "prepare": "待内容准备",
    "scheduled": "已排期", "publishing": "发送中", "published": "已发布", "complete": "制作完成",
    "error": "制作异常", "publish_review": "发布需处理",
}


def _file_exists(value):
    try:
        return bool(value and Path(value).is_file())
    except (OSError, ValueError):
        return False


def subtitle_outputs(connection, task_ids=None):
    """Only the current track/revision can establish subtitle completion."""
    params = list(task_ids or [])
    condition = ""
    if task_ids is not None:
        if not params:
            return {}
        condition = f" AND o.task_id IN ({','.join('?' for _ in params)})"
    rows = connection.execute("""
        SELECT o.*, st.id AS subtitle_track_id, st.active_revision_id,
          sr.status AS subtitle_revision_status, sj.id AS subtitle_job_id,
          sj.status AS subtitle_status, sj.validation_status AS subtitle_validation_status,
          sj.output_file_path AS subtitled_output_file_path
        FROM output_clip o
        LEFT JOIN subtitle_tracks st ON st.task_id=o.task_id AND st.output_clip_id=o.id AND st.is_active=1
        LEFT JOIN subtitle_revisions sr ON sr.id=st.active_revision_id AND sr.track_id=st.id
        LEFT JOIN subtitle_jobs sj ON sj.task_id=o.task_id AND sj.output_clip_id=o.id
          AND sj.revision_id=st.active_revision_id AND sj.is_active=1
        WHERE o.is_active=1""" + condition + " ORDER BY o.id", params).fetchall()
    grouped = defaultdict(list)
    for record in rows:
        output = dict(record)
        output["file_exists"] = _file_exists(output.get("output_file_path"))
        output["subtitle_publish_ready"] = bool(
            output.get("subtitle_track_id")
            and output.get("subtitle_revision_status") == "approved"
            and output.get("subtitle_status") == "completed"
            and output.get("subtitle_validation_status") == "verified"
            and _file_exists(output.get("subtitled_output_file_path"))
        )
        grouped[output["task_id"]].append(output)
    return dict(grouped)


def load_task_rows(connection, task_ids=None):
    params = list(task_ids or [])
    condition = ""
    if task_ids is not None:
        if not params:
            return []
        condition = f" AND t.id IN ({','.join('?' for _ in params)})"
    return [dict(row) for row in connection.execute("""
        SELECT t.*, t.task_name AS title, b.id AS batch_item, b.batch_id
        FROM tasks t LEFT JOIN material_batch_items b ON b.task_id=t.id
        WHERE COALESCE(t.is_deleted,0)=0""" + condition, params)]


def _project(connection, rows):
    from app.services import production_review_service as reviews
    from app.services import cut_evidence_service as cuts

    ids = [row["id"] for row in rows]
    if not ids:
        return {}
    slots = ",".join("?" for _ in ids)
    outputs = subtitle_outputs(connection, ids)
    candidates = {row["task_id"]: row["count"] for row in connection.execute(
        f"SELECT task_id,count(*) count FROM clip_candidates WHERE is_deleted=0 AND task_id IN ({slots}) GROUP BY task_id", ids)}
    jobs = defaultdict(list)
    for job in connection.execute(
        f"SELECT rowid,* FROM workflow_jobs WHERE task_id IN ({slots}) ORDER BY rowid DESC", ids):
        jobs[job["task_id"]].append(dict(job))
    published = defaultdict(list)
    for job in connection.execute(f"""
        SELECT p.* FROM publish_jobs p JOIN output_clip o ON o.id=p.output_clip_id AND o.task_id=p.task_id
        WHERE o.is_active=1 AND p.platform='douyin' AND p.task_id IN ({slots})
        ORDER BY p.created_at DESC,p.updated_at DESC,p.id DESC""", ids):
        published[job["task_id"]].append(dict(job))
    result = {}
    for row in rows:
        task_id = row["id"]
        status = str(row.get("status") or "").lower()
        task_outputs = [o for o in outputs.get(task_id, []) if o["status"] == "completed"]
        pending_subtitles = [o for o in task_outputs if not o["subtitle_publish_ready"]]
        # External delivery has its own status and must not replace production.
        task_jobs = [j for j in jobs[task_id] if j['job_type'] != 'publish']
        active = next((j for j in task_jobs if j["status"] == "running"), None)
        active = active or next((j for j in task_jobs if j["status"] == "queued"), None)
        latest = task_jobs[0] if task_jobs else {}
        try:
            config = json.loads(row.get("auto_config_json") or "{}")
            config = config if isinstance(config, dict) else {}
        except (ValueError, TypeError):
            config = {}
        modern = bool(row.get("batch_item") or config.get("subtitle_strategy") in {"original", "review"})
        mode = "legacy"
        approved = False
        blocked = ""
        if modern:
            try:
                mode, _ = reviews.delivery_policy(connection, task_id)
                if task_outputs and not active:
                    receipt = reviews.current_review(connection, task_id)
                    value = reviews.manifest(connection, task_id)
                    approved = bool(receipt and receipt["revision"] == value["revision"]
                                    and receipt["manifest_sha256"] == cuts.digest(value))
            except (ValueError, OSError, TypeError, KeyError) as exc:
                blocked = str(exc)
                mode = "subtitled" if config.get("subtitle_strategy") == "review" else "original"
        elif status == "pending_subtitle_review" or any(o.get("subtitle_track_id") for o in task_outputs):
            mode = "legacy_subtitled"
        # Preserve a successful historical delivery when a newer draft was cancelled.
        latest_publications = {}
        for job in published[task_id]:
            key = (job["output_clip_id"], job["platform"])
            previous = latest_publications.get(key)
            if previous is None or (previous["status"] == "CANCELLED" and job["status"] in {"PUBLISHED", "EXPORTED"}):
                latest_publications[key] = job
        publications = list(latest_publications.values())
        pub_counts = Counter(j["status"] for j in publications)
        unprepared = sum(not any(j["output_clip_id"] == o["id"] and j["status"] != "CANCELLED"
                                for j in publications) for o in task_outputs)
        progress = max(0, min(99, int(row.get("progress") or 0)))
        production_active = active and active['job_type'] in {'material_import', 'transcript', 'ai_analysis', 'video_cut', 'auto_pipeline'}
        raw_processing = status in {'transcribing', 'ai_analyzing', 'cutting', 'audio_extracting', 'preparing_source', 'clip_selecting', 'video_cutting'}
        if production_active:
            progress = max(0, min(99, int(active.get('progress') or 0)))
        elif not raw_processing and (task_outputs or status in {"completed", "ready_to_publish"}):
            progress = 100
        stage = "waiting"
        tone = "blue"
        action = {"label": "继续制作", "url": f"/tasks/{task_id}", "action": "detail"}
        category = None
        count = 1
        message = "素材已就绪，可继续转写或选片。"
        if active:
            stage = "processing" if active["status"] == "running" else "queued"
            action["label"] = "查看进度"
            message = active.get("message") or STAGES[stage]
        elif status.startswith("failed") or status in {"completed_with_errors", "failed"} or latest.get("status") == "failed":
            stage, category, tone = "error", "errors", "red"
            message = latest.get("error_message") or row.get("error_message") or row.get("last_error") or "查看失败步骤，确认后重试。"
            action["label"] = "处理异常"
            if latest.get("job_type") == "material_import" and row.get("batch_id"):
                action["url"] = f"/materials?batch={row['batch_id']}"
        elif task_outputs and modern and not approved:
            stage, category, count = "review", "clips", len(task_outputs)
            message = blocked or "检查当前实际成片，明确确认后继续。"
            action = {"label": "检查成片", "url": f"/tasks/{task_id}/clips/review#production-review", "action": "review_outputs"}
        elif mode in {"subtitled", "legacy_subtitled"} and pending_subtitles and (approved or not modern):
            stage, category, count = "subtitles", "subtitles", len(pending_subtitles)
            message = "审核当前字幕版本，并完成渲染验证。"
            action = {"label": "处理字幕", "url": f"/subtitles/{task_id}", "action": "subtitle_review"}
        elif task_outputs and unprepared and (approved or not modern):
            stage, category, count = "prepare", "prepare", unprepared
            message = "成片已就绪，请进入内容准备。"
            action = {"label": "准备发布", "url": f"/tasks/{task_id}/clips/review#production-review" if modern else f"/publish?task_id={task_id}&tab=content", "action": "review_outputs" if modern else "publish"}
        elif pub_counts["PUBLISHING"]:
            stage = "publishing"
        elif pub_counts["SCHEDULED"]:
            stage = "scheduled"
        elif pub_counts["DRAFT"] or pub_counts["WAITING"]:
            stage = "prepare"
        elif pub_counts['PUBLISHED']:
            stage, tone = "published", "green"
        elif publications or status in {"completed", "ready_to_publish"}:
            stage, tone = "complete", "green"
        elif candidates.get(task_id) and status == "pending_review":
            stage, category, count = "review", "clips", candidates[task_id]
            message = "检查候选和选择，再生成实际成片。"
            action = {"label": "审片与切片", "url": f"/tasks/{task_id}/clips/review", "action": "review_outputs"}
        elif status in {"transcribing", "ai_analyzing", "cutting", "audio_extracting", "preparing_source", "clip_selecting", "video_cutting"}:
            stage, action["label"] = "processing", "查看进度"
        elif status in {"pending_processing", "pending_ai", "pending_video", "created"}:
            category = "start"
        if stage in {"scheduled", "publishing", "published", "complete"} or (stage == "prepare" and category is None):
            action = {"label": "查看排期" if stage == "scheduled" else "查看发布", "url": f"/publish?task_id={task_id}&tab={'schedule' if stage == 'scheduled' else 'content'}", "action": "publish"}
            message = {'scheduled':f"已有 {pub_counts['SCHEDULED']} 条成片排期，可到发送中心查看计划。",
                'publishing':'制作已完成，发送任务正在执行，请在执行记录查看结果。',
                'published':f"已有 {pub_counts['PUBLISHED']} 条成片确认发送成功，其余记录可到发送中心查看。",
                'complete':'制作已完成，发布或导出结果在发送中心单独记录。',
                'prepare':f"已有 {pub_counts['DRAFT'] + pub_counts['WAITING']} 条发布内容待准备或安排发送。"}[stage]
        if action['action'] == 'detail' and action['url'].startswith('/tasks/'):
            action['url'] += '#analysis' if status == 'pending_ai' else '#production-controls'
        subtitle_count = len(pending_subtitles) if mode in {"subtitled", "legacy_subtitled"} and (approved or not modern) else 0
        names = ["素材就绪", "转写与选片", "生成成片", "人工确认"]
        completed_steps = 3 if progress == 100 else (2 if candidates.get(task_id) else 1)
        if approved or (not modern and progress == 100):
            completed_steps = 4
        steps = [{"index": str(i), "name": name, "state": "done" if i <= completed_steps else ("current" if i == completed_steps + 1 else "pending")}
                 for i, name in enumerate(names, 1)]
        if stage == "error":
            for step in steps:
                if step["state"] == "current":
                    step["state"] = "warning"
                    break
        label = STAGES[stage]
        publish_attention = pub_counts["FAILED"] + pub_counts["NEED_REVIEW"]
        if publish_attention and not active and stage not in {'processing', 'queued', 'review', 'subtitles', 'error'}:
            stage, label = "publish_review", "发布需处理"
            tone = "amber"
            job = next(j for j in publications if j["status"] in {"FAILED", "NEED_REVIEW"})
            action = {"label": "复核发布", "url": f"/publish?task_id={task_id}&job_id={job['id']}&tab=history", "action": "publish"}
            message = '发布记录需要人工复核，制作进度另行保留。'
        ui = dict(stage=stage, stage_label=label, tone=tone, production_progress=progress,
                  primary_action=action, subtitle_mode=mode, subtitle_pending_count=subtitle_count,
                  subtitle_done_count=sum(o["subtitle_publish_ready"] for o in task_outputs),
                  subtitle_eligible=mode in {"subtitled", "legacy_subtitled"} and (approved or not modern),
                  review_required=modern, review_approved=approved, blocked_reason=blocked,
                  candidates=candidates.get(task_id, 0), outputs=len(task_outputs),
                  publish_summary=dict(pub_counts), publish_attention_count=publish_attention, workflow_steps=steps,
                  category=category, count=count, message=message,
                  busy=bool(active), running=bool(active and active["status"] == "running"))
        result[task_id] = ui
    return result


def task_projections(task_ids=None, connection=None, rows=None):
    if connection is not None:
        return _project(connection, rows if rows is not None else load_task_rows(connection, task_ids))
    with get_connection() as connection:
        return _project(connection, load_task_rows(connection, task_ids))
