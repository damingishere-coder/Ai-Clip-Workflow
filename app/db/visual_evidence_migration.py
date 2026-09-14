"""候选视觉的可选证据与缓存引用；沿用现有 SQLite 迁移执行器。"""

import hashlib
import sqlite3


VERSION = "20260914_04_visual_evidence"
NAME = "候选视觉证据"
STATEMENTS = (
    """CREATE TABLE candidate_visual_evidence (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(id),
        workflow_job_id TEXT NOT NULL REFERENCES workflow_jobs(id),
        analysis_run_id TEXT REFERENCES ai_analysis_runs(id),
        candidate_key TEXT NOT NULL,
        input_fingerprint TEXT NOT NULL,
        request_fingerprint TEXT NOT NULL,
        request_json TEXT NOT NULL,
        cache_relative_dir TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('disabled','pending','completed','partial','unavailable')),
        call_status TEXT NOT NULL DEFAULT 'not_called'
            CHECK(call_status IN ('not_called','pending','completed','uncertain','retryable_failed')),
        evidence_json TEXT,
        evidence_sha256 TEXT,
        failure_reason TEXT NOT NULL DEFAULT '',
        pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
        cache_cleaned_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(workflow_job_id,candidate_key)
    )""",
    "CREATE INDEX idx_visual_run ON candidate_visual_evidence(analysis_run_id,candidate_key)",
    "CREATE INDEX idx_visual_cleanup ON candidate_visual_evidence(pinned,cache_cleaned_at,status,updated_at)",
    """CREATE TRIGGER immutable_visual_request BEFORE UPDATE OF
        id,task_id,workflow_job_id,candidate_key,input_fingerprint,request_fingerprint,
        request_json,cache_relative_dir,provider,model,created_at ON candidate_visual_evidence
        BEGIN SELECT RAISE(ABORT,'visual request is immutable'); END""",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


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
    for statement in STATEMENTS:
        connection.execute(statement)


def verify(connection):
    connection.execute("""SELECT id,task_id,workflow_job_id,analysis_run_id,candidate_key,
        input_fingerprint,request_fingerprint,request_json,cache_relative_dir,provider,model,
        status,call_status,evidence_json,evidence_sha256,failure_reason,pinned,cache_cleaned_at,
        created_at,updated_at FROM candidate_visual_evidence LIMIT 0""")
    for name, expected in zip(("idx_visual_run", "idx_visual_cleanup", "immutable_visual_request"), STATEMENTS[1:], strict=True):
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if not row or " ".join(str(row[0]).split()) != " ".join(expected.split()):
            raise ValueError("视觉证据索引或不可变请求保护缺失或已改变")
    if connection.execute("PRAGMA foreign_key_check(candidate_visual_evidence)").fetchall():
        raise ValueError("视觉证据外键异常")
