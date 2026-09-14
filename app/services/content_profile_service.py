"""Version lookup and explicit task binding, using the existing DB transaction.

There is intentionally no activation/editor API. Legacy adapters only support
their audited policy; unknown revisions fail closed instead of ignoring rules.
"""

import json
import hashlib

from app.models.content_profile import ContentProfile
from app.services.content_profile_baselines import legacy_profile_baselines


_BUILTINS = {p.id: p for p in legacy_profile_baselines()}
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
    }


def _snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def freeze_new_job_payload(connection, task_id: str, job_type: str, payload: dict | None) -> dict:
    """New jobs get evidence; retries never call this and retain old ledgers."""
    result = dict(payload or {})
    if job_type not in {"ai_analysis", "auto_pipeline"}:
        return result
    from app.core.config import settings
    from app.services.ai_prompt_preset_service import get_task_ai_prompt_snapshot_with_connection
    task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        raise ValueError("任务不存在")
    prompt = get_task_ai_prompt_snapshot_with_connection(connection, task_id)
    if not prompt.get("content_profile_version_id"):
        # A new execution of an old task is known now. Do not backfill that
        # task's historical binding or any existing/queued/failed Job or Run.
        version, profile = active_profile(connection, task["selection_profile"] or "general")
        prompt.update(content_profile_version_id=version["id"], content_profile_sha256=profile.content_hash(),
                      content_profile_json=profile.canonical_json())
    snapshot = {
        "prompt": prompt,
        "selection": {key: task[key] for key in SELECTION_FIELDS},
        "provider": (str(result.get("provider") or settings.ai_default_provider)).lower(),
    }
    result[JOB_SNAPSHOT_KEY] = {"sha256": _snapshot_hash(snapshot), "snapshot": snapshot}
    return result


def read_job_snapshot(job: dict) -> dict | None:
    payload = job.get("payload_json") or {}
    if JOB_SNAPSHOT_KEY not in payload:
        return None
    evidence = payload[JOB_SNAPSHOT_KEY]
    if not isinstance(evidence, dict) or not isinstance(evidence.get("snapshot"), dict):
        raise ValueError("Job 策略快照损坏，拒绝回退到当前任务规则")
    snapshot = evidence["snapshot"]
    if evidence.get("sha256") != _snapshot_hash(snapshot):
        raise ValueError("Job 策略快照哈希不一致")
    if not isinstance(snapshot.get("selection"), dict) or not isinstance(snapshot.get("prompt"), dict) or not snapshot.get("provider"):
        raise ValueError("Job 策略快照字段不完整")
    analyzer_key(snapshot["selection"], snapshot["prompt"])
    from app.db.database import get_connection
    with get_connection() as connection:
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
    return snapshot
