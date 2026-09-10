"""归档旧康熙入口，保留正文、任务绑定与不可变历史。"""

import hashlib
import sqlite3

VERSION = "20260908_01_prompt_archive"
NAME = "归档重复康熙提示词入口"
DDL = "ALTER TABLE ai_prompt_presets ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1))"
ARCHIVE_SQL = "UPDATE ai_prompt_presets SET is_archived=1, is_default=0 WHERE id='preset_004'"
CHECKSUM = hashlib.sha256((DDL + "\n" + ARCHIVE_SQL).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        return "is_archived" not in {r[1] for r in connection.execute("PRAGMA table_info(ai_prompt_presets)")}


def apply(connection):
    if "is_archived" not in {r[1] for r in connection.execute("PRAGMA table_info(ai_prompt_presets)")}:
        connection.execute(DDL)
    connection.execute(ARCHIVE_SQL)


def verify(connection):
    connection.execute("SELECT is_archived FROM ai_prompt_presets LIMIT 0")
    row = connection.execute("SELECT is_archived, is_default FROM ai_prompt_presets WHERE id='preset_004'").fetchone()
    if row is not None and tuple(row) != (1, 0):
        raise ValueError("4 号旧康熙方案必须保持归档且非默认")
