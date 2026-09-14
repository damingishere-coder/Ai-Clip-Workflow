"""Run-cohort human review statistics. No AI, production rule, or queue writes."""

from collections import Counter, defaultdict
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
import re
from uuid import uuid4

from app.db.database import get_connection
from app.models.human_review import ObservationDecision
from app.services.review_observation_service import read_observations


def _date(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Legacy task timestamps used UTC without an offset.
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _load_run(connection, task_id, run_id):
    row = connection.execute("""SELECT r.*, v.profile_id FROM ai_analysis_runs r
        JOIN tasks t ON t.id=r.task_id
        LEFT JOIN content_profile_versions v ON v.id=r.content_profile_version_id
        WHERE r.task_id=? AND r.id=? AND t.is_deleted=0""", (task_id, run_id)).fetchone()
    if row is None:
        raise ValueError("分析批次不存在或任务已删除")
    return _run_data(dict(row))


def _run_data(run):
    try:
        payload = json.loads(run.get("analysis_payload_json") or "{}")
        if not isinstance(payload, dict):
            payload = {}
        observations, basis = read_observations(payload)
    except (ValueError, TypeError, AttributeError):
        payload, observations, basis = {}, [], "invalid_payload"
    meta = payload.get("analysis_meta") or {}
    meta = meta if isinstance(meta, dict) else {}
    source_identity = meta.get("review_source_identity") or {}
    run.update(observations=observations, evidence_basis=basis,
               profile=run.get("profile_id") or meta.get("selection_profile") or "unknown",
               incomplete=bool(meta.get("analysis_incomplete") or meta.get("quality_degraded") or basis.startswith("invalid")),
               source_identity=source_identity if isinstance(source_identity, dict) else {})
    return run


def _feedback_for_run(connection, run_id):
    return [dict(r) for r in connection.execute(
        "SELECT rowid AS event_order,* FROM clip_feedback WHERE analysis_run_id=? ORDER BY created_at,rowid", (run_id,))]


def _decisions(run, feedback, cutoff):
    """An unknown latest explicit event cannot resurrect an earlier approval."""
    rows = {r["key"]: {**r, "human_decision": None, "decision_reason": None, "ambiguous": False} for r in run["observations"]}
    candidates = defaultdict(list)
    for row in rows.values():
        if row.get("clip_key"):
            candidates[f"{run['task_id']}_{row['clip_key']}"[:120]].append(row["key"])
    unmatched = 0
    ordered = sorted(feedback, key=lambda f: (_date(f["created_at"]) or datetime.min.replace(tzinfo=timezone.utc), f["event_order"]))
    for event in ordered:
        when = _date(event["created_at"])
        if event["task_id"] != run["task_id"] or not when or when > cutoff:
            continue
        key = event.get("analysis_candidate_key")
        if not key:
            keys = candidates.get(event["clip_candidate_id"], [])
            key = keys[0] if len(keys) == 1 else None
        if key not in rows:
            unmatched += 1
            continue
        row = rows[key]
        source = event["decision_source"]
        if source not in {"explicit_feedback", "observation_review"}:
            if row["human_decision"] is None:
                row["ambiguous"] = True
            continue
        matches = event["decision"] in {"keep", "reject"}
        if source == "observation_review":
            matches = matches and event.get("observation_sha256") == row["sha256"]
        else:
            # Old explicit endpoints judged the mutable clip. Only exact initial
            # bounds and copy can establish that they judged this AI proposal.
            matches = matches and all(str(event.get(field) or "") == str(row.get(target) or "")
                for field, target in (("title_snapshot", "title"), ("summary_snapshot", "summary"),
                                      ("start_time", "start_time"), ("end_time", "end_time")))
        row.update(human_decision=event["decision"] if matches else None,
                   decision_reason=event["reason_code"] if matches else None,
                   decision_id=event["id"],
                   ambiguous=not matches, decision_at=event["created_at"], decision_source=source)
    return list(rows.values()), unmatched


def get_review_observations(task_id, run_id):
    with get_connection() as connection:
        connection.execute("BEGIN")
        run = _load_run(connection, task_id, run_id)
        rows, unmatched = _decisions(run, _feedback_for_run(connection, run_id), datetime.now(timezone.utc))
    return {"run_id": run_id, "run_number": run["run_number"], "profile": run["profile"],
            "profile_version_id": run.get("content_profile_version_id"), "prompt_version_id": run.get("prompt_version_id"),
            "evidence_basis": run["evidence_basis"], "analysis_incomplete": run["incomplete"],
            "observations": rows, "unmatched_feedback": unmatched}


def list_review_runs(task_id):
    with get_connection() as connection:
        runs = [dict(r) for r in connection.execute("""SELECT r.id,r.run_number,r.created_at,r.is_active
            FROM ai_analysis_runs r JOIN tasks t ON t.id=r.task_id WHERE r.task_id=? AND t.is_deleted=0
            ORDER BY r.is_active DESC,r.run_number DESC LIMIT 100""", (task_id,))]
    return {"runs": runs, "limit": 100}


def save_observation_decision(task_id, run_id, payload: ObservationDecision):
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        run = _load_run(connection, task_id, run_id)
        row = next((r for r in run["observations"] if r["key"] == payload.observation_key), None)
        if row is None or row["sha256"] != payload.observation_sha256:
            raise ValueError("候选快照已变化或无法验证，请刷新证据后重新判断")
        latest = connection.execute("""SELECT * FROM clip_feedback WHERE task_id=? AND analysis_run_id=?
            AND (analysis_candidate_key=? OR (analysis_candidate_key IS NULL AND clip_candidate_id=?))
            AND decision_source IN ('observation_review','explicit_feedback') ORDER BY created_at DESC,rowid DESC LIMIT 1""",
            (task_id, run_id, row["key"], f"{task_id}_{row['clip_key']}"[:120] if row["clip_key"] else "")).fetchone()
        if latest and latest["decision_source"] == "observation_review" and all(latest[field] == value for field, value in (
            ("observation_sha256", row["sha256"]), ("decision", payload.decision),
            ("reason_code", payload.reason_code), ("note", payload.note.strip()))):
            return {"status": "ok", "id": latest["id"], "deduplicated": True}
        feedback_id = uuid4().hex[:12]
        # Diagnostic records deliberately have no production candidate. The old
        # column has no candidate FK; its opaque ID must not name another Run.
        candidate_id = f"{task_id}_{row['clip_key']}"[:120] if row["clip_key"] else f"observation:{run_id}:{row['sha256']}"
        connection.execute("""INSERT INTO clip_feedback(id,task_id,clip_candidate_id,analysis_run_id,selection_profile,
            decision,reason_code,decision_source,note,title_snapshot,summary_snapshot,start_time,end_time,created_at,
            analysis_candidate_key,observation_sha256) VALUES(?,?,?,?,?,?,?,'observation_review',?,?,?,?,?,?,?,?)""",
            (feedback_id, task_id, candidate_id, run_id, run["profile"], payload.decision, payload.reason_code,
             payload.note.strip(), row["title"], row["summary"], row["start_time"], row["end_time"], now, row["key"], row["sha256"]))
        connection.commit()
    return {"status": "ok", "id": feedback_id, "deduplicated": False}


def _summary(rows, *, truncated=False):
    reviewed = [r for r in rows if r["human_decision"]]
    recommended = [r for r in rows if r["initial_recommended"] is True]
    reviewed_recommended = [r for r in recommended if r["human_decision"]]
    known_reviewed = [r for r in reviewed if r.get("source_sha256") and not r["incomplete"]]
    identities = {r["source_sha256"] for r in known_reviewed}
    profiles = {r["profile"] for r in known_reviewed}
    # A confidence label applies to the displayed population, not a cleaner
    # hidden subset. Mixed incomplete/unknown-source rows remain descriptive.
    trusted = not truncated and len(known_reviewed) == len(reviewed) and len(known_reviewed) >= 20 and len(identities) >= 3 and len(profiles) == 1 and "unknown" not in profiles
    recommendation_trusted = trusted and len(reviewed_recommended) >= 20 and len({r["source_sha256"] for r in reviewed_recommended}) >= 3
    return {"candidate_count": len(rows), "review_pool_count": sum(r["in_review_pool"] for r in rows),
            "diagnostic_count": sum(not r["in_review_pool"] for r in rows),
            "accepted": sum(r["human_decision"] == "keep" for r in reviewed),
            "rejected": sum(r["human_decision"] == "reject" for r in reviewed),
            "reviewed": len(reviewed), "unreviewed": len(rows) - len(reviewed),
            "manual_acceptance_rate": round(sum(r["human_decision"] == "keep" for r in reviewed) / len(reviewed), 4) if reviewed else None,
            "manual_rejection_rate": round(sum(r["human_decision"] == "reject" for r in reviewed) / len(reviewed), 4) if reviewed else None,
            "all_candidate_review_coverage": round(len(reviewed) / len(rows), 4) if rows else None,
            "ambiguous": sum(r["ambiguous"] for r in rows), "recommended": len(recommended),
            "recommendation_unknown": sum(r["initial_recommended"] is None for r in rows),
            "reviewed_recommended": len(reviewed_recommended),
            "recommendation_acceptance_rate": round(sum(r["human_decision"] == "keep" for r in reviewed_recommended) / len(reviewed_recommended), 4) if reviewed_recommended else None,
            "review_coverage": round(len(reviewed_recommended) / len(recommended), 4) if recommended else None,
            "task_count": len({r["task_id"] for r in rows}), "run_count": len({r["run_id"] for r in rows}),
            "verified_original_count": len(identities), "source_unknown_decisions": sum(not r.get("source_sha256") for r in reviewed),
            "incomplete_analysis_decisions": sum(r["incomplete"] for r in reviewed),
            "confidence": "exploratory" if trusted else "insufficient",
            "confidence_label": "探索性" if trusted else "数据不足",
            "recommendation_confidence_label": "探索性" if recommendation_trusted else "数据不足",
            "threshold": {"valid_decisions": 20, "independent_originals": 3},
            "rejection_reasons": dict(Counter(r["decision_reason"] for r in reviewed if r["human_decision"] == "reject"))}


def get_human_review_summary(days=30, *, cutoff=None, connection=None, include_evidence=False):
    cutoff = _date(cutoff) if cutoff else datetime.now(timezone.utc)
    if cutoff is None or not 1 <= days <= 180:
        raise ValueError("审片统计时间范围无效")
    start = cutoff - timedelta(days=days)
    with (get_connection() if connection is None else nullcontext(connection)) as connection:
        if not connection.in_transaction:
            connection.execute("BEGIN")
        # Cohort = analyses generated in this interval, decisions as of cutoff.
        # Explicitly bounded, with truncation reported instead of a false total.
        runs = [dict(r) for r in connection.execute("""SELECT r.*,v.profile_id FROM ai_analysis_runs r
            JOIN tasks t ON t.id=r.task_id LEFT JOIN content_profile_versions v ON v.id=r.content_profile_version_id
            WHERE t.is_deleted=0 AND julianday(r.created_at)>=julianday(?) AND julianday(r.created_at)<=julianday(?)
            ORDER BY r.created_at DESC,r.id LIMIT 2001""", (start.isoformat(), cutoff.isoformat()))]
        truncated = len(runs) > 2000
        runs = runs[:2000]
        events = defaultdict(list)
        # Two bounded reads, not one query per candidate or Run.
        event_rows = connection.execute("""SELECT f.rowid AS event_order,f.* FROM clip_feedback f
            JOIN ai_analysis_runs r ON r.id=f.analysis_run_id JOIN tasks t ON t.id=r.task_id
            WHERE t.is_deleted=0 AND julianday(r.created_at)>=julianday(?) AND julianday(r.created_at)<=julianday(?)
            AND julianday(f.created_at)<=julianday(?) ORDER BY f.created_at DESC,f.rowid DESC LIMIT 200001""",
            (start.isoformat(), cutoff.isoformat(), cutoff.isoformat())).fetchall()
        truncated = truncated or len(event_rows) > 200000
        for event in event_rows[:200000]:
            events[event["analysis_run_id"]].append(dict(event))
        unattributed = connection.execute("""SELECT COUNT(*) FROM clip_feedback f
            JOIN tasks t ON t.id=f.task_id WHERE t.is_deleted=0
            AND julianday(f.created_at)>=julianday(?) AND julianday(f.created_at)<=julianday(?)
            AND NOT EXISTS(SELECT 1 FROM ai_analysis_runs r WHERE r.id=f.analysis_run_id AND r.task_id=f.task_id)""",
            (start.isoformat(), cutoff.isoformat())).fetchone()[0]
    all_rows, unmatched, invalid_runs = [], 0, 0
    for raw in runs:
        run = _run_data(raw)
        invalid_runs += run["evidence_basis"].startswith("invalid")
        rows, missing = _decisions(run, events[run["id"]], cutoff)
        unmatched += missing
        source = run["source_identity"]
        sha = source.get("sha256") if source.get("kind") == "original_video_sha256" else None
        sha = sha if isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha) else None
        for row in rows:
            row.update(task_id=run["task_id"], run_id=run["id"], profile=run["profile"],
                       source_sha256=sha, incomplete=run["incomplete"],
                       profile_version_id=run.get("content_profile_version_id"), prompt_version_id=run.get("prompt_version_id"))
            all_rows.append(row)
    groups = {}
    for dimension in ("profile", "quality_tier", "score_band"):
        grouped = defaultdict(list)
        for row in all_rows:
            score = row["quality_score"]
            key = ("unknown" if score is None else "0–64" if score < 65 else "65–77" if score < 78 else "78–89" if score < 90 else "90–100") if dimension == "score_band" else row.get(dimension) or "unknown"
            grouped[str(key)].append(row)
        groups[dimension] = [{"key": key, **_summary(values, truncated=truncated)} for key, values in sorted(grouped.items())]
    result = {"schema_version": "human-review-v1", "time_basis": "analysis_created_at", "start": start.isoformat(),
            "cutoff": cutoff.isoformat(), "days": days, "truncated": truncated,
            "unmatched_feedback": unmatched, "recent_unattributed_feedback": unattributed, "invalid_runs": invalid_runs,
            "profile_version_unknown": sum(not r["profile_version_id"] for r in all_rows),
            "prompt_version_unknown": sum(not r["prompt_version_id"] for r in all_rows),
            "summary": _summary(all_rows, truncated=truncated), "groups": groups,
            "note": "统计这段时间内生成的 AI 批次，截至统计时间的最后明确评价；默认启用、自动选片和普通保存均不算人工认可。重复分析按不同推荐尝试计数。原视频身份未知时不推算独立样本数。仅描述关联，不作因果结论。"}
    if include_evidence:
        fields = ("task_id", "run_id", "key", "sha256", "human_decision", "decision_id", "decision_at",
                  "decision_source", "ambiguous", "profile", "profile_version_id", "prompt_version_id", "source_sha256")
        result["evidence_refs"] = [{key: row.get(key) for key in fields} for row in all_rows]
    return result
