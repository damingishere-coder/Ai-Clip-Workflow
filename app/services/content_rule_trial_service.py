"""复盘先在固定素材上试验；明确内容审核后才允许应用同一个方案。"""

import hashlib
import json
from uuid import uuid4

from app.db.database import get_connection


def change_hash(change):
    return hashlib.sha256(
        json.dumps(change, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def record_manual_prompt_change(
    connection, preset_id, before, after, reason, timestamp
):
    if before == after:
        return
    from app.services.weekly_review_service import freeze_task

    # 必须在正文改变前冻结遗漏的历史任务，避免追溯时把新正文当成旧输入。
    for task in connection.execute(
        "SELECT id FROM tasks WHERE id NOT IN (SELECT task_id FROM task_generation_rules)"
    ).fetchall():
        freeze_task(connection, task["id"])
    head = connection.execute(
        "SELECT application_id FROM content_rule_heads WHERE preset_id=?", (preset_id,)
    ).fetchone()
    previous = head["application_id"] if head else None
    connection.execute(
        "INSERT INTO prompt_change_audits VALUES (?,?,?,?,?,?,?)",
        (uuid4().hex, preset_id, before, after, previous, reason, timestamp),
    )
    if previous:
        connection.execute(
            "UPDATE content_rule_applications SET state='superseded',reverted_at=?,decision='manual_change' WHERE id=?",
            (timestamp, previous),
        )
        connection.execute(
            "UPDATE content_rule_heads SET application_id=NULL WHERE application_id=?",
            (previous,),
        )
    # 历史冻结输入及归因不改写；旧试验无法领取新正文的结果。
    connection.execute(
        "UPDATE content_rule_trials SET status='stale' WHERE status IN ('pending','accepted') AND report_id IN (SELECT id FROM weekly_content_reports WHERE result_json LIKE ?)",
        (f'%"{preset_id}"%',),
    )


def restore_kangxi_baseline(expected_sha256):
    """显式部署步骤调用；先备份数据库，拒绝活动任务和并发正文变化。"""
    from app.services.ai_prompt_preset_service import (
        ensure_ai_prompt_version_with_connection,
        _now_iso,
    )
    from app.db import database

    backup = database.create_schema_migration_backup(
        database.settings.database_path,
        database.settings.data_dir / "backups",
        "kangxi-baseline-restore",
    )

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute(
            "SELECT 1 FROM workflow_jobs WHERE status IN ('queued','running') LIMIT 1"
        ).fetchone():
            raise ValueError("存在活动任务，暂不恢复提示词")
        if connection.execute(
            "SELECT 1 FROM publish_jobs WHERE status='PUBLISHING' LIMIT 1"
        ).fetchone():
            raise ValueError("存在正在发布的任务，暂不恢复提示词")
        preset = connection.execute(
            "SELECT * FROM ai_prompt_presets WHERE id='preset_001'"
        ).fetchone()
        baseline = connection.execute(
            "SELECT prompt_text,prompt_sha256 FROM ai_prompt_versions WHERE preset_id='preset_001' AND version_number=1"
        ).fetchone()
        if (
            not baseline
            or baseline["prompt_sha256"]
            != "fac845220c05e37e8ec8372eadfd272a19367d3489ff57f9581f399f1a3f92e6"
            or hashlib.sha256(baseline["prompt_text"].encode()).hexdigest()
            != baseline["prompt_sha256"]
        ):
            raise ValueError("复盘前 1 号基线哈希不符，拒绝恢复")
        if (
            hashlib.sha256(preset["prompt_text"].encode()).hexdigest()
            != expected_sha256
        ):
            raise ValueError("活动提示词已变化，请重新核对快照")
        timestamp = _now_iso()
        record_manual_prompt_change(
            connection,
            "preset_001",
            preset["prompt_text"],
            baseline["prompt_text"],
            "恢复复盘前内容修订 1",
            timestamp,
        )
        connection.execute(
            "UPDATE ai_prompt_presets SET prompt_text=?,updated_at=? WHERE id='preset_001'",
            (baseline["prompt_text"], timestamp),
        )
        version = ensure_ai_prompt_version_with_connection(
            connection,
            preset_id="preset_001",
            preset_name=preset["name"],
            prompt_text=baseline["prompt_text"],
            now=timestamp,
        )
        connection.commit()
        return {
            "status": "restored",
            "prompt_version_id": version["id"],
            "prompt_sha256": baseline["prompt_sha256"],
            "backup": str(backup),
        }


def create_trial(report_id):
    from app.services.weekly_review_service import fail, now

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        report = connection.execute(
            "SELECT * FROM weekly_content_reports WHERE id=?", (report_id,)
        ).fetchone()
        if not report or report["status"] != "ready":
            fail("请先生成可核对的复盘建议")
        changes = json.loads(report["result_json"]).get("changes", [])
        if len(changes) != 1 or len(changes[0].get("suggestion_indexes", [])) != 1:
            fail("每轮只试验一个明确改动，请重新生成单项建议")
        change = changes[0]
        if (
            change["analysis_rules"].strip()
            and change["copy_rules"] != change["before_copy"]
        ):
            fail("选片和文案不能在同一轮同时改动")
        current = connection.execute(
            "SELECT prompt_text FROM ai_prompt_presets WHERE id=? AND is_archived=0",
            (change["preset_id"],),
        ).fetchone()
        if not current or current[0] != change["before_prompt"]:
            fail("基线正文已改变，请重新生成建议")
        digest = change_hash(change)
        existing = connection.execute(
            "SELECT * FROM content_rule_trials WHERE report_id=? AND change_hash=? AND status IN ('pending','accepted') ORDER BY created_at DESC LIMIT 1",
            (report_id, digest),
        ).fetchone()
        if existing:
            return dict(existing)
        trial_id = "trial-" + uuid4().hex[:20]
        connection.execute(
            "INSERT INTO content_rule_trials(id,report_id,change_index,change_hash,created_at) VALUES (?,?,0,?,?)",
            (trial_id, report_id, digest, now().isoformat()),
        )
        connection.commit()
        return {
            "id": trial_id,
            "status": "pending",
            "message": "试验已建立；正式正文未改变。请提交同素材基线与试验结果的逐条内容审核。",
        }


def review_trial(trial_id, payload):
    from app.services.weekly_review_service import fail, now

    materials = payload.get("materials")
    if not isinstance(materials, list) or not materials:
        fail("缺少固定素材对照结果")
    for material in materials:
        if not isinstance(material, dict) or not all(
            isinstance(material.get(k), str) and material[k].strip()
            for k in (
                "source_task_id",
                "transcript_sha256",
                "baseline_result_sha256",
                "trial_result_sha256",
            )
        ):
            fail("对照须记录原任务、同一转写哈希、两份结果哈希")
        samples = material.get("samples")
        if not isinstance(samples, list) or not samples:
            fail("必须逐条记录对照样本；零条也需说明无可用内容的依据")
        for sample in samples:
            if not isinstance(sample, dict) or not all(
                isinstance(sample.get(k), str) and sample[k].strip()
                for k in (
                    "sample_id",
                    "opening",
                    "topic",
                    "highlight",
                    "response",
                    "ending",
                    "comparison",
                )
            ):
                fail("逐条审核必须涵盖开头、主题、笑点、回应、收尾和基线对比")
    if payload.get("human_confirmed") is not True or payload.get("verdict") not in {
        "accepted",
        "rejected",
    }:
        fail("需要明确的人工内容审核结论，AI 评分不能代替")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        trial = connection.execute(
            "SELECT * FROM content_rule_trials WHERE id=?", (trial_id,)
        ).fetchone()
        if not trial or trial["status"] != "pending":
            fail("试验不存在、已结束或基线已变化")
        report = connection.execute(
            "SELECT result_json FROM weekly_content_reports WHERE id=?",
            (trial["report_id"],),
        ).fetchone()
        change = json.loads(report[0])["changes"][trial["change_index"]]
        if change_hash(change) != trial["change_hash"]:
            fail("试验草案已变化，请重新对照")
        _verify_material_results(connection, materials, change)
        connection.execute(
            "UPDATE content_rule_trials SET status=?,material_json=?,review_json=?,reviewed_at=? WHERE id=?",
            (
                payload["verdict"],
                json.dumps(materials, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                now().isoformat(),
                trial_id,
            ),
        )
        connection.commit()
    return {
        "status": payload["verdict"],
        "message": "内容审核已记录；正式规则尚未应用。",
    }


def _verify_material_results(connection, materials, change):
    from app.services.weekly_review_service import fail
    from app.services.storage_service import get_artifact_paths
    from app.services.ai_analysis_workflow_service import (
        validate_ai_analysis_meta_for_cut,
    )
    from app.models.task import AIClipAnalysisResult
    from app.services.ai.content_decision_analyzer import (
        CONTRACT_VERSION,
        validate_decisions,
    )
    from app.services.ai.ai_clip_analyzer import (
        _extract_transcript_rows,
        AIAnalysisError,
    )

    seen = set()
    for material in materials:
        task_id = material["source_task_id"]
        if task_id in seen:
            fail("固定素材重复")
        seen.add(task_id)
        task = connection.execute(
            "SELECT ai_prompt_preset_id FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if not task or task[0] != change["preset_id"]:
            fail("对照素材不属于本轮方案")
        transcript = get_artifact_paths(task_id)["transcript_path"]
        if (
            not transcript.is_file()
            or hashlib.sha256(transcript.read_bytes()).hexdigest()
            != material["transcript_sha256"]
        ):
            fail("对照转写与真实素材不一致")
        rows = _extract_transcript_rows(transcript.read_text(encoding="utf-8-sig"))
        required_samples = set()
        for side, prompt in (
            ("baseline", change["before_prompt"]),
            ("trial", change["after_prompt"]),
        ):
            result = material.get(side + "_result")
            if not isinstance(result, dict) or str(result.get("task_id")) != task_id:
                fail("缺少固定素材的完整基线或试验结果")
            digest = hashlib.sha256(
                json.dumps(
                    result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            if digest != material[side + "_result_sha256"]:
                fail("对照结果哈希不符")
            meta = validate_ai_analysis_meta_for_cut(
                result.get("analysis_meta"), "variety_comedy"
            )
            if meta.get("analysis_incomplete") or meta.get("quality_degraded"):
                fail("未完成或非法分析不能作为试验验收依据")
            if meta.get("decision_contract") != CONTRACT_VERSION:
                fail("试验缺少明确决策契约")
            if (
                meta.get("prompt_sha256") != hashlib.sha256(prompt.encode()).hexdigest()
                or meta.get("transcript_sha256") != material["transcript_sha256"]
            ):
                fail("对照使用的提示词或转写不是本轮固定输入")
            try:
                parsed = AIClipAnalysisResult.model_validate(result)
            except ValueError:
                fail("对照结果结构无效")
            if any(c.decision is None for c in parsed.clips):
                fail("试验必须使用明确决定的分析结果")
            for clip in parsed.clips:
                original = clip.quality_evidence.get("ai_original_decision")
                if (
                    not isinstance(original, dict)
                    or original.get("decision") != clip.decision
                    or original.get("start_time") != clip.start_time
                    or original.get("end_time") != clip.end_time
                ):
                    fail("对照决定或边界与 AI 原始意见不一致")
                try:
                    validate_decisions(
                        {"clips": [original]},
                        {original.get("source_id"): rows},
                        final=True,
                        catalog_ids={
                            c.quality_evidence.get("source_id") for c in parsed.clips
                        },
                    )
                except (AIAnalysisError, KeyError, TypeError):
                    fail("对照结果的证据校验失败")
            required_samples.update(
                f"{side}:{c.clip_id}" for c in parsed.clips if c.decision == "publish"
            )
        if not required_samples:
            required_samples.add("zero-usable")
        reviewed = [s["sample_id"] for s in material["samples"]]
        if len(reviewed) != len(set(reviewed)) or set(reviewed) != required_samples:
            fail("必须逐条覆盖两组全部可出片样本，不得只挑选改善的片段")


def require_accepted_trial(connection, report_id, changes):
    from app.services.weekly_review_service import fail

    if len(changes) != 1 or len(changes[0].get("suggestion_indexes", [])) != 1:
        fail("每轮只能应用一个经过对照审片的明确改动")
    trial = connection.execute(
        "SELECT id FROM content_rule_trials WHERE report_id=? AND change_hash=? AND status='accepted'",
        (report_id, change_hash(changes[0])),
    ).fetchone()
    if not trial:
        fail("请先完成固定素材试验和逐条内容审核；候选数量增加不代表改进")


def list_trials(report_id):
    with get_connection() as connection:
        return [
            dict(r)
            for r in connection.execute(
                "SELECT * FROM content_rule_trials WHERE report_id=? ORDER BY created_at DESC",
                (report_id,),
            )
        ]
