"""Version lookup and explicit task binding, using the existing DB transaction.

There is intentionally no activation/editor API. Legacy adapters only support
their audited policy; unknown revisions fail closed instead of ignoring rules.
"""

import json
import hashlib
from contextlib import nullcontext

from app.models.content_profile import ContentProfile
from app.services.content_profile_definitions import builtin_profiles


_BUILTINS = {p.id: p for p in builtin_profiles()}
JOB_SNAPSHOT_KEY = "generation_snapshot_v1"
SELECTION_FIELDS = (
    "selection_profile", "candidate_clip_count", "final_clip_target", "max_clip_duration",
    "highlight_density_per_hour", "highlight_total_limit", "ai_preference",
)


def registered_profile(profile_id: str) -> ContentProfile:
    try:
        return _BUILTINS[profile_id]
    except KeyError as exc:
        raise ValueError(f"不支持的 Content Profile：{profile_id}") from exc


def validate_content_candidate_limit(profile_id: str, candidate_count: int) -> None:
    profile = registered_profile(profile_id)
    if profile.analyzer_key == "content" and not 1 <= candidate_count <= profile.selection.candidate_pool_max:
        raise ValueError(f"{profile.name}的候选池必须为 1–{profile.selection.candidate_pool_max} 条")


def _version(connection, version_id: str) -> tuple[dict, ContentProfile]:
    row = connection.execute("SELECT * FROM content_profile_versions WHERE id=?", (version_id,)).fetchone()
    if row is None:
        raise ValueError("Content Profile 版本不存在，不能猜测或替换历史策略")
    version = dict(row)
    profile = ContentProfile.model_validate_json(version["config_json"])
    if (profile.id != version["profile_id"] or profile.content_hash() != version["config_sha256"]
            or profile.rules_version != version["rules_version"]):
        raise ValueError("Content Profile 版本证据校验失败")
    return version, profile


def active_profile(connection, profile_id: str) -> tuple[dict, ContentProfile]:
    registered_profile(profile_id)
    head = connection.execute(
        "SELECT current_version_id FROM content_profiles WHERE id=? AND is_enabled=1", (profile_id,),
    ).fetchone()
    if not head or not head[0]:
        raise ValueError("Content Profile 尚未启用")
    version, profile = _version(connection, head[0])
    if profile.id != profile_id:
        raise ValueError("Content Profile 正式版本指向不一致")
    _assert_supported(profile)
    return version, profile


def list_content_profiles() -> list[dict]:
    from app.db.database import get_connection
    with get_connection() as connection:
        ids = [row[0] for row in connection.execute("SELECT id FROM content_profiles WHERE is_enabled=1 ORDER BY id")]
        return [active_profile(connection, profile_id)[1].model_dump(mode="json") for profile_id in ids]


def _assert_supported(profile: ContentProfile):
    if profile.content_hash() != registered_profile(profile.id).content_hash():
        raise ValueError("此 Profile 规则版本尚无匹配的 Analyzer，已阻止以旧算法执行新规则")


