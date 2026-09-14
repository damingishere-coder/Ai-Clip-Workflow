"""Additive Profile evidence migration; never backfill historical attribution.

Published DDL and seed definitions are immutable migration inputs. New built-ins
or revisions must use a later migration, not modify this migration's seed.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


VERSION = "20260914_01_content_profiles"
NAME = "内容模板不可变版本与任务分析证据"
_SEED_DATA = json.loads(Path(__file__).with_name("content_profiles_seed_v1.json").read_text(encoding="utf-8"))
SEEDS = tuple((p["profile_id"], p["config_json"], p["config_sha256"], p["rules_version"]) for p in _SEED_DATA)
STATEMENTS = (
    """CREATE TABLE content_profiles (
        id TEXT PRIMARY KEY,
        current_version_id TEXT REFERENCES content_profile_versions(id),
        is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0,1)),
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE content_profile_versions (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL REFERENCES content_profiles(id),
        version_number INTEGER NOT NULL CHECK(version_number > 0),
        config_json TEXT NOT NULL,
        config_sha256 TEXT NOT NULL CHECK(length(config_sha256)=64),
        rules_version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(profile_id, version_number), UNIQUE(profile_id, config_sha256)
    )""",
    "ALTER TABLE task_generation_rules ADD COLUMN content_profile_version_id TEXT REFERENCES content_profile_versions(id)",
    "ALTER TABLE task_generation_rules ADD COLUMN content_profile_sha256 TEXT",
    "ALTER TABLE task_generation_rules ADD COLUMN content_profile_json TEXT",
    "ALTER TABLE ai_analysis_runs ADD COLUMN content_profile_version_id TEXT REFERENCES content_profile_versions(id)",
    "ALTER TABLE ai_analysis_runs ADD COLUMN content_profile_sha256 TEXT",
    "CREATE INDEX idx_ai_runs_content_profile ON ai_analysis_runs(content_profile_version_id, created_at)",
    """CREATE TRIGGER content_profile_versions_no_update BEFORE UPDATE ON content_profile_versions
        BEGIN SELECT RAISE(ABORT, 'content profile versions are immutable'); END""",
    """CREATE TRIGGER content_profile_versions_no_delete BEFORE DELETE ON content_profile_versions
        BEGIN SELECT RAISE(ABORT, 'content profile versions are immutable'); END""",
    """CREATE TRIGGER content_profile_head_same_profile BEFORE UPDATE OF current_version_id ON content_profiles
        WHEN NEW.current_version_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM content_profile_versions WHERE id=NEW.current_version_id AND profile_id=NEW.id
        ) BEGIN SELECT RAISE(ABORT, 'content profile head belongs to another profile'); END""",
)
CHECKSUM = hashlib.sha256(("\n".join(STATEMENTS) + repr(SEEDS)).encode("utf-8")).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='tasks'").fetchone():
            return False
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone():
            return True
        return not connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone()


def apply(connection):
    # The existing ledger owns BEGIN/COMMIT. No executescript (implicit commit).
    for statement in STATEMENTS:
        connection.execute(statement)
    created_at = datetime.now(timezone.utc).isoformat()
    for profile_id, config, digest, rules in SEEDS:
        version_id = f"profile-{profile_id}-{digest[:20]}"
        connection.execute("INSERT INTO content_profiles(id,created_at) VALUES (?,?)", (profile_id, created_at))
        connection.execute(
            """INSERT INTO content_profile_versions
            (id,profile_id,version_number,config_json,config_sha256,rules_version,created_at)
            VALUES (?,?,1,?,?,?,?)""", (version_id, profile_id, config, digest, rules, created_at),
        )
        connection.execute("UPDATE content_profiles SET current_version_id=? WHERE id=?", (version_id, profile_id))


def verify(connection):
    connection.execute("SELECT content_profile_version_id,content_profile_sha256,content_profile_json FROM task_generation_rules LIMIT 0")
    connection.execute("SELECT content_profile_version_id,content_profile_sha256 FROM ai_analysis_runs LIMIT 0")
    for name in ("idx_ai_runs_content_profile", "content_profile_versions_no_update", "content_profile_versions_no_delete", "content_profile_head_same_profile"):
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone():
            raise ValueError(f"内容模板迁移缺少索引或保护：{name}")
    for profile_id, config, digest, rules in SEEDS:
        row = connection.execute(
            "SELECT config_json,config_sha256,rules_version FROM content_profile_versions WHERE profile_id=? AND version_number=1",
            (profile_id,),
        ).fetchone()
        if row is None or tuple(row) != (config, digest, rules):
            raise ValueError(f"内容模板内置版本损坏：{profile_id}")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("内容模板迁移后外键检查失败")
