"""Explicit, evidence-bound experiments reuse Content Review and Prompt versions."""

from collections import defaultdict
from datetime import datetime, timezone
import json
import hashlib
import statistics
from uuid import uuid4

from app.db.database import get_connection
from app.services.content_challenger_service import (
    _fail, _report, _prompt_version, read_challenger_with_connection,
)
from app.services.content_intelligence_service import _performance, _clock_ms, verified_execution_controls, METRICS
from app.services.content_profile_service import active_profile
from app.services.review_observation_service import evidence_hash

PRIMARY = "five_second_completion_rate"
GUARDRAILS = ("completion_rate", "two_second_bounce_rate", "watch_ratio")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _summary(works, truncated=False):
    weeks = sorted(set().union(*(set(w.get("official_weeks", [])) for w in works)))
    originals = {w.get("source_sha256") for w in works if w.get("source_sha256")}
    metrics = {}
    for name in METRICS:
        values = [w["metrics"][name] for w in works if w["metrics"].get(name) is not None]
        metrics[name] = {"count": len(values), "missing": len(works) - len(values),
                         "median": round(statistics.median(values), 6) if values else None}
    ready = bool(not truncated and len(works) >= 20 and len(weeks) >= 3 and len(originals) >= 3
                 and metrics[PRIMARY]["count"] == len(works))
    return {"work_count": len(works), "official_weeks": weeks, "verified_original_count": len(originals),
            "metrics": metrics, "ready": ready, "confidence_label": "达到比较门槛" if ready else "数据不足",
            "published_start": min((w["published_at"] for w in works), default=None),
            "published_end": max((w["published_at"] for w in works), default=None)}


def _matches(work, draft, prompt_version):
    base = draft["evidence"]["baseline"]
    return bool(work.get("attribution_valid") and work.get("strategy_evidence_valid")
        and work.get("initial_bounds_match") is True and work.get("source_sha256")
        and work.get("profile_version_id") == draft["profile_version_id"]
        and work.get("profile_sha256") == base["profile_sha256"]
        and work.get("rules_version") == base["rules_version"]
        and work.get("prompt_version_id") == prompt_version
        and work.get("age_band") not in (None, "unknown") and work.get("execution_controls")
        and work.get("official_weeks"))


def _context(connection, challenger_id, report_id):
    draft = read_challenger_with_connection(connection, challenger_id)
    saved = _report(connection, report_id)
    report, grouped = saved["report"], defaultdict(list)
    performance = report["performance"]
    for work in performance.get("works", []):
        if _matches(work, draft, draft["champion_prompt_version_id"]):
            controls = work["execution_controls"]
            if controls.get("sha256") != evidence_hash(controls.get("value")):
                _fail("报告执行条件证据损坏")
            grouped[(work["age_band"], controls["sha256"])].append(work)
    cohorts = []
    for (age, _controls_sha), works in sorted(grouped.items()):
        cohort = {"age_band": age, "controls": works[0]["execution_controls"],
                  "works": works, **_summary(works, performance.get("truncated", False))}
        cohort["sha256"] = evidence_hash(cohort)
        cohorts.append(cohort)
    return {"challenger_id": draft["id"], "challenger_sha256": draft["evidence_sha256"],
            "report_id": saved["id"], "report_sha256": saved["payload_sha256"],
            "account_id": report.get("account_id"), "report_start": report["start"], "report_cutoff": report["cutoff"],
            "cohorts": cohorts, "primary_metric": PRIMARY, "guardrails": list(GUARDRAILS),
            "notice": "至少 20 条同策略作品、3 条可验证原片和 3 个官方导出周，主指标不能缺失。旧报告缺少执行条件时须重新生成；不倒推历史。只说明关联，不证明因果。"}


def experiment_context(challenger_id, report_id):
    with get_connection() as connection:
        connection.execute("BEGIN")
        return _context(connection, challenger_id, report_id)


