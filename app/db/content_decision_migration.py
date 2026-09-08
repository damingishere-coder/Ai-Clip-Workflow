"""新增决策字段，历史值保持 NULL；基线恢复必须由独立显式操作执行。"""

import hashlib
import sqlite3

VERSION = "20260908_02_content_decisions"
NAME = "内容决定数量及复盘试验审核"
STATEMENTS = (
    "ALTER TABLE tasks ADD COLUMN selection_count_mode TEXT NOT NULL DEFAULT 'legacy' CHECK(selection_count_mode IN ('legacy','content'))",
    "ALTER TABLE clip_candidates ADD COLUMN decision TEXT CHECK(decision IN ('publish','review','reject') OR decision IS NULL)",
    "ALTER TABLE clip_candidates ADD COLUMN decision_reason TEXT",
    "ALTER TABLE clip_candidates ADD COLUMN review_issues_json TEXT",
    "ALTER TABLE clip_candidates ADD COLUMN decision_confirmed INTEGER NOT NULL DEFAULT 0",
    """CREATE TABLE prompt_change_audits (
        id TEXT PRIMARY KEY, preset_id TEXT NOT NULL REFERENCES ai_prompt_presets(id),
        before_prompt TEXT NOT NULL, after_prompt TEXT NOT NULL,
        previous_application_id TEXT, reason TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE content_rule_trials (
        id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES weekly_content_reports(id),
        change_index INTEGER NOT NULL, change_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', material_json TEXT NOT NULL DEFAULT '[]',
        review_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, reviewed_at TEXT
    )""",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        return "selection_count_mode" not in {
            r[1] for r in connection.execute("PRAGMA table_info(tasks)")
        }


def apply(connection):
    for statement in STATEMENTS:
        connection.execute(statement)


def verify(connection):
    connection.execute("SELECT selection_count_mode FROM tasks LIMIT 0")
    connection.execute(
        "SELECT decision, decision_reason, review_issues_json, decision_confirmed FROM clip_candidates LIMIT 0"
    )
    connection.execute("SELECT * FROM prompt_change_audits LIMIT 0")
    connection.execute("SELECT * FROM content_rule_trials LIMIT 0")
