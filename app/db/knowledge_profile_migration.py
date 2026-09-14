"""Append the first knowledge policy and Prompt without replacing user presets."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

VERSION = "20260914_03_knowledge_provider"
NAME = "知识模板与 Provider 快照"
SEED = json.loads(Path(__file__).with_name("knowledge_profile_seed_v1.json").read_text(encoding="utf-8"))
CHECKSUM = hashlib.sha256(("knowledge-seed-v1:provider-snapshot-column:max-slot-plus-one:" + json.dumps(SEED, sort_keys=True)).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='tasks'").fetchone():
            return False
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone():
            return True
        return not c.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone()


def apply(c):
    c.execute("ALTER TABLE task_generation_rules ADD COLUMN provider_snapshot_json TEXT")
    now = datetime.now(timezone.utc).isoformat()
    p = SEED
    version_id = f"profile-{p['profile_id']}-{p['config_sha256'][:20]}"
    # Plain INSERT makes any unexpected ID collision fail transactionally.
    c.execute("INSERT INTO content_profiles(id,created_at) VALUES (?,?)", (p["profile_id"], now))
    c.execute("""INSERT INTO content_profile_versions
        (id,profile_id,version_number,config_json,config_sha256,rules_version,created_at)
        VALUES (?,?,1,?,?,?,?)""", (version_id, p["profile_id"], p["config_json"], p["config_sha256"], p["rules_version"], now))
    c.execute("UPDATE content_profiles SET current_version_id=? WHERE id=?", (version_id, p["profile_id"]))
    slot = c.execute("SELECT COALESCE(MAX(slot),0)+1 FROM ai_prompt_presets").fetchone()[0]
    c.execute("""INSERT INTO ai_prompt_presets(id,slot,name,prompt_text,is_default,is_archived,created_at,updated_at)
        VALUES (?,?,?,?,0,0,?,?)""", (p["prompt_id"], slot, p["prompt_name"], p["prompt_text"], now, now))
    c.execute("""INSERT INTO ai_prompt_versions
        (id,preset_id,version_number,preset_name_snapshot,prompt_text,prompt_sha256,created_at)
        VALUES (?,?,1,?,?,?,?)""", (f"promptv_{p['prompt_id']}_001", p["prompt_id"], p["prompt_name"], p["prompt_text"],
        hashlib.sha256(p["prompt_text"].encode()).hexdigest(), now))


def verify(c):
    c.execute("SELECT provider_snapshot_json FROM task_generation_rules LIMIT 0")
    p = SEED
    row = c.execute("SELECT config_json,config_sha256 FROM content_profile_versions WHERE profile_id=? AND version_number=1", (p["profile_id"],)).fetchone()
    if not row or tuple(row) != (p["config_json"], p["config_sha256"]):
        raise ValueError("知识 Profile 初始版本证据不一致")
    # The mutable preset may be edited by the user later; inspect immutable v1.
    row = c.execute("SELECT prompt_text,prompt_sha256 FROM ai_prompt_versions WHERE preset_id=? AND version_number=1", (p["prompt_id"],)).fetchone()
    if not row or tuple(row) != (p["prompt_text"], hashlib.sha256(p["prompt_text"].encode()).hexdigest()):
        raise ValueError("知识 Prompt 初始版本证据不一致")
    if c.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("知识模板迁移外键校验失败")