def create_experiment(challenger_id, payload):
    if not payload.confirm:
        _fail("请明确确认冻结基线并创建实验", 422)
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        context = _context(connection, challenger_id, payload.report_id)
        if context["challenger_sha256"] != payload.challenger_sha256 or context["report_sha256"] != payload.report_sha256:
            _fail("预览的报告或 Challenger 已不匹配")
        cohort = next((c for c in context["cohorts"] if c["sha256"] == payload.cohort_sha256), None)
        if not cohort or not cohort["ready"] or not context["account_id"]:
            _fail("真实官方基线或样本不足，不能创建正式作品实验")
        existing = connection.execute("SELECT * FROM content_improvement_experiments WHERE challenger_id=?", (challenger_id,)).fetchone()
        if existing:
            strategy, _ = read_strategy(connection, dict(existing))
            if strategy["report_id"] != payload.report_id or strategy["baseline"]["sha256"] != payload.cohort_sha256:
                _fail("此 Challenger 已冻结另一实验基线，不能重新挑选证据")
            return {"experiment_id": existing["id"], "status": "already_created"}
        draft = read_challenger_with_connection(connection, challenger_id)
        now, experiment_id = datetime.now(timezone.utc).isoformat(), "experiment-" + uuid4().hex[:16]
        strategy = {"schema_version": "challenger-experiment-v1", **{k: v for k, v in context.items() if k != "cohorts"},
                    "baseline": cohort, "champion_prompt_version_id": draft["champion_prompt_version_id"],
                    "challenger_prompt_version_id": draft["challenger_prompt_version_id"],
                    "profile_version_id": draft["profile_version_id"], "created_at": now}
        connection.execute("""INSERT INTO content_improvement_experiments
            (id,account_id,recommendation_id,diagnosis_code,title,hypothesis,action_text,primary_metric,primary_direction,
             guardrail_metrics_json,baseline_batch_id,baseline_json,target_sample_size,minimum_baseline_size,minimum_weeks,
             status,created_at,updated_at,challenger_id,strategy_json,strategy_sha256)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,20,20,3,'active',?,?,?,?,?)""",
            (experiment_id, context["account_id"], "challenger:" + challenger_id, "prompt_challenger", draft["evidence"]["name"],
             draft["evidence"]["hypothesis"], "使用冻结 Challenger 试验任务，人工审片后在发送中心关联实验。", PRIMARY, "higher",
             _json(GUARDRAILS), cohort["works"][0]["batch_id"], _json(cohort), now, now,
             challenger_id, _json(strategy), evidence_hash(strategy)))
        connection.commit()
    return {"experiment_id": experiment_id, "status": "created"}


def read_strategy(connection, experiment):
    draft = read_challenger_with_connection(connection, experiment["challenger_id"])
    try:
        strategy = json.loads(experiment["strategy_json"])
        saved = _report(connection, strategy["report_id"])
        if (evidence_hash(strategy) != experiment["strategy_sha256"] or strategy["challenger_id"] != draft["id"]
                or strategy["challenger_sha256"] != draft["evidence_sha256"]
                or strategy["report_sha256"] != saved["payload_sha256"]
                or strategy["account_id"] != experiment["account_id"]
                or strategy["baseline"] != json.loads(experiment["baseline_json"])
                or strategy["champion_prompt_version_id"] != draft["champion_prompt_version_id"]
                or strategy["challenger_prompt_version_id"] != draft["challenger_prompt_version_id"]
                or strategy["profile_version_id"] != draft["profile_version_id"]
                or experiment["primary_metric"] != PRIMARY or experiment["primary_direction"] != "higher"
                or any(experiment[k] != v for k, v in (("target_sample_size", 20), ("minimum_baseline_size", 20), ("minimum_weeks", 3)))):
            _fail("实验策略证据不一致")
    except (ValueError, TypeError, KeyError) as exc:
        _fail(f"实验策略证据无法读取：{exc}")
    return strategy, draft