def freeze_task_profile(connection, task_id: str) -> dict:
    """Only task creation / explicit profile selection calls this; never migration."""
    task = connection.execute("SELECT selection_profile FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        raise ValueError("任务不存在")
    version, profile = active_profile(connection, task[0] or "general")
    cursor = connection.execute(
        """UPDATE task_generation_rules SET content_profile_version_id=?,
        content_profile_sha256=?,content_profile_json=? WHERE task_id=?""",
        (version["id"], profile.content_hash(), profile.canonical_json(), task_id),
    )
    if cursor.rowcount != 1:
        raise ValueError("任务 Prompt 尚未冻结，不能单独绑定 Profile")
    return {"content_profile_version_id": version["id"], "content_profile_sha256": profile.content_hash(),
            "content_profile_json": profile.canonical_json()}


def read_task_profile(connection, task_id: str) -> dict:
    row = connection.execute(
        """SELECT t.selection_profile, r.content_profile_version_id,
        r.content_profile_sha256,r.content_profile_json FROM tasks t
        LEFT JOIN task_generation_rules r ON r.task_id=t.id WHERE t.id=?""", (task_id,),
    ).fetchone()
    if not row:
        raise ValueError("任务不存在")
    evidence = {key: row[key] for key in ("content_profile_version_id", "content_profile_sha256", "content_profile_json")}
    if all(value is None for value in evidence.values()):
        return {}  # Historical unknown is never silently migrated on read.
    if not all(evidence.values()):
        raise ValueError("任务 Content Profile 快照不完整")
    version, profile = _version(connection, evidence["content_profile_version_id"])
    frozen = ContentProfile.model_validate_json(evidence["content_profile_json"])
    if profile != frozen or profile.id != row["selection_profile"] or evidence["content_profile_sha256"] != version["config_sha256"]:
        raise ValueError("任务 Content Profile 快照与版本不一致")
    _assert_supported(profile)
    return evidence


def analyzer_key(task: dict, snapshot: dict) -> str:
    profile_id = task.get("selection_profile") or "general"
    profile = registered_profile(profile_id)
    if snapshot.get("content_profile_json"):
        frozen = ContentProfile.model_validate_json(snapshot["content_profile_json"])
        if frozen.id != profile_id or frozen.content_hash() != snapshot.get("content_profile_sha256"):
            raise ValueError("分析开始时任务 Profile 已改变，拒绝混合策略")
        _assert_supported(frozen)
        profile = frozen
    return profile.analyzer_key


def analysis_profile_evidence(task: dict, snapshot: dict) -> dict:
    if not snapshot.get("content_profile_version_id"):
        return {}
    return {
        "content_profile_version_id": snapshot["content_profile_version_id"],
        "content_profile_sha256": snapshot["content_profile_sha256"],
        "content_profile": json.loads(snapshot["content_profile_json"]),
        "effective_selection": {key: task.get(key) for key in SELECTION_FIELDS},
        **({"challenger": snapshot["challenger"]} if snapshot.get("challenger") else {}),
    }


def _snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def freeze_task_provider(connection, task_id: str, provider_name: str | None) -> None:
    from app.core.config import settings
    from app.services.ai.provider_snapshot import capture
    snapshot = capture((provider_name or settings.ai_default_provider).lower())
    evidence = {"snapshot": snapshot, "sha256": _snapshot_hash(snapshot)}
    connection.execute("UPDATE task_generation_rules SET provider_snapshot_json=? WHERE task_id=?",
                       (json.dumps(evidence, ensure_ascii=False), task_id))


def _task_provider(connection, task_id: str) -> dict | None:
    row = connection.execute("SELECT provider_snapshot_json FROM task_generation_rules WHERE task_id=?", (task_id,)).fetchone()
    if not row or row[0] is None:
        return None
    evidence = json.loads(row[0])
    snapshot = evidence.get("snapshot")
    if not isinstance(snapshot, dict) or evidence.get("sha256") != _snapshot_hash(snapshot):
        raise ValueError("任务 Provider 快照损坏，不能自动替换为当前设置")
    return snapshot


def freeze_new_job_payload(connection, task_id: str, job_type: str, payload: dict | None) -> dict:
    """New jobs get evidence; retries never call this and retain old ledgers."""
    result = dict(payload or {})
    from app.services.material_batch_service import require_imported_source
    if job_type != "material_import":
        require_imported_source(connection, task_id)
    if job_type not in {"ai_analysis", "auto_pipeline"}:
        return result
    from app.services.material_batch_service import frozen_job_payload
    batch_payload = frozen_job_payload(connection, task_id, job_type, result)
    if batch_payload is not None:
        return batch_payload
    from app.core.config import settings
    from app.services.ai_prompt_preset_service import get_task_ai_prompt_snapshot_with_connection
    task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        raise ValueError("任务不存在")
    prompt = get_task_ai_prompt_snapshot_with_connection(connection, task_id)
    if prompt.get("challenger") and job_type == "auto_pipeline":
        raise ValueError("Challenger 试验任务请逐步处理并人工审核，不能启动全自动流水线")
    if not prompt.get("content_profile_version_id"):
        # A new execution of an old task is known now. Do not backfill that
        # task's historical binding or any existing/queued/failed Job or Run.
        version, profile = active_profile(connection, task["selection_profile"] or "general")
        prompt.update(content_profile_version_id=version["id"], content_profile_sha256=profile.content_hash(),
                      content_profile_json=profile.canonical_json())
    from app.services.ai.provider_snapshot import capture
    # An explicit analysis button chooses its displayed current Provider. Auto
    # jobs inherit the task's creation-time provider without changing settings.
    provider = str(result.get("provider") or "").lower()
    identity = capture(provider) if provider else _task_provider(connection, task_id)
    if identity is None:
        identity = capture(settings.ai_default_provider.lower())
    snapshot = {
        "prompt": prompt,
        "selection": {key: task[key] for key in SELECTION_FIELDS},
        "provider": identity["name"],
        "provider_identity": identity,
    }
    from app.services.visual_policy_service import task_visual_policy
    snapshot["visual_policy"] = task_visual_policy(connection, task_id)
    if task["selection_profile"] == "variety_comedy":
        from app.services.clip_feedback_service import list_recent_feedback_context_with_connection
        snapshot["feedback_context"] = {
            "source": "clip_feedback", "query_version": "recent-final-decisions-v1",
            "items": list_recent_feedback_context_with_connection(connection, "variety_comedy", 20),
        }
    result[JOB_SNAPSHOT_KEY] = {"sha256": _snapshot_hash(snapshot), "snapshot": snapshot}
    return result


def read_job_snapshot(job: dict, *, connection=None) -> dict | None:
    payload = job.get("payload_json") or {}
    from app.services.material_batch_service import task_item
    from app.db.database import get_connection
    with (nullcontext(connection) if connection is not None else get_connection()) as batch_connection:
        batch = task_item(batch_connection, job.get("task_id"))
        if batch and payload.get(JOB_SNAPSHOT_KEY) != batch["generation"]:
            raise ValueError("批次 Job 缺少冻结策略或策略已变化，不能回退当前配置")
    if JOB_SNAPSHOT_KEY not in payload:
        if job.get("task_id"):
            from app.db.database import get_connection
            from app.services.challenger_trial_service import task_binding
            with (nullcontext(connection) if connection is not None else get_connection()) as trial_connection:
                if task_binding(trial_connection, job["task_id"]):
                    raise ValueError("试验 Job 缺少策略快照，不能作为历史 Job 回退")
        return None
    evidence = payload[JOB_SNAPSHOT_KEY]
    if not isinstance(evidence, dict) or not isinstance(evidence.get("snapshot"), dict):
        raise ValueError("Job 策略快照损坏，拒绝回退到当前任务规则")
    snapshot = evidence["snapshot"]
    if evidence.get("sha256") != _snapshot_hash(snapshot):
        raise ValueError("Job 策略快照哈希不一致")
    if not isinstance(snapshot.get("selection"), dict) or not isinstance(snapshot.get("prompt"), dict) or not snapshot.get("provider"):
        raise ValueError("Job 策略快照字段不完整")
    if "visual_policy" in snapshot:
        from app.services.visual_policy_service import validate_policy
        validate_policy(snapshot["visual_policy"])
    if "feedback_context" in snapshot:
        feedback = snapshot["feedback_context"]
        if (snapshot["selection"].get("selection_profile") != "variety_comedy"
                or not isinstance(feedback, dict) or feedback.get("source") != "clip_feedback"
                or feedback.get("query_version") != "recent-final-decisions-v1"
                or not isinstance(feedback.get("items"), list) or len(feedback["items"]) > 20
                or any(not isinstance(item, dict) for item in feedback["items"])):
            raise ValueError("Job 审片反馈快照损坏，不能替换为当前反馈")
    analyzer_key(snapshot["selection"], snapshot["prompt"])
    from app.db.database import get_connection
    with (nullcontext(connection) if connection is not None else get_connection()) as connection:
        prompt = snapshot["prompt"]
        version, profile = _version(connection, prompt.get("content_profile_version_id"))
        if profile.canonical_json() != prompt.get("content_profile_json") or version["config_sha256"] != prompt.get("content_profile_sha256"):
            raise ValueError("Job Profile 版本引用不一致")
        prompt_version = connection.execute(
            "SELECT preset_id,prompt_sha256 FROM ai_prompt_versions WHERE id=?", (prompt.get("prompt_version_id"),),
        ).fetchone()
        actual_hash = hashlib.sha256(str(prompt.get("prompt_text") or "").strip().encode("utf-8")).hexdigest()
        if not prompt_version or tuple(prompt_version) != (prompt.get("id"), actual_hash) or prompt.get("prompt_sha256") != actual_hash:
            raise ValueError("Job Prompt 版本引用不一致")
        from app.services.challenger_trial_service import validate_trial_snapshot
        validate_trial_snapshot(connection, job["task_id"], prompt)
    return snapshot
