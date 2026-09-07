"""Versioned weekly review storage; no AI calls or runtime work during migration."""

import hashlib
import sqlite3

VERSION = "20260907_01_weekly_review"
NAME = "三条周复盘与生成规则应用记录"
STATEMENTS = (
    """CREATE TABLE weekly_content_reports (
        id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES publish_accounts(id),
        week_key TEXT NOT NULL, revision INTEGER NOT NULL, evidence_hash TEXT NOT NULL,
        status TEXT NOT NULL, evidence_json TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}',
        error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, started_at TEXT,
        expires_at TEXT, finished_at TEXT,
        UNIQUE(account_id, week_key, revision)
    )""",
    """CREATE UNIQUE INDEX uq_weekly_report_inflight ON weekly_content_reports(account_id)
        WHERE status IN ('queued', 'running')""",
    """CREATE TABLE content_rule_applications (
        id TEXT PRIMARY KEY, report_id TEXT NOT NULL UNIQUE REFERENCES weekly_content_reports(id),
        state TEXT NOT NULL, changes_json TEXT NOT NULL, created_at TEXT NOT NULL,
        reverted_at TEXT, decision TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE content_rule_heads (
        preset_id TEXT PRIMARY KEY REFERENCES ai_prompt_presets(id),
        copy_rules TEXT NOT NULL DEFAULT '', application_id TEXT REFERENCES content_rule_applications(id)
    )""",
    """CREATE TABLE task_generation_rules (
        task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
        preset_id TEXT NOT NULL REFERENCES ai_prompt_presets(id),
        prompt_text TEXT NOT NULL, copy_rules TEXT NOT NULL DEFAULT '',
        application_id TEXT REFERENCES content_rule_applications(id),
        frozen_at TEXT NOT NULL
    )""",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def requires_backup(path) -> bool:
    if not path.is_file():
        return False
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='weekly_content_reports'"
            ).fetchone()
            is None
        )


def apply(connection):
    for statement in STATEMENTS:
        connection.execute(statement)
    # Freeze existing tasks as-is, without claiming historical use of an improvement.
    connection.execute("""
        INSERT INTO task_generation_rules(task_id, preset_id, prompt_text, frozen_at)
        SELECT t.id, p.id, p.prompt_text, datetime('now') FROM tasks t
        JOIN ai_prompt_presets p ON p.id=COALESCE(t.ai_prompt_preset_id, 'preset_001')
    """)


def verify(connection):
    for table in (
        "weekly_content_reports",
        "content_rule_applications",
        "content_rule_heads",
        "task_generation_rules",
    ):
        connection.execute(f"SELECT * FROM {table} LIMIT 0")
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='uq_weekly_report_inflight'"
    ).fetchone():
        raise ValueError("周复盘任务唯一索引缺失")
