"""Immutable Challenger strategy evidence, using existing Prompt versions."""

import hashlib
import sqlite3

VERSION = "20260915_03_content_challengers"
NAME = "人工 Challenger 草稿与策略版本证据"
STATEMENTS = (
    """CREATE TABLE content_strategy_challengers (
        id TEXT PRIMARY KEY,
        report_id TEXT NOT NULL REFERENCES content_intelligence_reports(id),
        report_sha256 TEXT NOT NULL CHECK(length(report_sha256)=64),
        profile_version_id TEXT NOT NULL REFERENCES content_profile_versions(id),
        champion_prompt_version_id TEXT NOT NULL REFERENCES ai_prompt_versions(id),
        challenger_prompt_version_id TEXT NOT NULL REFERENCES ai_prompt_versions(id),
        challenger_preset_id TEXT NOT NULL UNIQUE REFERENCES ai_prompt_presets(id),
        request_key TEXT NOT NULL UNIQUE,
        request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
        evidence_json TEXT NOT NULL,
        evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
        status TEXT NOT NULL DEFAULT 'draft',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_challenger_report_created ON content_strategy_challengers(report_id,created_at)",
    """CREATE TRIGGER immutable_challenger_strategy BEFORE UPDATE OF
        report_id,report_sha256,profile_version_id,champion_prompt_version_id,challenger_prompt_version_id,
        challenger_preset_id,request_key,request_sha256,evidence_json,evidence_sha256,created_at
        ON content_strategy_challengers BEGIN SELECT RAISE(ABORT,'Challenger strategy evidence is immutable'); END""",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return "ai_analysis_runs" in names and "content_strategy_challengers" not in names


def apply(connection):
    for statement in STATEMENTS:
        connection.execute(statement)


def verify(connection):
    names = ("content_strategy_challengers", "idx_challenger_report_created", "immutable_challenger_strategy")
    for name, statement in zip(names, STATEMENTS, strict=True):
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if not row or row[0] != statement:
            raise ValueError("Challenger 策略表、索引或不可变约束不一致")
