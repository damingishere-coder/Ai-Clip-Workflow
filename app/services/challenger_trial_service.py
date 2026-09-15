"""Explicit task admission for archived Challenger prompts; no production head changes."""

import json

from app.db.database import get_connection
from app.services.content_challenger_service import read_challenger_with_connection
from app.services.content_profile_service import active_profile


def prepare_trial(connection, payload):
    if not payload.challenger_id:
        if payload.challenger_sha256 or payload.confirm_challenger:
            raise ValueError("试验确认缺少 Challenger")
        return None
    if not payload.confirm_challenger:
        raise ValueError("请明确确认使用未验证的 Challenger 创建试验任务")
    if payload.auto_mode:
        raise ValueError("Challenger 试验先逐步处理并人工审核，不能启动全自动流水线")
    draft = read_challenger_with_connection(connection, payload.challenger_id)
    if draft["evidence_sha256"] != payload.challenger_sha256:
        raise ValueError("Challenger 与预览证据不一致，请重新核对")
    if draft["status"] not in {"draft", "trial"}:
        raise ValueError("此 Challenger 已停止创建新的试验任务")
    champion = connection.execute("SELECT prompt_text,is_archived FROM ai_prompt_presets WHERE id=?",
        (draft["evidence"]["baseline"]["preset_id"],)).fetchone()
    if not champion or champion[1] or champion[0].strip() != draft["evidence"]["baseline"]["prompt_text"]:
        raise ValueError("正式 Prompt 已改变，旧草稿基线已过期，请重新核对并创建草稿")
    version, profile = active_profile(connection, payload.selection_profile)
    if (version["id"] != draft["profile_version_id"]
            or profile.content_hash() != draft["evidence"]["baseline"]["profile_sha256"]):
        raise ValueError("试验任务必须使用 Challenger 冻结的 Content Profile 版本")
    if payload.ai_prompt_preset_id != draft["challenger_preset_id"]:
        raise ValueError("试验任务必须使用 Challenger 冻结的 Prompt")
    preset = connection.execute("SELECT prompt_text FROM ai_prompt_presets WHERE id=?", (payload.ai_prompt_preset_id,)).fetchone()
    if not preset or preset[0].strip() != draft["evidence"]["challenger_prompt_text"]:
        raise ValueError("Challenger Prompt 正文已不匹配")
    return draft


def bind_trial(connection, task_id, draft, now):
    if draft is None:
        return
    connection.execute("UPDATE task_generation_rules SET challenger_id=?,challenger_sha256=? WHERE task_id=?",
        (draft["id"], draft["evidence_sha256"], task_id))
    connection.execute("UPDATE content_strategy_challengers SET status='trial',updated_at=? WHERE id=? AND status='draft'", (now, draft["id"]))


def task_binding(connection, task_id):
    row = connection.execute("SELECT challenger_id,challenger_sha256 FROM task_generation_rules WHERE task_id=?", (task_id,)).fetchone()
    if not row or (row[0] is None and row[1] is None):
        return None
    if not row[0] or not row[1]:
        raise ValueError("试验任务策略绑定不完整")
    return {"id": row[0], "sha256": row[1]}


def validate_trial_snapshot(connection, task_id, prompt, *, attaching=False):
    binding = task_binding(connection, task_id)
    marker = prompt.get("challenger")
    if binding is None:
        if marker is not None:
            raise ValueError("普通任务不能冒用 Challenger 试验标记")
        return None
    if not attaching and marker != binding:
        raise ValueError("试验 Job 的 Challenger 证据缺失或不一致")
    draft = read_challenger_with_connection(connection, binding["id"])
    if (draft["evidence_sha256"] != binding["sha256"]
            or prompt.get("id") != draft["challenger_preset_id"]
            or prompt.get("prompt_version_id") != draft["challenger_prompt_version_id"]
            or prompt.get("prompt_sha256") != draft["evidence"]["challenger_prompt_sha256"]
            or prompt.get("prompt_text", "").strip() != draft["evidence"]["challenger_prompt_text"]
            or prompt.get("content_profile_version_id") != draft["profile_version_id"]
            or prompt.get("content_profile_sha256") != draft["evidence"]["baseline"]["profile_sha256"]):
        raise ValueError("试验实际 Prompt/Profile 与 Challenger 不一致，已阻止分析")
    return binding


def task_trial(task_id):
    with get_connection() as connection:
        binding = task_binding(connection, task_id)
        if not binding:
            return None
        draft = read_challenger_with_connection(connection, binding["id"])
        if binding["sha256"] != draft["evidence_sha256"]:
            raise ValueError("试验任务与 Challenger 证据不一致")
        return draft


def validate_trial_run(connection, task_id, run):
    """Validate stored facts on commit/recovery; legacy Runs retain their semantics."""
    binding = task_binding(connection, task_id)
    if not binding:
        return
    payload = json.loads(run.get("analysis_payload_json") or "{}")
    meta = payload.get("analysis_meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict) or meta.get("challenger") != binding or run.get("task_id") != task_id:
        raise ValueError("试验 AI Run 的 Challenger 标记缺失或不一致")
    version = connection.execute("SELECT prompt_text FROM ai_prompt_versions WHERE id=?", (run.get("prompt_version_id"),)).fetchone()
    prompt = {"id": run.get("ai_prompt_preset_id"), "prompt_version_id": run.get("prompt_version_id"),
        "prompt_sha256": run.get("prompt_text_sha256"), "prompt_text": version[0] if version else "",
        "content_profile_version_id": run.get("content_profile_version_id"),
        "content_profile_sha256": run.get("content_profile_sha256"), "challenger": meta["challenger"]}
    validate_trial_snapshot(connection, task_id, prompt)
    if any(meta.get(field) != prompt[field] for field in ("content_profile_version_id", "content_profile_sha256")):
        raise ValueError("试验 AI Run 的 Profile 元数据与实际版本不一致")