def validate_assignment(connection, experiment, job, *, historical=False):
    if not experiment.get("challenger_id"):
        return
    strategy, draft = read_strategy(connection, experiment)
    row = connection.execute("""SELECT oc.task_id AS output_task,oc.snapshot_source,oc.is_active,
        oc.source_start_ms,oc.source_end_ms,oc.source_duration_ms,c.clip_key,c.task_id AS candidate_task,r.*
        FROM output_clip oc JOIN clip_candidates c ON c.id=oc.clip_candidate_id
        JOIN ai_analysis_runs r ON r.id=c.source_analysis_run_id WHERE oc.id=?""", (job["output_clip_id"],)).fetchone()
    if not row:
        _fail("作品缺少真实 AI Run 策略链")
    run = dict(row)
    if (job["account_id"] != experiment["account_id"] or job["platform"] != "douyin"
            or not job["task_id"] == run["task_id"] == run["output_task"] == run["candidate_task"]
            or run["snapshot_source"] not in {"cut_commit", "cut_plan_v1"} or (not historical and not run["is_active"])
            or run["prompt_version_id"] != draft["challenger_prompt_version_id"]):
        _fail("发布作品实际 Run 与实验策略不匹配")
    from app.services.challenger_trial_service import validate_trial_run, task_binding
    if task_binding(connection, run["task_id"]) != {"id": draft["id"], "sha256": draft["evidence_sha256"]}:
        _fail("作品不属于此 Challenger 的显式试验任务")
    try:
        validate_trial_run(connection, run["task_id"], run)
    except ValueError as exc:
        _fail(str(exc))
    if verified_execution_controls(connection, run) != strategy["baseline"]["controls"]:
        _fail("实际 Provider、模型、候选参数、视觉或反馈上下文与实验基线不匹配")
    from app.services.human_review_service import _run_data
    observed = _run_data(run)
    candidates = [o for o in observed["observations"] if o.get("clip_key") == run["clip_key"]]
    if observed["incomplete"] or len(candidates) != 1:
        _fail("AI Run 不完整或缺少唯一初始候选证据，不能入组")
    start, end = (_clock_ms(candidates[0].get(key)) for key in ("start_time", "end_time"))
    if (start is None or end is None or end <= start or start != run["source_start_ms"]
            or end != run["source_end_ms"] or run["source_duration_ms"] != end - start):
        _fail("实际成片与 AI 初始候选边界不一致，不能入组")


def progress(connection, experiment):
    strategy, draft = read_strategy(connection, experiment)
    if experiment.get("decision_evidence_json"):
        try:
            decision = json.loads(experiment["decision_evidence_json"])
            if (evidence_hash(decision) != experiment["decision_evidence_sha256"]
                    or decision["strategy_sha256"] != experiment["strategy_sha256"]
                    or decision["experiment_id"] != experiment["id"]
                    or decision["decision"] != experiment["decision"]
                    or decision["decided_at"] != experiment["completed_at"]):
                _fail("已结束实验的结论证据不一致")
            return decision["progress"]
        except (ValueError, TypeError, KeyError) as exc:
            _fail(f"实验结论证据无法读取：{exc}")
    assigned = [dict(r) for r in connection.execute("""SELECT pj.* FROM content_improvement_experiment_items ei
        JOIN publish_jobs pj ON pj.id=ei.publish_job_id WHERE ei.experiment_id=?""", (experiment["id"],))]
    valid, invalid = set(), []
    for job in assigned:
        try:
            validate_assignment(connection, experiment, job, historical=True)
            valid.add(job["id"])
        except ValueError as exc:
            invalid.append({"publish_job_id": job["id"], "reason": str(exc)})
    cutoff = datetime.now(timezone.utc)
    start = datetime.fromisoformat(strategy["report_start"])
    official = _performance(connection, experiment["account_id"], start, cutoff)
    baseline = strategy["baseline"]
    works = [w for w in official["works"] if w.get("publish_job_id") in valid
             and _matches(w, draft, draft["challenger_prompt_version_id"])
             and w["age_band"] == baseline["age_band"] and w["execution_controls"] == baseline["controls"]]
    summary = _summary(works, official.get("truncated", False))
    ready = summary["ready"] and not invalid and baseline["ready"]
    before, after = baseline["metrics"][PRIMARY]["median"], summary["metrics"][PRIMARY]["median"]
    return {"assigned_count": len(assigned), "treatment_count": len(works), "baseline_count": baseline["work_count"],
            "official_export_weeks": len(summary["official_weeks"]), "official_export_week_keys": summary["official_weeks"],
            "target_sample_size": 20, "minimum_baseline_size": 20, "minimum_weeks": 3,
            "stage": "decision_ready" if ready else "collecting", "trend_visible": len(works) >= 10,
            "decision_ready": ready, "treatment_metrics": {k: v["median"] for k, v in summary["metrics"].items()},
            "baseline_primary": before, "treatment_primary": after,
            "primary_delta": round(after - before, 6) if before is not None and after is not None else None,
            "summary": summary, "invalid_assignments": invalid, "works": works, "cutoff": cutoff.isoformat(),
            "excluded_count": len(assigned) - len(works), "notice": strategy["notice"]}


