"""Freeze new cut input and output evidence without rewriting historical runs."""
import hashlib
import sqlite3

VERSION = "20260915_08_cut_evidence"
NAME = "切片执行证据"
STATEMENTS = (
    """CREATE TABLE cut_run_evidence (
        cut_run_id TEXT PRIMARY KEY REFERENCES cut_runs(id) ON DELETE CASCADE,
        task_id TEXT NOT NULL REFERENCES tasks(id),
        evidence_json TEXT NOT NULL, evidence_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_cut_evidence_task ON cut_run_evidence(task_id,cut_run_id)",
    "CREATE TRIGGER immutable_cut_evidence BEFORE UPDATE ON cut_run_evidence BEGIN SELECT RAISE(ABORT,'Cut evidence is immutable'); END",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True) as c:
        return not c.execute("SELECT 1 FROM sqlite_master WHERE name='cut_run_evidence'").fetchone()


def apply(connection):
    for statement in STATEMENTS:
        connection.execute(statement)


def verify(connection):
    for name, statement in zip(("cut_run_evidence", "idx_cut_evidence_task", "immutable_cut_evidence"), STATEMENTS, strict=True):
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if not row or row[0] != statement:
            raise ValueError("切片执行证据约束不一致")
