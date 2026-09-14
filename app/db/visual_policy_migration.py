"""仅增加默认关闭字段和可空策略证据，不回填历史版本。"""

import hashlib
import sqlite3

VERSION = "20260914_05_visual_policy"
NAME = "任务可选视觉策略"
STATEMENTS = (
    "ALTER TABLE tasks ADD COLUMN visual_enabled INTEGER NOT NULL DEFAULT 0 CHECK(visual_enabled IN (0,1))",
    "ALTER TABLE task_generation_rules ADD COLUMN visual_policy_json TEXT",
    "ALTER TABLE candidate_visual_evidence ADD COLUMN cache_cleanup_error TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX idx_visual_task ON candidate_visual_evidence(task_id,created_at,id)",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE name='tasks'").fetchone()) and "visual_enabled" not in {r[1] for r in connection.execute("PRAGMA table_info(tasks)")}


def apply(connection):
    for statement in STATEMENTS:
        connection.execute(statement)


def verify(connection):
    columns = {r[1]: r for r in connection.execute("PRAGMA table_info(tasks)")}
    if "visual_enabled" not in columns or columns["visual_enabled"][3:5] != (1, "0"):
        raise ValueError("视觉开关缺少兼容默认值")
    connection.execute("SELECT visual_policy_json FROM task_generation_rules LIMIT 0")
    connection.execute("SELECT cache_cleanup_error FROM candidate_visual_evidence LIMIT 0")
    row = connection.execute("SELECT sql FROM sqlite_master WHERE name='idx_visual_task'").fetchone()
    if not row or row[0] != STATEMENTS[-1]:
        raise ValueError("视觉任务查询索引不一致")
    if connection.execute("SELECT 1 FROM tasks WHERE visual_enabled NOT IN (0,1) LIMIT 1").fetchone():
        raise ValueError("视觉开关值无效")