def freeze_decision(connection, experiment, decision, outcome, now):
    evidence = {"schema_version": "experiment-decision-v1", "experiment_id": experiment["id"],
                "strategy_sha256": experiment["strategy_sha256"], "decision": decision, "progress": outcome,
                "decided_at": now, "source": "explicit_human_decision"}
    connection.execute("""UPDATE content_improvement_experiments SET decision_evidence_json=?,decision_evidence_sha256=?
        WHERE id=?""", (_json(evidence), evidence_hash(evidence), experiment["id"]))


def _decision(experiment):
    try:
        evidence = json.loads(experiment["decision_evidence_json"])
        if (evidence_hash(evidence) != experiment["decision_evidence_sha256"]
                or evidence["experiment_id"] != experiment["id"] or evidence["strategy_sha256"] != experiment["strategy_sha256"]
                or evidence["decision"] != experiment["decision"] or evidence["decided_at"] != experiment["completed_at"]
                or not evidence["progress"]["decision_ready"] or experiment["status"] != "completed" or experiment["decision"] != "keep"):
            _fail("只有已冻结且人工保留的完整实验可正式启用")
        return evidence
    except (KeyError, TypeError, ValueError) as exc:
        _fail(f"人工实验结论证据不足：{exc}")


def _event(connection, row):
    value = dict(row)
    try:
        evidence = json.loads(value.pop("evidence_json"))
        if evidence_hash(evidence) != value["evidence_sha256"]:
            _fail("正式策略操作证据损坏")
        before = _prompt_version(connection, value["before_prompt_version_id"])
        after = _prompt_version(connection, value["after_prompt_version_id"])
        if (before["prompt_sha256"] != evidence["before_sha256"] or after["prompt_sha256"] != evidence["after_sha256"]
                or evidence["action"] != value["action"] or evidence["experiment_id"] != value["experiment_id"]):
            _fail("策略操作与实际 Prompt 版本不一致")
        return {**value, "evidence": evidence}
    except (KeyError, TypeError, ValueError) as exc:
        _fail(f"正式策略操作证据无法读取：{exc}")


