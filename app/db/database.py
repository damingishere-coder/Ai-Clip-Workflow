import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.config import settings


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.tasks_dir.mkdir(parents=True, exist_ok=True)

    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'upload',
                source_path TEXT,
                platform TEXT NOT NULL DEFAULT '通用',
                status TEXT NOT NULL DEFAULT '待提交视频',
                progress INTEGER NOT NULL DEFAULT 0,
                max_clip_minutes INTEGER NOT NULL DEFAULT 2,
                target_clip_count INTEGER NOT NULL DEFAULT 8,
                ai_preference TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clip_candidates (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                title TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                summary TEXT,
                reason TEXT,
                spread_value TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );
            """
        )
        connection.commit()
