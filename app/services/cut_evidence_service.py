"""Input and actual CutPlan evidence, committed atomically with cut results."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from app.services.video_cut_service import parse_time_to_seconds, format_seconds_for_ffmpeg


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def planned_bounds(clip):
    start = parse_time_to_seconds(clip["start_time"])
    end = parse_time_to_seconds(clip["end_time"])
    if end <= start:
        raise ValueError("切片输入时间无效")
    start_ms = round(float(format_seconds_for_ffmpeg(start)) * 1000)
    duration_ms = round(float(format_seconds_for_ffmpeg(end-start)) * 1000)
    return start_ms, start_ms+duration_ms


def selection_snapshot(connection, task_id):
    task = connection.execute("SELECT original_video_path,is_deleted FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task or task["is_deleted"]:
        raise ValueError("任务不存在或已删除")
    runs = connection.execute("SELECT id,analysis_payload_json,provider,model,prompt_version_id,prompt_text_sha256,content_profile_version_id,content_profile_sha256,requested_clip_count FROM ai_analysis_runs WHERE task_id=? AND is_active=1 ORDER BY id", (task_id,)).fetchall()
    rows = connection.execute("""SELECT id,clip_key,title,start_time,end_time,source_analysis_run_id
        FROM clip_candidates WHERE task_id=? AND enabled=1 AND is_deleted=0 ORDER BY id""", (task_id,)).fetchall()
    return {"source_path": task["original_video_path"],
        "runs": [{**{k: r[k] for k in r.keys() if k != "analysis_payload_json"},
                  "payload_sha256": hashlib.sha256(r["analysis_payload_json"].encode()).hexdigest()} for r in runs],
        "clips": [{"id": r["id"], "clip_key": r["clip_key"], "title": r["title"],
                   "source_analysis_run_id": r["source_analysis_run_id"],
                   "start_ms": planned_bounds(r)[0], "end_ms": planned_bounds(r)[1]} for r in rows]}


def source_stamp(path):
    path = Path(path).resolve()
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns, "device": stat.st_dev, "inode": stat.st_ino}


def verify_and_commit(connection, task_id, cut_run_id, inputs, results, now):
    if selection_snapshot(connection, task_id) != inputs["selection"]:
        raise ValueError("切片期间分析版本、候选选择或边界已变化，请重新生成切片")
    if source_stamp(inputs["source"]["path"]) != inputs["source"]:
        raise ValueError("切片期间原片文件发生变化，请重新校验素材")
    planned = {c["id"]: c for c in inputs["selection"]["clips"]}
    if len(results) != len(planned) or {r.clip_candidate_id for r in results} != set(planned):
        raise ValueError("切片返回结果与冻结选集不一致")
    for result in results:
        if result.status not in {"completed", "failed"}:
            raise ValueError("切片返回了未知执行状态")
        if result.status == "completed":
            expected = planned[result.clip_candidate_id]
            if (result.source_start_ms, result.source_end_ms) != (expected["start_ms"], expected["end_ms"]):
                raise ValueError("成片缺少实际执行边界或与冻结切片计划不同")
    evidence = {"schema": "cut-execution-v1", "task_id": task_id, "cut_run_id": cut_run_id,
                "inputs": inputs, "results": [asdict(r) for r in results]}
    connection.execute("INSERT INTO cut_run_evidence(cut_run_id,task_id,evidence_json,evidence_sha256,created_at) VALUES(?,?,?,?,?)",
                       (cut_run_id, task_id, canonical(evidence), digest(evidence), now))


def read_evidence(connection, cut_run_id):
    row = connection.execute("SELECT * FROM cut_run_evidence WHERE cut_run_id=?", (cut_run_id,)).fetchone()
    if not row:
        return None  # Historical evidence stays unknown.
    evidence = json.loads(row["evidence_json"])
    if (not isinstance(evidence, dict) or evidence.get("schema") != "cut-execution-v1"
            or digest(evidence) != row["evidence_sha256"] or evidence.get("cut_run_id") != cut_run_id
            or evidence.get("task_id") != row["task_id"]):
        raise ValueError("切片执行证据校验失败")
    return {"sha256": row["evidence_sha256"], "evidence": evidence}
