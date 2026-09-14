"""Frozen derived reports; source facts and production policies are untouched."""

import hashlib
import sqlite3

VERSION = "20260915_02_intelligence_reports"
NAME = "可追溯的内容经验报告"
STATEMENTS = (
    """CREATE TABLE content_intelligence_reports (
        id TEXT PRIMARY KEY,
        account_id TEXT REFERENCES publish_accounts(id),
        request_key TEXT NOT NULL UNIQUE,
        config_sha256 TEXT NOT NULL CHECK(length(config_sha256)=64),
        schema_version TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_intelligence_reports_account_created ON content_intelligence_reports(account_id,created_at)",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return "ai_analysis_runs" in names and "content_intelligence_reports" not in names


def apply(connection):
    for statement in STATEMENTS:
        connection.execute(statement)


def verify(connection):
    for statement, name in zip(STATEMENTS, ("content_intelligence_reports", "idx_intelligence_reports_account_created"), strict=True):
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if not row or row[0] != statement:
            raise ValueError("内容经验报告表或索引定义不一致")
