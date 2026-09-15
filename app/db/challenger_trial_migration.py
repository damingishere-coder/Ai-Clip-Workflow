"""Optional task binding; legacy tasks and job payloads remain untouched."""

import hashlib
import sqlite3

VERSION = "20260915_04_challenger_trials"
NAME = "显式 Challenger 试验任务绑定"
STATEMENTS = (
    "ALTER TABLE task_generation_rules ADD COLUMN challenger_id TEXT REFERENCES content_strategy_challengers(id)",
    """ALTER TABLE task_generation_rules ADD COLUMN challenger_sha256 TEXT
        CHECK((challenger_id IS NULL AND challenger_sha256 IS NULL) OR
              (challenger_id IS NOT NULL AND challenger_sha256 IS NOT NULL AND length(challenger_sha256)=64))""",
    "CREATE INDEX idx_task_challenger ON task_generation_rules(challenger_id)",
    """CREATE TRIGGER immutable_task_challenger BEFORE UPDATE OF challenger_id,challenger_sha256
        ON task_generation_rules WHEN OLD.challenger_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT,'Trial binding is immutable'); END""",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        columns = {r[1] for r in connection.execute("PRAGMA table_info(task_generation_rules)")}
        return bool(columns) and "challenger_id" not in columns


def apply(connection):
    for statement in STATEMENTS:
        connection.execute(statement)


def verify(connection):
    columns = {r[1]: r for r in connection.execute("PRAGMA table_info(task_generation_rules)")}
    if any(name not in columns or columns[name][2] != "TEXT" or columns[name][3] for name in ("challenger_id", "challenger_sha256")):
        raise ValueError("试验任务字段不兼容")
    if not any(r[2:5] == ("content_strategy_challengers", "challenger_id", "id") for r in connection.execute("PRAGMA foreign_key_list(task_generation_rules)")):
        raise ValueError("试验任务版本外键缺失")
    for name, statement in zip(("idx_task_challenger", "immutable_task_challenger"), STATEMENTS[2:], strict=True):
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if not row or row[0] != statement:
            raise ValueError("试验任务索引或不可变约束不一致")
