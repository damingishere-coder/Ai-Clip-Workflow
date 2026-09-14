"""Nullable attribution only; historical feedback remains unchanged."""

import hashlib
import sqlite3

VERSION = "20260915_01_human_review"
NAME = "人工审片与初始候选快照关联"
STATEMENTS = (
    "ALTER TABLE clip_feedback ADD COLUMN analysis_candidate_key TEXT",
    "ALTER TABLE clip_feedback ADD COLUMN observation_sha256 TEXT",
    "CREATE INDEX idx_feedback_run_candidate ON clip_feedback(analysis_run_id,analysis_candidate_key,created_at)",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE name='clip_feedback'").fetchone()) and "analysis_candidate_key" not in {r[1] for r in connection.execute("PRAGMA table_info(clip_feedback)")}


def apply(connection):
    for statement in STATEMENTS:
        connection.execute(statement)


def verify(connection):
    columns = {r[1]: r for r in connection.execute("PRAGMA table_info(clip_feedback)")}
    for name in ("analysis_candidate_key", "observation_sha256"):
        if name not in columns or columns[name][3:5] != (0, None):
            raise ValueError("人工审片归因字段必须可空，不回填历史")
    index = connection.execute("SELECT sql FROM sqlite_master WHERE name='idx_feedback_run_candidate'").fetchone()
    if not index or index[0] != STATEMENTS[-1]:
        raise ValueError("人工审片查询索引不一致")
