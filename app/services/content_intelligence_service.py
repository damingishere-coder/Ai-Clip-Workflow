"""Deterministic, frozen observations. No model calls or production-policy writes."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import statistics
from uuid import uuid4

from app.db.database import get_connection
from app.services.content_profile_service import _version
from app.services.content_review_service import (
    BEIJING_TIMEZONE, ContentReviewError, DOUYIN_ITEM_EXPORT_SOURCE_KIND,
    MATCHED_STATUSES, _parse_iso_datetime,
)
from app.services.human_review_service import _run_data, get_human_review_summary
from app.services.review_observation_service import evidence_hash

VERSION = "content-intelligence-v2"
MAX_WORKS = 10000
METRICS = ("play_count", "five_second_completion_rate", "two_second_bounce_rate",
           "completion_rate", "average_watch_seconds", "watch_ratio", "like_count",
           "comment_count", "share_count", "collect_count", "follower_gain_count")
DIMENSIONS = ("duration_band", "profile", "topic", "hook_type", "score_band", "prompt_version_id", "publish_hour")


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def _band(seconds):
    if seconds is None:
        return "unknown"
    return "<60s" if seconds < 60 else "60–90s" if seconds <= 90 else "91–150s" if seconds <= 150 else ">150s"


def _clock_ms(value):
    try:
        hours, minutes, seconds = str(value).split(":")
        h, m, s = int(hours), int(minutes), float(seconds)
        if h < 0 or not 0 <= m < 60 or not 0 <= s < 60:
            return None
        return round((h * 3600 + m * 60 + s) * 1000)
    except (ValueError, TypeError, OverflowError):
        return None


def _official_rows(connection, account_id, cutoff):
    # Rank by official export identity BEFORE considering attribution. A newer
    # unmatched row invalidates an older match. The export key is not a real aweme ID.
    rows = [dict(r) for r in connection.execute("""WITH ranked AS (
        SELECT i.*, ROW_NUMBER() OVER(PARTITION BY i.aweme_id
            ORDER BY julianday(i.captured_at) DESC,i.created_at DESC,i.rowid DESC) AS rank
        FROM douyin_item_metric_snapshots i JOIN content_metric_import_batches b ON b.id=i.batch_id
        WHERE i.account_id=? AND b.account_id=i.account_id AND b.status='committed' AND b.source_kind=?
          AND julianday(i.captured_at)<=julianday(?) AND julianday(b.committed_at)<=julianday(?)
    ), works AS (
        SELECT *,ROW_NUMBER() OVER(PARTITION BY CASE WHEN publish_job_id IS NOT NULL THEN 'job:'||publish_job_id ELSE 'work:'||aweme_id END
            ORDER BY julianday(captured_at) DESC,created_at DESC,id DESC) AS work_rank,
            COUNT(*) OVER(PARTITION BY batch_id,publish_job_id) AS batch_job_count
        FROM ranked WHERE rank=1
    ) SELECT i.*,pj.account_id AS job_account,pj.platform,pj.task_id AS job_task,pj.output_clip_id,
        oc.task_id AS output_task,oc.clip_candidate_id,oc.source_duration_ms,oc.source_start_ms,oc.source_end_ms,
        oc.snapshot_source,oc.cut_run_id,c.task_id AS candidate_task,c.clip_key,c.source_analysis_run_id
    FROM works i LEFT JOIN publish_jobs pj ON pj.id=i.publish_job_id
    LEFT JOIN output_clip oc ON oc.id=pj.output_clip_id LEFT JOIN clip_candidates c ON c.id=oc.clip_candidate_id
    WHERE i.work_rank=1 ORDER BY julianday(i.captured_at) DESC,i.id LIMIT ?""",
        (account_id, DOUYIN_ITEM_EXPORT_SOURCE_KIND, cutoff.isoformat(), cutoff.isoformat(), MAX_WORKS + 1))]
    return rows[:MAX_WORKS], len(rows) > MAX_WORKS


def _runs(connection, rows):
    ids = sorted({r["source_analysis_run_id"] for r in rows if r.get("source_analysis_run_id")})
    result, versions = {}, {}
    for offset in range(0, len(ids), 400):
        chunk = ids[offset:offset + 400]
        for raw in connection.execute(f"""SELECT r.*,v.profile_id,p.prompt_text AS version_prompt,
            p.prompt_sha256 AS version_prompt_sha FROM ai_analysis_runs r
            LEFT JOIN content_profile_versions v ON v.id=r.content_profile_version_id
            LEFT JOIN ai_prompt_versions p ON p.id=r.prompt_version_id WHERE r.id IN ({','.join('?' for _ in chunk)})""", chunk):
            run = _run_data(dict(raw))
            version_id = run.get("content_profile_version_id")
            if version_id not in versions:
                try:
                    versions[version_id] = _version(connection, version_id)[0] if version_id else None
                except (ValueError, TypeError):
                    versions[version_id] = None
            version = versions[version_id]
            profile_valid = bool(version and version["config_sha256"] == run.get("content_profile_sha256"))
            prompt_sha = hashlib.sha256(run["version_prompt"].encode()).hexdigest() if isinstance(run.get("version_prompt"), str) else None
            prompt_valid = bool(prompt_sha and prompt_sha == run.get("prompt_text_sha256") == run.get("version_prompt_sha"))
            run.update(profile_valid=profile_valid, prompt_valid=prompt_valid,
                       rules_version=version["rules_version"] if profile_valid else None)
            run["verified_execution_controls"] = verified_execution_controls(connection, run)
            result[run["id"]] = run
    return result


def _features(row, run, duplicated_job):
    chain_valid = bool(row.get("publish_job_id") and row.get("match_status") in MATCHED_STATUSES
        and row.get("job_account") == row["account_id"] and row.get("platform") == "douyin"
        and row.get("job_task") == row.get("output_task") == row.get("candidate_task")
        and row.get("candidate_task") and not duplicated_job)
    run_valid = bool(chain_valid and run and run["task_id"] == row["candidate_task"])
    observation = next((o for o in run["observations"] if o.get("clip_key") == row.get("clip_key")), None) if run_valid else None
    duration = _number(row.get("duration_seconds"))
    duration_basis = "official_export" if duration and duration > 0 else "unknown"
    cut_duration = _number(row.get("source_duration_ms")) if chain_valid and row.get("snapshot_source") == "cut_commit" else None
    if duration_basis == "unknown":
        duration = cut_duration / 1000 if cut_duration and cut_duration > 0 else None
        duration_basis = "cut_snapshot" if duration else "unknown"
    published, captured = _parse_iso_datetime(row.get("published_at")), _parse_iso_datetime(row.get("captured_at"))
    age = (captured - published).total_seconds() / 86400 if published and captured and captured >= published else None
    age_band = "unknown" if age is None else "0–7d" if age <= 7 else "8–30d" if age <= 30 else ">30d"
    metrics = {key: _number(row.get(key)) for key in METRICS}
    metrics["watch_ratio"] = metrics["average_watch_seconds"] / duration if metrics["average_watch_seconds"] is not None and duration else None
    profile = run["profile"] if run_valid else "unknown"
    source = run.get("source_identity", {}) if run_valid else {}
    source_sha = source.get("sha256") if source.get("kind") == "original_video_sha256" else None
    source_sha = source_sha if isinstance(source_sha, str) and len(source_sha) == 64 and all(c in "0123456789abcdef" for c in source_sha) else None
    score = observation.get("quality_score") if observation else None
    initial_start = _clock_ms(observation.get("start_time")) if observation else None
    initial_end = _clock_ms(observation.get("end_time")) if observation else None
    bounds_known = (chain_valid and row.get("snapshot_source") == "cut_commit"
        and initial_start is not None and initial_end is not None and initial_end > initial_start
        and row.get("source_start_ms") is not None and row.get("source_end_ms") is not None)
    bounds_match = (initial_start == row["source_start_ms"] and initial_end == row["source_end_ms"]) if bounds_known else None
    version_valid = bool(run_valid and run["profile_valid"] and run["prompt_valid"] and observation and not run["incomplete"])
    return {"snapshot_id": row["id"], "batch_id": row["batch_id"], "export_identity": row["aweme_id"],
        "publish_job_id": row.get("publish_job_id"), "output_clip_id": row.get("output_clip_id"),
        "cut_run_id": row.get("cut_run_id"), "task_id": row.get("candidate_task") if chain_valid else None,
        "run_id": run["id"] if run_valid else None, "observation_key": observation["key"] if observation else None,
        "observation_sha256": observation["sha256"] if observation else None,
        "title": row.get("title"), "published_at": row.get("published_at"), "captured_at": row.get("captured_at"),
        "duration_seconds": duration, "duration_basis": duration_basis, "duration_band": _band(duration),
        "cut_start_ms": row.get("source_start_ms") if chain_valid else None,
        "cut_end_ms": row.get("source_end_ms") if chain_valid else None,
        "initial_start_ms": initial_start, "initial_end_ms": initial_end, "initial_bounds_match": bounds_match,
        "age_days": round(age, 3) if age is not None else None, "age_band": age_band,
        "publish_hour": str(published.astimezone(BEIJING_TIMEZONE).hour) if published else "unknown",
        "profile": profile, "profile_version_id": run.get("content_profile_version_id") if run_valid else None,
        "profile_sha256": run.get("content_profile_sha256") if run_valid else None,
        "prompt_version_id": run.get("prompt_version_id") if run_valid else None,
        "prompt_sha256": run.get("prompt_text_sha256") if run_valid else None,
        "rules_version": run.get("rules_version") if run_valid else None,
        "provider": run.get("provider") if run_valid else None, "model": run.get("model") if run_valid else None,
        "source_sha256": source_sha, "topic": observation.get("topic") if observation else None,
        "hook_type": observation.get("hook_type") if observation else None,
        "quality_score": score, "score_band": "unknown" if score is None else "0–64" if score < 65 else "65–77" if score < 78 else "78–89" if score < 90 else "90–100",
        "attribution_valid": chain_valid, "strategy_evidence_valid": version_valid,
        "attribution_issue": "multiple_export_identities_for_job" if duplicated_job else None if chain_valid else "missing_or_invalid_chain",
        "execution_controls": run.get("verified_execution_controls") if run_valid else None,
        "metrics": metrics}


def execution_controls(run):
    """Unknown execution settings never mean the current settings or disabled visual."""
    if not run:
        return None
    try:
        meta = json.loads(run.get("analysis_payload_json") or "{}")["analysis_meta"]
        provider, selection = meta["provider_identity"], meta["effective_selection"]
        signal = meta["visual_signal"]
        visual = signal["policy"]
        if (not isinstance(provider, dict) or not isinstance(provider.get("fields"), dict)
                or provider.get("name") != run.get("provider") or not run.get("model")
                or provider["fields"].get("model") != run["model"]
                or not isinstance(selection, dict) or not selection.get("selection_profile")
                or not isinstance(visual, dict) or type(visual.get("enabled")) is not bool
                or signal.get("status") not in ("disabled", "completed", "partial", "unavailable")):
            return None
        feedback = meta.get("feedback_context")
        from app.services.content_profile_service import SELECTION_FIELDS
        if any(key not in selection for key in SELECTION_FIELDS):
            return None
        if selection["selection_profile"] == "variety_comedy" and (not isinstance(feedback, dict)
                or not isinstance(feedback.get("items"), list) or not feedback.get("query_version")):
            return None
        value = {"schema_version": "trial-controls-v1", "provider": provider, "model": run["model"],
                 "selection": selection, "visual_policy": visual,
                 "visual_status": signal["status"],
                 "feedback_sha256": evidence_hash(feedback) if feedback is not None else None}
        return {"value": value, "sha256": evidence_hash(value)}
    except (KeyError, TypeError, ValueError):
        return None


def verified_execution_controls(connection, run):
    """Cross-check actual Run against its frozen Job, without reconstructing old jobs."""
    try:
        from app.services.content_profile_service import read_job_snapshot
        meta = json.loads(run["analysis_payload_json"])["analysis_meta"]
        job = connection.execute("SELECT * FROM workflow_jobs WHERE id=? AND task_id=?",
                                 (meta.get("workflow_job_id"), run["task_id"])).fetchone()
        if not job:
            return None
        job = dict(job)
        job["payload_json"] = json.loads(job["payload_json"])
        frozen = read_job_snapshot(job, connection=connection)
        if not frozen:
            return None
        prompt = frozen["prompt"]
        if (prompt["prompt_version_id"] != run["prompt_version_id"]
                or prompt["prompt_sha256"] != run["prompt_text_sha256"]
                or prompt["content_profile_version_id"] != run["content_profile_version_id"]
                or prompt["content_profile_sha256"] != run["content_profile_sha256"]
                or meta.get("prompt_version_id") != run["prompt_version_id"]
                or meta.get("prompt_sha256") != run["prompt_text_sha256"]
                or frozen["selection"] != meta.get("effective_selection")
                or frozen.get("provider_identity") != meta.get("provider_identity")
                or frozen.get("visual_policy") != meta.get("visual_signal", {}).get("policy")
                or frozen.get("feedback_context") != meta.get("feedback_context")):
            return None
        return execution_controls(run)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _group(rows, dimension, key, weeks, truncated):
    minimum = 30 if dimension == "prompt_version_id" else 20
    missing = {name: sum(row["metrics"][name] is None for row in rows) for name in METRICS}
    trusted = (not truncated and key != "unknown" and len(rows) >= minimum and len(weeks) >= 3
        and all(r["strategy_evidence_valid"] and r.get("initial_bounds_match") is True and r["source_sha256"]
                and r["age_band"] != "unknown" and r["profile"] != "unknown" for r in rows)
        and len({r["source_sha256"] for r in rows}) >= 3)
    metrics = {}
    for name in METRICS:
        values = [r["metrics"][name] for r in rows if r["metrics"][name] is not None]
        metrics[name] = {"count": len(values), "missing_rate": round(missing[name] / len(rows), 4),
                         "median": round(statistics.median(values), 6) if values else None,
                         "confidence": "comparison_ready" if trusted and not missing[name] else "insufficient"}
    return {"key": key, "profile": rows[0]["profile"], "age_band": rows[0]["age_band"], "work_count": len(rows),
            "verified_original_count": len({r["source_sha256"] for r in rows if r["source_sha256"]}),
            "strategy_unknown": sum(not r["strategy_evidence_valid"] for r in rows),
            "bounds_changed": sum(r.get("initial_bounds_match") is False for r in rows),
            "bounds_unknown": sum(r.get("initial_bounds_match") is None for r in rows),
            "published_start": min((r["published_at"] for r in rows), key=_parse_iso_datetime),
            "published_end": max((r["published_at"] for r in rows), key=_parse_iso_datetime),
            "official_weeks": sorted(weeks), "minimum_works": minimum, "metrics": metrics,
            "confidence": "comparison_ready" if trusted else "insufficient",
            "confidence_label": "达到比较门槛" if trusted else "数据不足"}


def _performance(connection, account_id, start, cutoff):
    if not account_id:
        return {"status": "no_account", "works": [], "groups": {}, "recommendations": [], "work_count": 0,
                "note": "未选择账号，仅提供人工审片统计，不生成作品表现结论。"}
    account = connection.execute("SELECT platform FROM publish_accounts WHERE id=?", (account_id,)).fetchone()
    if not account or account[0] != "douyin":
        raise ContentReviewError("抖音账号不存在", status_code=404)
    rows, truncated = _official_rows(connection, account_id, cutoff)
    missing_time = sum(not _parse_iso_datetime(r.get("published_at")) for r in rows)
    rows = [r for r in rows if (published := _parse_iso_datetime(r.get("published_at"))) and start <= published <= cutoff]
    runs = _runs(connection, rows)
    works = [_features(r, runs.get(r.get("source_analysis_run_id")), bool(r.get("publish_job_id") and r["batch_job_count"] > 1)) for r in rows]
    # Evidence weeks belong to the included works, not unrelated account exports.
    weeks_by_work = defaultdict(set)
    week_rows = connection.execute("""SELECT i.batch_id,i.aweme_id,i.publish_job_id,i.match_status,i.captured_at FROM douyin_item_metric_snapshots i
        JOIN content_metric_import_batches b ON b.id=i.batch_id WHERE i.account_id=? AND b.account_id=i.account_id
        AND b.status='committed' AND b.source_kind=? AND julianday(i.captured_at) BETWEEN julianday(?) AND julianday(?)
        AND julianday(b.committed_at)<=julianday(?) ORDER BY i.captured_at DESC,i.id LIMIT 200001""",
        (account_id, DOUYIN_ITEM_EXPORT_SOURCE_KIND, start.isoformat(), cutoff.isoformat(), cutoff.isoformat())).fetchall()
    truncated = truncated or len(week_rows) > 200000
    for row in week_rows[:200000]:
        when = _parse_iso_datetime(row["captured_at"])
        if when:
            key = "job:" + row["publish_job_id"] if row["publish_job_id"] and row["match_status"] in MATCHED_STATUSES else "work:" + row["aweme_id"]
            weeks_by_work[key].add(when.astimezone(BEIJING_TIMEZONE).strftime("%G-W%V"))
    batch_identities = defaultdict(set)
    for row in week_rows[:200000]:
        if row["publish_job_id"]:
            batch_identities[(row["batch_id"], row["publish_job_id"])].add(row["aweme_id"])
    verified_weeks = defaultdict(set)
    works_by_job = {w["publish_job_id"]: w for w in works if w["attribution_valid"]}
    for row in week_rows[:200000]:
        work = works_by_job.get(row["publish_job_id"])
        when = _parse_iso_datetime(row["captured_at"])
        if (work and row["match_status"] in MATCHED_STATUSES and when
                and when >= _parse_iso_datetime(work["published_at"])
                and len(batch_identities[(row["batch_id"], row["publish_job_id"])]) == 1):
            verified_weeks[work["publish_job_id"]].add(when.astimezone(BEIJING_TIMEZONE).strftime("%G-W%V"))
    for work in works:
        work["official_weeks"] = sorted(verified_weeks[work["publish_job_id"]]) if work["attribution_valid"] else []
    groups = {}
    for dimension in DIMENSIONS:
        grouped = defaultdict(list)
        for work in works:
            grouped[(work["profile"], work["age_band"], str(work.get(dimension) or "unknown"))].append(work)
        groups[dimension] = [_group(values, dimension, key[2], set().union(*(weeks_by_work["job:" + w["publish_job_id"] if w["attribution_valid"] else "work:" + w["export_identity"]] for w in values)), truncated)
                             for key, values in sorted(grouped.items())]
    return {"status": "available" if works else "no_official_data", "time_basis": "published_at", "work_count": len(works),
        "attributed_count": sum(w["attribution_valid"] for w in works), "unknown_publish_time": missing_time,
        "truncated": truncated, "works": works, "groups": groups, "recommendations": _comparisons(groups),
        "note": "先取各官方导出键的最新记录，再按发布任务去重；同批多个作品键指向同一任务时记为归因歧义。未匹配记录仅按导出键计数，不能证明独立作品身份。每组隔离 Profile 与采集时发布年龄，指标缺失不是零。达到门槛仍只说明关联，不证明策略导致表现变化。"}


def _comparisons(groups):
    recommendations = []
    for dimension in ("duration_band", "topic", "hook_type", "score_band", "prompt_version_id", "publish_hour"):
        strata = defaultdict(list)
        for group in groups[dimension]:
            if group["metrics"]["five_second_completion_rate"]["confidence"] == "comparison_ready":
                strata[(group["profile"], group["age_band"])].append(group)
        for (profile, age), values in sorted(strata.items()):
            if len(values) < 2:
                continue
            ordered = sorted(values, key=lambda g: (g["metrics"]["five_second_completion_rate"]["median"], g["key"]))
            lower, higher = ordered[0], ordered[-1]
            low, high = lower["metrics"]["five_second_completion_rate"]["median"], higher["metrics"]["five_second_completion_rate"]["median"]
            if high <= low:
                continue
            evidence = {"dimension": dimension, "profile": profile, "age_band": age,
                        "lower_key": lower["key"], "higher_key": higher["key"],
                        "lower_count": lower["work_count"], "higher_count": higher["work_count"],
                        "metric": "five_second_completion_rate", "lower_median": low, "higher_median": high}
            recommendations.append({"id": evidence_hash(evidence)[:24], "evidence": evidence,
                "confidence": "comparison_ready", "conclusion": "在本报告相同内容类型和发布年龄组中，两个分组的 5 秒完播中位数不同。",
                "recommendation": "可据此提出单变量 Challenger 草稿，再通过人工实验判断；不能直接改变生产策略。",
                "limitation": "探索性分组比较，未校正多重比较、题材差异和发布时间偏差，不构成因果或显著性结论。"})
    return recommendations[:12]


def create_report(account_id, days, request_key):
    config = {"account_id": account_id or None, "days": days, "schema_version": VERSION}
    config_sha = evidence_hash(config)
    if not 1 <= days <= 180:
        raise ContentReviewError("报告时间范围无效")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT * FROM content_intelligence_reports WHERE request_key=?", (request_key,)).fetchone()
        if existing:
            if existing["config_sha256"] != config_sha:
                raise ContentReviewError("重复请求的报告配置不一致", status_code=409)
            return _read_report(existing)
        cutoff = datetime.now(timezone.utc)
        start = cutoff - timedelta(days=days)
        report = {**config, "start": start.isoformat(), "cutoff": cutoff.isoformat(),
            "human_review": get_human_review_summary(days, cutoff=cutoff.isoformat(), connection=connection, include_evidence=True),
            "performance": _performance(connection, account_id, start, cutoff),
            "policy": "报告仅保存观察，不调用模型、不修改 Prompt/Profile、不创建实验或排期。"}
        report_id = uuid4().hex
        payload = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        connection.execute("""INSERT INTO content_intelligence_reports
            (id,account_id,request_key,config_sha256,schema_version,payload_json,payload_sha256,created_at)
            VALUES(?,?,?,?,?,?,?,?)""", (report_id, account_id or None, request_key, config_sha, VERSION, payload, evidence_hash(report), cutoff.isoformat()))
        connection.commit()
        return {"id": report_id, "payload_sha256": evidence_hash(report), "created_at": cutoff.isoformat(), "report": report}


def _read_report(row):
    try:
        payload = json.loads(row["payload_json"])
        config = {key: payload[key] for key in ("account_id", "days", "schema_version")}
        if (evidence_hash(payload) != row["payload_sha256"] or evidence_hash(config) != row["config_sha256"]
                or payload["account_id"] != row["account_id"] or payload["schema_version"] != row["schema_version"]):
            raise ValueError("hash mismatch")
    except (ValueError, TypeError, KeyError) as exc:
        raise ContentReviewError("报告证据校验失败，不能作为建议或实验依据", status_code=409) from exc
    return {"id": row["id"], "payload_sha256": row["payload_sha256"], "created_at": row["created_at"], "report": payload}


def get_report(report_id):
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM content_intelligence_reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise ContentReviewError("报告不存在", status_code=404)
        return _read_report(row)


def list_reports(account_id=None, limit=30):
    with get_connection() as connection:
        return [dict(r) for r in connection.execute("""SELECT id,account_id,created_at,payload_sha256,schema_version
            FROM content_intelligence_reports WHERE account_id IS ? ORDER BY created_at DESC,id DESC LIMIT ?""", (account_id or None, limit))]
