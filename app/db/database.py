import sqlite3
from datetime import datetime
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.config import settings


DEFAULT_AI_PROMPT_PRESET_ID = "preset_001"
DEFAULT_AI_PROMPT_PATH = settings.project_root / "prompts" / "default_ai_prompt_preset_001.txt"


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
                task_name TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'upload',
                platform TEXT NOT NULL DEFAULT 'general',
                original_video_path TEXT,
                nas_file_path TEXT,
                max_clip_duration INTEGER NOT NULL DEFAULT 2,
                candidate_clip_count INTEGER NOT NULL DEFAULT 8,
                ai_preference TEXT,
                ai_prompt_preset_id TEXT NOT NULL DEFAULT 'preset_001',
                status TEXT NOT NULL DEFAULT 'pending_video',
                progress INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clip_candidates (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                clip_key TEXT,
                title TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                summary TEXT,
                reason TEXT,
                highlight_reason TEXT,
                spread_value TEXT,
                suggested_editing TEXT,
                confidence_score REAL NOT NULL DEFAULT 0,
                selected_by_default INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                reviewed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS output_clip (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                clip_candidate_id TEXT,
                output_file_path TEXT,
                output_file_name TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id),
                FOREIGN KEY(clip_candidate_id) REFERENCES clip_candidates(id)
            );

            CREATE TABLE IF NOT EXISTS ai_prompt_presets (
                id TEXT PRIMARY KEY,
                slot INTEGER NOT NULL UNIQUE,
                name TEXT NOT NULL,
                prompt_text TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        _migrate_tasks_table(connection)
        _migrate_clip_candidates_table(connection)
        _migrate_output_clip_table(connection)
        _seed_ai_prompt_presets(connection)
        connection.commit()


def _get_table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _migrate_tasks_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "tasks")
    migrations = {
        "task_name": "ALTER TABLE tasks ADD COLUMN task_name TEXT",
        "source_type": "ALTER TABLE tasks ADD COLUMN source_type TEXT NOT NULL DEFAULT 'upload'",
        "platform": "ALTER TABLE tasks ADD COLUMN platform TEXT NOT NULL DEFAULT 'general'",
        "original_video_path": "ALTER TABLE tasks ADD COLUMN original_video_path TEXT",
        "nas_file_path": "ALTER TABLE tasks ADD COLUMN nas_file_path TEXT",
        "max_clip_duration": "ALTER TABLE tasks ADD COLUMN max_clip_duration INTEGER NOT NULL DEFAULT 2",
        "candidate_clip_count": "ALTER TABLE tasks ADD COLUMN candidate_clip_count INTEGER NOT NULL DEFAULT 8",
        "ai_preference": "ALTER TABLE tasks ADD COLUMN ai_preference TEXT",
        "ai_prompt_preset_id": "ALTER TABLE tasks ADD COLUMN ai_prompt_preset_id TEXT NOT NULL DEFAULT 'preset_001'",
        "status": "ALTER TABLE tasks ADD COLUMN status TEXT NOT NULL DEFAULT 'pending_video'",
        "progress": "ALTER TABLE tasks ADD COLUMN progress INTEGER NOT NULL DEFAULT 0",
        "error_message": "ALTER TABLE tasks ADD COLUMN error_message TEXT",
        "is_deleted": "ALTER TABLE tasks ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0",
        "deleted_at": "ALTER TABLE tasks ADD COLUMN deleted_at TEXT",
        "created_at": "ALTER TABLE tasks ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "updated_at": "ALTER TABLE tasks ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    }

    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)

    columns = _get_table_columns(connection, "tasks")
    if "title" in columns:
        connection.execute(
            """
            UPDATE tasks
            SET task_name = COALESCE(NULLIF(task_name, ''), NULLIF(title, ''), '未命名任务')
            WHERE task_name IS NULL OR task_name = ''
            """
        )

    if "source_path" in columns:
        connection.execute(
            """
            UPDATE tasks
            SET original_video_path = COALESCE(NULLIF(original_video_path, ''), source_path)
            WHERE source_type = 'upload' AND source_path IS NOT NULL AND source_path != ''
            """
        )
        connection.execute(
            """
            UPDATE tasks
            SET nas_file_path = COALESCE(NULLIF(nas_file_path, ''), source_path)
            WHERE source_type = 'nas' AND source_path IS NOT NULL AND source_path != ''
            """
        )

    if "max_clip_minutes" in columns:
        connection.execute(
            """
            UPDATE tasks
            SET max_clip_duration = max_clip_minutes
            WHERE max_clip_minutes IS NOT NULL
              AND (max_clip_duration IS NULL OR max_clip_duration = 2)
            """
        )

    if "target_clip_count" in columns:
        connection.execute(
            """
            UPDATE tasks
            SET candidate_clip_count = target_clip_count
            WHERE target_clip_count IS NOT NULL
              AND (candidate_clip_count IS NULL OR candidate_clip_count = 8)
            """
        )

    connection.executescript(
        """
        UPDATE tasks SET task_name = '未命名任务' WHERE task_name IS NULL OR task_name = '';
        UPDATE tasks SET is_deleted = 0 WHERE is_deleted IS NULL;
        UPDATE tasks SET ai_prompt_preset_id = 'preset_001' WHERE ai_prompt_preset_id IS NULL OR ai_prompt_preset_id = '';
        UPDATE tasks SET source_type = 'upload' WHERE source_type NOT IN ('upload', 'nas') OR source_type IS NULL OR source_type = '';

        UPDATE tasks SET platform = 'douyin' WHERE platform IN ('抖音', 'douyin');
        UPDATE tasks SET platform = 'bilibili' WHERE platform IN ('B站', '哔哩哔哩', 'bilibili');
        UPDATE tasks SET platform = 'general' WHERE platform IN ('通用', 'general') OR platform IS NULL OR platform = '';
        UPDATE tasks SET platform = 'general' WHERE platform NOT IN ('douyin', 'bilibili', 'general');

        UPDATE tasks SET status = 'pending_video' WHERE status IN ('待提交视频', 'pending_video');
        UPDATE tasks SET status = 'pending_processing' WHERE status IN ('待处理', 'pending', 'pending_processing');
        UPDATE tasks SET status = 'audio_extracting' WHERE status IN ('音频提取中', 'extracting_audio', 'audio_extracting');
        UPDATE tasks SET status = 'transcribing' WHERE status IN ('转写中', 'transcribing');
        UPDATE tasks SET status = 'pending_ai' WHERE status IN ('待 AI 分析', 'waiting_ai', 'pending_ai');
        UPDATE tasks SET status = 'ai_analyzing' WHERE status IN ('AI 分析中', 'ai_analyzing');
        UPDATE tasks SET status = 'pending_review' WHERE status IN ('待人工审核', 'waiting_review', 'pending_review');
        UPDATE tasks SET status = 'cutting' WHERE status IN ('切割中', 'cutting');
        UPDATE tasks SET status = 'completed' WHERE status IN ('已完成', 'completed');
        UPDATE tasks SET status = 'completed_with_errors' WHERE status IN ('部分完成', 'completed_with_errors');
        UPDATE tasks SET status = 'failed' WHERE status IN ('失败', 'failed');
        UPDATE tasks SET status = 'pending_video' WHERE status NOT IN (
            'pending_video', 'pending_processing', 'audio_extracting', 'transcribing',
            'pending_ai', 'ai_analyzing', 'pending_review', 'cutting',
            'completed', 'completed_with_errors', 'failed'
        );
        """
    )


