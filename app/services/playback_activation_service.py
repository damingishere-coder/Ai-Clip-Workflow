"""Explicit, transactional activation/rollback. No startup or import side effects."""

from datetime import datetime, timezone
import hashlib

from app.services.content_profile_definitions import playback_comedy_profile
from app.services.content_profile_service import active_profile, registered_profile
from app.services.ai.playback_comedy_policy import playback_rules
from app.services.ai_prompt_preset_service import ensure_ai_prompt_version_with_connection


def preview(connection, *, target="playback"):
    if target not in {"playback", "legacy"}:
        raise ValueError("目标必须为playback或legacy")
    current, old = active_profile(connection, "variety_comedy")
    new = playback_comedy_profile() if target == "playback" else registered_profile("variety_comedy")
    return {"current_version_id": current["id"], "current_sha256": old.content_hash(),
            "target": target, "target_sha256": new.content_hash(), "target_rules_version": new.rules_version,
            "target_prompt_preset_id": new.prompt_preset_id,
            "target_prompt_sha256": hashlib.sha256(playback_rules().encode()).hexdigest() if target == "playback" else None,
            "already_active": old == new, "scope": "new_tasks_only_existing_snapshots_unchanged",
            "weights": {d.id: d.weight for d in new.scoring.dimensions}, "selection": new.selection.model_dump(mode="json")}


def apply(connection, *, expected_sha256, target="playback"):
    """Caller owns BEGIN IMMEDIATE/commit/rollback, including all Prompt writes."""
    if not connection.in_transaction:
        raise ValueError("必须在事务内启用规则")
    before = preview(connection, target=target)
    if before["current_sha256"] != expected_sha256:
        raise ValueError("当前正式规则已改变，请重新预览，未执行覆盖")
    new = playback_comedy_profile() if target == "playback" else registered_profile("variety_comedy")
    now = datetime.now(timezone.utc).isoformat()
    if target == "playback":
        prompt = playback_rules()
        preset = connection.execute("SELECT prompt_text,is_archived FROM ai_prompt_presets WHERE id=?", (new.prompt_preset_id,)).fetchone()
        if preset:
            if preset[0].strip() != prompt or preset[1]:
                raise ValueError("专用Prompt已被修改或归档，不能覆盖用户内容")
        else:
            slot = connection.execute("SELECT COALESCE(MAX(slot),0)+1 FROM ai_prompt_presets").fetchone()[0]
            connection.execute("""INSERT INTO ai_prompt_presets(id,slot,name,prompt_text,is_default,is_archived,created_at,updated_at)
                VALUES(?,?,?,?,0,0,?,?)""", (new.prompt_preset_id, slot, "综艺·播放目标 v1", prompt, now, now))
        ensure_ai_prompt_version_with_connection(connection, preset_id=new.prompt_preset_id,
                                                preset_name="综艺·播放目标 v1", prompt_text=prompt, now=now)
    version = connection.execute("SELECT id,config_json FROM content_profile_versions WHERE profile_id=? AND config_sha256=?",
                                 (new.id, new.content_hash())).fetchone()
    if version and version[1] != new.canonical_json():
        raise ValueError("目标版本证据不一致")
    if not version:
        if target == "legacy":
            raise ValueError("历史版本缺失，不能凭空重建回滚证据")
        number = connection.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM content_profile_versions WHERE profile_id=?", (new.id,)).fetchone()[0]
        version_id = f"profile-{new.id}-{new.content_hash()[:20]}"
        connection.execute("""INSERT INTO content_profile_versions(id,profile_id,version_number,config_json,config_sha256,rules_version,created_at)
            VALUES(?,?,?,?,?,?,?)""", (version_id, new.id, number, new.canonical_json(), new.content_hash(), new.rules_version, now))
    else:
        version_id = version[0]
    connection.execute("UPDATE content_profiles SET current_version_id=? WHERE id=? AND current_version_id=?",
                       (version_id, new.id, before["current_version_id"]))
    after = preview(connection, target=target)
    if not after["already_active"]:
        raise ValueError("启用后校验失败")
    return {"before": before, "after": after}
