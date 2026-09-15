"""Frozen trial evidence and explicit policy decisions; legacy rows stay null."""

import hashlib
import sqlite3

VERSION = "20260915_05_challenger_experiments"
NAME = "Challenger 作品实验与人工策略启用"
COLUMNS = (
    "challenger_id TEXT REFERENCES content_strategy_challengers(id)",
    "strategy_json TEXT", "strategy_sha256 TEXT",
    "decision_evidence_json TEXT", "decision_evidence_sha256 TEXT",
)
STATEMENTS = tuple(f"ALTER TABLE content_improvement_experiments ADD COLUMN {column}" for column in COLUMNS) + (
    "CREATE UNIQUE INDEX idx_experiment_challenger ON content_improvement_experiments(challenger_id) WHERE challenger_id IS NOT NULL",
    """CREATE TRIGGER immutable_experiment_strategy BEFORE UPDATE OF
        challenger_id,strategy_json,strategy_sha256,baseline_json,account_id,primary_metric,primary_direction,guardrail_metrics_json,
        target_sample_size,minimum_baseline_size,minimum_weeks ON content_improvement_experiments
        WHEN OLD.challenger_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'Experiment strategy is immutable'); END""",
    """CREATE TRIGGER immutable_experiment_decision BEFORE UPDATE OF decision_evidence_json,decision_evidence_sha256,
        decision,status,completed_at ON content_improvement_experiments
        WHEN OLD.challenger_id IS NOT NULL AND OLD.decision_evidence_json IS NOT NULL
        BEGIN SELECT RAISE(ABORT,'Experiment decision is immutable'); END""",
    """CREATE TABLE content_policy_events (
        id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES content_improvement_experiments(id),
        challenger_id TEXT NOT NULL REFERENCES content_strategy_challengers(id),
        action TEXT NOT NULL CHECK(action IN ('activate','rollback')),
        before_prompt_version_id TEXT NOT NULL REFERENCES ai_prompt_versions(id),
        after_prompt_version_id TEXT NOT NULL REFERENCES ai_prompt_versions(id),
        request_key TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
        evidence_json TEXT NOT NULL, evidence_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_policy_event_experiment ON content_policy_events(experiment_id,created_at)",
    "CREATE TRIGGER immutable_policy_event BEFORE UPDATE ON content_policy_events BEGIN SELECT RAISE(ABORT,'Policy event is immutable'); END",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        columns = {r[1] for r in connection.execute("PRAGMA table_info(content_improvement_experiments)")}
        return bool(columns) and "challenger_id" not in columns


def apply(connection):
    for statement in STATEMENTS:
        connection.execute(statement)


def verify(connection):
    columns = {r[1]: r for r in connection.execute("PRAGMA table_info(content_improvement_experiments)")}
    if any((name := column.split()[0]) not in columns or columns[name][2] != "TEXT" or columns[name][3] for column in COLUMNS):
        raise ValueError("实验可空证据字段不兼容")
    if not any(r[2:5] == ("content_strategy_challengers", "challenger_id", "id") for r in connection.execute("PRAGMA foreign_key_list(content_improvement_experiments)")):
        raise ValueError("实验策略外键缺失")
    names = ("idx_experiment_challenger", "immutable_experiment_strategy", "immutable_experiment_decision",
             "content_policy_events", "idx_policy_event_experiment", "immutable_policy_event")
    for name, statement in zip(names, STATEMENTS[len(COLUMNS):], strict=True):
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if not row or row[0] != statement:
            raise ValueError("实验策略索引或证据约束不一致")