def _migrate_clip_candidates_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "clip_candidates")
    migrations = {
        "clip_key": "ALTER TABLE clip_candidates ADD COLUMN clip_key TEXT",
        "highlight_reason": "ALTER TABLE clip_candidates ADD COLUMN highlight_reason TEXT",
        "suggested_editing": "ALTER TABLE clip_candidates ADD COLUMN suggested_editing TEXT",
        "confidence_score": "ALTER TABLE clip_candidates ADD COLUMN confidence_score REAL NOT NULL DEFAULT 0",
        "selected_by_default": "ALTER TABLE clip_candidates ADD COLUMN selected_by_default INTEGER NOT NULL DEFAULT 1",
        "reviewed": "ALTER TABLE clip_candidates ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0",
    }

    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)

    columns = _get_table_columns(connection, "clip_candidates")
    connection.executescript(
        """
        UPDATE clip_candidates
        SET clip_key = COALESCE(NULLIF(clip_key, ''), id)
        WHERE clip_key IS NULL OR clip_key = '';

        UPDATE clip_candidates
        SET highlight_reason = COALESCE(NULLIF(highlight_reason, ''), NULLIF(reason, ''), '')
        WHERE highlight_reason IS NULL OR highlight_reason = '';

        UPDATE clip_candidates
        SET suggested_editing = COALESCE(NULLIF(suggested_editing, ''), '待人工审核时补充剪辑建议')
        WHERE suggested_editing IS NULL OR suggested_editing = '';
        """
    )


def _migrate_output_clip_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "output_clip")
    migrations = {
        "id": "ALTER TABLE output_clip ADD COLUMN id TEXT",
        "task_id": "ALTER TABLE output_clip ADD COLUMN task_id TEXT",
        "clip_candidate_id": "ALTER TABLE output_clip ADD COLUMN clip_candidate_id TEXT",
        "output_file_path": "ALTER TABLE output_clip ADD COLUMN output_file_path TEXT",
        "output_file_name": "ALTER TABLE output_clip ADD COLUMN output_file_name TEXT",
        "status": "ALTER TABLE output_clip ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'",
        "error_message": "ALTER TABLE output_clip ADD COLUMN error_message TEXT",
        "created_at": "ALTER TABLE output_clip ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "updated_at": "ALTER TABLE output_clip ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    }

    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)


def _seed_ai_prompt_presets(connection: sqlite3.Connection) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    default_prompt = ""
    if DEFAULT_AI_PROMPT_PATH.exists():
        default_prompt = DEFAULT_AI_PROMPT_PATH.read_text(encoding="utf-8")

    presets = [
        (DEFAULT_AI_PROMPT_PRESET_ID, 1, "默认直播切片分析专家", default_prompt, 1),
        ("preset_002", 2, "2号方案", "", 0),
        ("preset_003", 3, "3号方案", "", 0),
    ]
    for preset_id, slot, name, prompt_text, is_default in presets:
        existing = connection.execute(
            "SELECT id, prompt_text FROM ai_prompt_presets WHERE id = ?",
            (preset_id,),
        ).fetchone()
        if existing:
            if preset_id == DEFAULT_AI_PROMPT_PRESET_ID and default_prompt and not (existing["prompt_text"] or "").strip():
                connection.execute(
                    """
                    UPDATE ai_prompt_presets
                    SET name = ?, prompt_text = ?, is_default = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (name, default_prompt, now, preset_id),
                )
            continue
        connection.execute(
            """
            INSERT INTO ai_prompt_presets (
                id, slot, name, prompt_text, is_default, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (preset_id, slot, name, prompt_text, is_default, now, now),
        )