def _policy_preview(connection, experiment_id, action):
    row = connection.execute("SELECT * FROM content_improvement_experiments WHERE id=?", (experiment_id,)).fetchone()
    if not row or not row["challenger_id"]:
        _fail("Challenger 实验不存在", 404)
    experiment = dict(row)
    strategy, draft = read_strategy(connection, experiment)
    decision = _decision(experiment)
    base = draft["evidence"]["baseline"]
    head, _ = active_profile(connection, base["profile"]["id"])
    if head["id"] != draft["profile_version_id"] or head["config_sha256"] != base["profile_sha256"]:
        _fail("正式 Profile 已改变，请重新评估实验，不自动覆盖")
    preset = connection.execute("SELECT * FROM ai_prompt_presets WHERE id=? AND is_archived=0", (base["preset_id"],)).fetchone()
    if not preset:
        _fail("正式 Prompt 不存在或已归档")
    latest = connection.execute("SELECT * FROM content_policy_events WHERE experiment_id=? ORDER BY rowid DESC LIMIT 1", (experiment_id,)).fetchone()
    latest = _event(connection, latest) if latest else None
    if action == "activate":
        if latest:
            _fail("此实验已执行过正式启用；回退后再次尝试须建立新草稿和实验")
        before, after = base["prompt_text"], draft["evidence"]["challenger_prompt_text"]
    elif action == "rollback":
        if not latest or latest["action"] != "activate":
            _fail("没有可回退的启用记录")
        before = _prompt_version(connection, latest["after_prompt_version_id"])["prompt_text"]
        after = latest["evidence"]["before_text"]
    else:
        _fail("未知策略操作", 422)
    if preset["prompt_text"].strip() != before.strip():
        _fail("正式 Prompt 已被其他操作改变，请重新核对，不能覆盖人工改动")
    preview = {"experiment_id": experiment_id, "challenger_id": draft["id"], "action": action,
               "preset_id": preset["id"], "preset_name": preset["name"], "before_text": preset["prompt_text"], "after_text": after,
               "before_sha256": hashlib.sha256(before.strip().encode()).hexdigest(),
               "after_sha256": hashlib.sha256(after.strip().encode()).hexdigest(),
               "profile_version_id": head["id"], "profile_sha256": head["config_sha256"],
               "strategy_sha256": experiment["strategy_sha256"], "decision_sha256": evidence_hash(decision),
               "latest_event_id": latest["id"] if latest else None,
               "notice": "只改变此正式 Prompt 的新任务默认正文；已冻结任务不变。实验结论不会自动执行此操作。"}
    return {**preview, "sha256": evidence_hash(preview)}


def policy_preview(experiment_id, action):
    with get_connection() as connection:
        connection.execute("BEGIN")
        return _policy_preview(connection, experiment_id, action)


def apply_policy(experiment_id, payload):
    if not payload.confirm:
        _fail("请核对差异并明确确认正式策略操作", 422)
    request_sha = evidence_hash({"experiment_id": experiment_id, **payload.model_dump(mode="json")})
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT * FROM content_policy_events WHERE request_key=?", (str(payload.request_key),)).fetchone()
        if existing:
            if existing["request_sha256"] != request_sha:
                _fail("重复请求的正式策略操作不一致")
            return _event(connection, existing)
        preview = _policy_preview(connection, experiment_id, payload.action)
        if preview["sha256"] != payload.preview_sha256:
            _fail("正式策略预览已过期，请重新核对")
        from app.services.ai_prompt_preset_service import ensure_ai_prompt_version_with_connection
        now = datetime.now(timezone.utc).isoformat()
        versions = [ensure_ai_prompt_version_with_connection(connection, preset_id=preview["preset_id"],
            preset_name=preview["preset_name"], prompt_text=preview[key], now=now) for key in ("before_text", "after_text")]
        connection.execute("UPDATE ai_prompt_presets SET prompt_text=?,updated_at=? WHERE id=?",
                           (preview["after_text"], now, preview["preset_id"]))
        event_id = uuid4().hex
        connection.execute("""INSERT INTO content_policy_events
            (id,experiment_id,challenger_id,action,before_prompt_version_id,after_prompt_version_id,
             request_key,request_sha256,evidence_json,evidence_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, experiment_id, preview["challenger_id"], payload.action, versions[0]["id"], versions[1]["id"],
             str(payload.request_key), request_sha, _json(preview), evidence_hash(preview), now))
        result = _event(connection, connection.execute("SELECT * FROM content_policy_events WHERE id=?", (event_id,)).fetchone())
        connection.commit()
        return result


def policy_events(experiment_id):
    with get_connection() as connection:
        connection.execute("BEGIN")
        return [_event(connection, r) for r in connection.execute("SELECT * FROM content_policy_events WHERE experiment_id=? ORDER BY rowid", (experiment_id,))]
