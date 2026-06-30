import sqlite3
from datetime import datetime
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.config import settings


DEFAULT_AI_PROMPT_PRESET_ID = "preset_001"
DEFAULT_AI_PROMPT_PATH = settings.project_root / "prompts" / "default_ai_prompt_preset_001.txt"
VARIETY_AI_PROMPT_PATH = settings.project_root / "prompts" / "variety_interview_prompt_preset_002.txt"


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(settings.database_path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
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
                task_dir_name TEXT,
                source_type TEXT NOT NULL DEFAULT 'upload',
                platform TEXT NOT NULL DEFAULT 'general',
                original_video_path TEXT,
                nas_file_path TEXT,
                max_clip_duration INTEGER NOT NULL DEFAULT 5,
                candidate_clip_count INTEGER NOT NULL DEFAULT 5,
                ai_preference TEXT,
                ai_prompt_preset_id TEXT NOT NULL DEFAULT 'preset_001',
                auto_mode INTEGER NOT NULL DEFAULT 0,
                auto_config_json TEXT,
                status TEXT NOT NULL DEFAULT 'pending_video',
                progress INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                last_error TEXT,
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
                is_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
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

            CREATE TABLE IF NOT EXISTS ai_analysis_runs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                run_number INTEGER NOT NULL,
                provider TEXT NOT NULL,
                provider_label TEXT NOT NULL,
                model TEXT NOT NULL,
                ai_prompt_preset_id TEXT,
                ai_prompt_preset_name TEXT,
                requested_clip_count INTEGER NOT NULL DEFAULT 5,
                clip_count INTEGER NOT NULL DEFAULT 0,
                analysis_summary TEXT,
                fallback_notice TEXT,
                analysis_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS subtitle_style_presets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                font_family TEXT NOT NULL DEFAULT 'Microsoft YaHei',
                font_size INTEGER NOT NULL DEFAULT 42,
                position TEXT NOT NULL DEFAULT 'bottom_center',
                font_color TEXT NOT NULL DEFAULT '#ffffff',
                stroke_color TEXT NOT NULL DEFAULT '#111827',
                shadow_enabled INTEGER NOT NULL DEFAULT 1,
                is_default INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subtitle_jobs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                output_clip_id TEXT NOT NULL,
                style_preset_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                subtitle_file_path TEXT,
                output_file_path TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id),
                FOREIGN KEY(output_clip_id) REFERENCES output_clip(id),
                FOREIGN KEY(style_preset_id) REFERENCES subtitle_style_presets(id)
            );

            CREATE TABLE IF NOT EXISTS publish_platform_configs (
                platform TEXT PRIMARY KEY,
                app_name TEXT NOT NULL DEFAULT '',
                client_key TEXT NOT NULL DEFAULT '',
                client_secret TEXT NOT NULL DEFAULT '',
                redirect_uri TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL DEFAULT '',
                api_base_url TEXT NOT NULL DEFAULT '',
                auth_url TEXT NOT NULL DEFAULT '',
                token_url TEXT NOT NULL DEFAULT '',
                refresh_url TEXT NOT NULL DEFAULT '',
                upload_url TEXT NOT NULL DEFAULT '',
                create_url TEXT NOT NULL DEFAULT '',
                extra_config TEXT,
                status TEXT NOT NULL DEFAULT 'not_configured',
                last_test_status TEXT,
                last_test_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS publish_accounts (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                account_name TEXT NOT NULL,
                account_uid TEXT,
                open_id TEXT,
                access_token TEXT,
                refresh_token TEXT,
                token_expires_at TEXT,
                refresh_expires_at TEXT,
                authorization_status TEXT NOT NULL DEFAULT 'manual',
                scopes TEXT,
                remark TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS publish_jobs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                output_clip_id TEXT NOT NULL,
                clip_id TEXT,
                account_id TEXT,
                platform TEXT NOT NULL,
                publish_mode TEXT NOT NULL DEFAULT 'manual_review',
                video_source TEXT NOT NULL DEFAULT 'original',
                video_file_path TEXT NOT NULL DEFAULT '',
                video_path TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                description TEXT,
                caption TEXT,
                tags TEXT,
                hashtags TEXT,
                cover_text TEXT,
                risk_flags TEXT,
                visibility TEXT NOT NULL DEFAULT 'public',
                cover_mode TEXT NOT NULL DEFAULT 'auto',
                cover_time_seconds REAL NOT NULL DEFAULT 0,
                allow_download INTEGER NOT NULL DEFAULT 1,
                bilibili_tid TEXT,
                bilibili_copyright TEXT NOT NULL DEFAULT 'original',
                bilibili_source TEXT,
                cover_file_path TEXT,
                scheduled_at TEXT,
                status TEXT NOT NULL DEFAULT 'SCHEDULED',
                audit_status TEXT NOT NULL DEFAULT 'not_submitted',
                platform_item_id TEXT,
                platform_upload_id TEXT,
                remote_video_id TEXT,
                error_code TEXT,
                error_message TEXT,
                last_error TEXT,
                provider_response TEXT,
                publish_result TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                published_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id),
                FOREIGN KEY(output_clip_id) REFERENCES output_clip(id),
                FOREIGN KEY(account_id) REFERENCES publish_accounts(id)
            );

            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_jobs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                progress INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                payload_json TEXT,
                result_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS cut_runs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                run_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                is_active INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );
            """
        )
        _migrate_tasks_table(connection)
        _migrate_clip_candidates_table(connection)
        _migrate_output_clip_table(connection)
        _migrate_ai_analysis_runs_table(connection)
        _migrate_subtitle_style_presets_table(connection)
        _migrate_subtitle_jobs_table(connection)
        _migrate_publish_platform_configs_table(connection)
        _migrate_publish_accounts_table(connection)
        _migrate_publish_jobs_table(connection)
        _migrate_workflow_jobs_table(connection)
        _migrate_cut_runs_table(connection)
        _seed_ai_prompt_presets(connection)
        _seed_subtitle_style_preset(connection)
        _seed_publish_platform_configs(connection)
        _create_indexes(connection)
        connection.commit()


def _get_table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _create_indexes(connection: sqlite3.Connection) -> None:
    """创建常用查询索引（IF NOT EXISTS 语法兼容 SQLite 3.27+）。"""
    indexes = [
        # 任务列表与状态筛选
        "CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_is_deleted_created ON tasks(is_deleted, created_at)",
        # 片段候选查询（按任务、启用状态、删除标记）
        "CREATE INDEX IF NOT EXISTS idx_clip_candidates_task_enabled_deleted ON clip_candidates(task_id, enabled, is_deleted)",
        # 输出切片（按任务、状态）
        "CREATE INDEX IF NOT EXISTS idx_output_clip_task_status ON output_clip(task_id, status)",
        # AI 分析（按任务、创建时间；ai_analysis_runs 表无 status 列）
        "CREATE INDEX IF NOT EXISTS idx_ai_analysis_runs_task_created ON ai_analysis_runs(task_id, created_at)",
        # 字幕任务（按任务、输出切片、状态）
        "CREATE INDEX IF NOT EXISTS idx_subtitle_jobs_task_output_status ON subtitle_jobs(task_id, output_clip_id, status)",
        # 发布任务（按状态、平台、时间；按任务、输出切片）
        "CREATE INDEX IF NOT EXISTS idx_publish_jobs_status_platform_created ON publish_jobs(status, platform, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_publish_jobs_task_output ON publish_jobs(task_id, output_clip_id)",
        "CREATE INDEX IF NOT EXISTS idx_publish_jobs_status_scheduled ON publish_jobs(status, scheduled_at)",
        # OAuth state 过期清理
        "CREATE INDEX IF NOT EXISTS idx_oauth_states_expires ON oauth_states(expires_at)",
    ]
    for sql in indexes:
        try:
            connection.execute(sql)
        except sqlite3.Error:
            pass


def _migrate_tasks_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "tasks")
    migrations = {
        "task_name": "ALTER TABLE tasks ADD COLUMN task_name TEXT",
        "task_dir_name": "ALTER TABLE tasks ADD COLUMN task_dir_name TEXT",
        "source_type": "ALTER TABLE tasks ADD COLUMN source_type TEXT NOT NULL DEFAULT 'upload'",
        "platform": "ALTER TABLE tasks ADD COLUMN platform TEXT NOT NULL DEFAULT 'general'",
        "original_video_path": "ALTER TABLE tasks ADD COLUMN original_video_path TEXT",
        "nas_file_path": "ALTER TABLE tasks ADD COLUMN nas_file_path TEXT",
        "max_clip_duration": "ALTER TABLE tasks ADD COLUMN max_clip_duration INTEGER NOT NULL DEFAULT 5",
        "candidate_clip_count": "ALTER TABLE tasks ADD COLUMN candidate_clip_count INTEGER NOT NULL DEFAULT 5",
        "ai_preference": "ALTER TABLE tasks ADD COLUMN ai_preference TEXT",
        "ai_prompt_preset_id": "ALTER TABLE tasks ADD COLUMN ai_prompt_preset_id TEXT NOT NULL DEFAULT 'preset_001'",
        "auto_mode": "ALTER TABLE tasks ADD COLUMN auto_mode INTEGER NOT NULL DEFAULT 0",
        "auto_config_json": "ALTER TABLE tasks ADD COLUMN auto_config_json TEXT",
        "status": "ALTER TABLE tasks ADD COLUMN status TEXT NOT NULL DEFAULT 'pending_video'",
        "progress": "ALTER TABLE tasks ADD COLUMN progress INTEGER NOT NULL DEFAULT 0",
        "error_message": "ALTER TABLE tasks ADD COLUMN error_message TEXT",
        "last_error": "ALTER TABLE tasks ADD COLUMN last_error TEXT",
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
        UPDATE tasks SET task_dir_name = id WHERE task_dir_name IS NULL OR task_dir_name = '';
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
            'CREATED', 'PREPARING_SOURCE', 'TRANSCRIBING', 'AI_ANALYZING',
            'CLIP_SELECTING', 'VIDEO_CUTTING', 'METADATA_GENERATING',
            'SCHEDULE_CREATING', 'PUBLISH_JOB_CREATING', 'READY_TO_PUBLISH',
            'COMPLETED', 'FAILED_PREPARING_SOURCE', 'FAILED_TRANSCRIBING',
            'FAILED_AI_ANALYZING', 'FAILED_CLIP_SELECTING', 'FAILED_VIDEO_CUTTING',
            'FAILED_METADATA_GENERATING', 'FAILED_SCHEDULE_CREATING',
            'FAILED_PUBLISH_JOB_CREATING',
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
        "is_deleted": "ALTER TABLE clip_candidates ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0",
        "deleted_at": "ALTER TABLE clip_candidates ADD COLUMN deleted_at TEXT",
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
        SET suggested_editing = COALESCE(NULLIF(suggested_editing, ''), 'AI 结果检查时补充剪辑建议')
        WHERE suggested_editing IS NULL OR suggested_editing = '';

        UPDATE clip_candidates
        SET is_deleted = 0
        WHERE is_deleted IS NULL;
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
        "cut_run_id": "ALTER TABLE output_clip ADD COLUMN cut_run_id TEXT",
        "is_active": "ALTER TABLE output_clip ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
    }

    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)

    # 为已有记录补充默认值
    columns = _get_table_columns(connection, "output_clip")
    if "is_active" in columns:
        connection.execute("UPDATE output_clip SET is_active = 1 WHERE is_active IS NULL")


def _migrate_ai_analysis_runs_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "ai_analysis_runs")
    if not columns:
        return

    migrations = {
        "id": "ALTER TABLE ai_analysis_runs ADD COLUMN id TEXT",
        "task_id": "ALTER TABLE ai_analysis_runs ADD COLUMN task_id TEXT",
        "run_number": "ALTER TABLE ai_analysis_runs ADD COLUMN run_number INTEGER NOT NULL DEFAULT 1",
        "provider": "ALTER TABLE ai_analysis_runs ADD COLUMN provider TEXT NOT NULL DEFAULT 'remote'",
        "provider_label": "ALTER TABLE ai_analysis_runs ADD COLUMN provider_label TEXT NOT NULL DEFAULT '远程 AI'",
        "model": "ALTER TABLE ai_analysis_runs ADD COLUMN model TEXT NOT NULL DEFAULT ''",
        "ai_prompt_preset_id": "ALTER TABLE ai_analysis_runs ADD COLUMN ai_prompt_preset_id TEXT",
        "ai_prompt_preset_name": "ALTER TABLE ai_analysis_runs ADD COLUMN ai_prompt_preset_name TEXT",
        "requested_clip_count": "ALTER TABLE ai_analysis_runs ADD COLUMN requested_clip_count INTEGER NOT NULL DEFAULT 5",
        "clip_count": "ALTER TABLE ai_analysis_runs ADD COLUMN clip_count INTEGER NOT NULL DEFAULT 0",
        "analysis_summary": "ALTER TABLE ai_analysis_runs ADD COLUMN analysis_summary TEXT",
        "fallback_notice": "ALTER TABLE ai_analysis_runs ADD COLUMN fallback_notice TEXT",
        "analysis_payload_json": "ALTER TABLE ai_analysis_runs ADD COLUMN analysis_payload_json TEXT NOT NULL DEFAULT '{}'",
        "created_at": "ALTER TABLE ai_analysis_runs ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "is_active": "ALTER TABLE ai_analysis_runs ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
    }

    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)

    # 为已有记录补充默认值，并将每个 task 只有最新 run 标记为 active
    columns = _get_table_columns(connection, "ai_analysis_runs")
    if "is_active" in columns:
        connection.execute("UPDATE ai_analysis_runs SET is_active = 1 WHERE is_active IS NULL")
        # 每个 task 只保留最新 run_number 为 active
        connection.executescript(
            """
            UPDATE ai_analysis_runs SET is_active = 0
            WHERE id NOT IN (
                SELECT id FROM ai_analysis_runs
                WHERE (task_id, run_number) IN (
                    SELECT task_id, MAX(run_number)
                    FROM ai_analysis_runs
                    GROUP BY task_id
                )
            );
            """
        )


def _migrate_subtitle_style_presets_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "subtitle_style_presets")
    if not columns:
        return

    migrations = {
        "name": "ALTER TABLE subtitle_style_presets ADD COLUMN name TEXT NOT NULL DEFAULT '默认字幕样式'",
        "font_family": "ALTER TABLE subtitle_style_presets ADD COLUMN font_family TEXT NOT NULL DEFAULT 'Microsoft YaHei'",
        "font_size": "ALTER TABLE subtitle_style_presets ADD COLUMN font_size INTEGER NOT NULL DEFAULT 42",
        "position": "ALTER TABLE subtitle_style_presets ADD COLUMN position TEXT NOT NULL DEFAULT 'bottom_center'",
        "font_color": "ALTER TABLE subtitle_style_presets ADD COLUMN font_color TEXT NOT NULL DEFAULT '#ffffff'",
        "stroke_color": "ALTER TABLE subtitle_style_presets ADD COLUMN stroke_color TEXT NOT NULL DEFAULT '#111827'",
        "shadow_enabled": "ALTER TABLE subtitle_style_presets ADD COLUMN shadow_enabled INTEGER NOT NULL DEFAULT 1",
        "is_default": "ALTER TABLE subtitle_style_presets ADD COLUMN is_default INTEGER NOT NULL DEFAULT 1",
        "created_at": "ALTER TABLE subtitle_style_presets ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "updated_at": "ALTER TABLE subtitle_style_presets ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)


def _migrate_subtitle_jobs_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "subtitle_jobs")
    if not columns:
        return

    migrations = {
        "task_id": "ALTER TABLE subtitle_jobs ADD COLUMN task_id TEXT",
        "output_clip_id": "ALTER TABLE subtitle_jobs ADD COLUMN output_clip_id TEXT",
        "style_preset_id": "ALTER TABLE subtitle_jobs ADD COLUMN style_preset_id TEXT",
        "status": "ALTER TABLE subtitle_jobs ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'",
        "subtitle_file_path": "ALTER TABLE subtitle_jobs ADD COLUMN subtitle_file_path TEXT",
        "output_file_path": "ALTER TABLE subtitle_jobs ADD COLUMN output_file_path TEXT",
        "error_message": "ALTER TABLE subtitle_jobs ADD COLUMN error_message TEXT",
        "created_at": "ALTER TABLE subtitle_jobs ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "updated_at": "ALTER TABLE subtitle_jobs ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
        "is_active": "ALTER TABLE subtitle_jobs ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)

    # 为已有记录补充默认值
    columns = _get_table_columns(connection, "subtitle_jobs")
    if "is_active" in columns:
        connection.execute("UPDATE subtitle_jobs SET is_active = 1 WHERE is_active IS NULL")


def _migrate_publish_platform_configs_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "publish_platform_configs")
    if not columns:
        return

    migrations = {
        "platform": "ALTER TABLE publish_platform_configs ADD COLUMN platform TEXT",
        "app_name": "ALTER TABLE publish_platform_configs ADD COLUMN app_name TEXT NOT NULL DEFAULT ''",
        "client_key": "ALTER TABLE publish_platform_configs ADD COLUMN client_key TEXT NOT NULL DEFAULT ''",
        "client_secret": "ALTER TABLE publish_platform_configs ADD COLUMN client_secret TEXT NOT NULL DEFAULT ''",
        "redirect_uri": "ALTER TABLE publish_platform_configs ADD COLUMN redirect_uri TEXT NOT NULL DEFAULT ''",
        "scope": "ALTER TABLE publish_platform_configs ADD COLUMN scope TEXT NOT NULL DEFAULT ''",
        "api_base_url": "ALTER TABLE publish_platform_configs ADD COLUMN api_base_url TEXT NOT NULL DEFAULT ''",
        "auth_url": "ALTER TABLE publish_platform_configs ADD COLUMN auth_url TEXT NOT NULL DEFAULT ''",
        "token_url": "ALTER TABLE publish_platform_configs ADD COLUMN token_url TEXT NOT NULL DEFAULT ''",
        "refresh_url": "ALTER TABLE publish_platform_configs ADD COLUMN refresh_url TEXT NOT NULL DEFAULT ''",
        "upload_url": "ALTER TABLE publish_platform_configs ADD COLUMN upload_url TEXT NOT NULL DEFAULT ''",
        "create_url": "ALTER TABLE publish_platform_configs ADD COLUMN create_url TEXT NOT NULL DEFAULT ''",
        "extra_config": "ALTER TABLE publish_platform_configs ADD COLUMN extra_config TEXT",
        "status": "ALTER TABLE publish_platform_configs ADD COLUMN status TEXT NOT NULL DEFAULT 'not_configured'",
        "last_test_status": "ALTER TABLE publish_platform_configs ADD COLUMN last_test_status TEXT",
        "last_test_message": "ALTER TABLE publish_platform_configs ADD COLUMN last_test_message TEXT",
        "created_at": "ALTER TABLE publish_platform_configs ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "updated_at": "ALTER TABLE publish_platform_configs ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)


def _migrate_publish_accounts_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "publish_accounts")
    if not columns:
        return

    migrations = {
        "id": "ALTER TABLE publish_accounts ADD COLUMN id TEXT",
        "platform": "ALTER TABLE publish_accounts ADD COLUMN platform TEXT NOT NULL DEFAULT 'douyin'",
        "account_name": "ALTER TABLE publish_accounts ADD COLUMN account_name TEXT NOT NULL DEFAULT ''",
        "account_uid": "ALTER TABLE publish_accounts ADD COLUMN account_uid TEXT",
        "open_id": "ALTER TABLE publish_accounts ADD COLUMN open_id TEXT",
        "access_token": "ALTER TABLE publish_accounts ADD COLUMN access_token TEXT",
        "refresh_token": "ALTER TABLE publish_accounts ADD COLUMN refresh_token TEXT",
        "token_expires_at": "ALTER TABLE publish_accounts ADD COLUMN token_expires_at TEXT",
        "refresh_expires_at": "ALTER TABLE publish_accounts ADD COLUMN refresh_expires_at TEXT",
        "authorization_status": "ALTER TABLE publish_accounts ADD COLUMN authorization_status TEXT NOT NULL DEFAULT 'manual'",
        "scopes": "ALTER TABLE publish_accounts ADD COLUMN scopes TEXT",
        "remark": "ALTER TABLE publish_accounts ADD COLUMN remark TEXT",
        "created_at": "ALTER TABLE publish_accounts ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "updated_at": "ALTER TABLE publish_accounts ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)


def _migrate_publish_jobs_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "publish_jobs")
    if not columns:
        return

    migrations = {
        "id": "ALTER TABLE publish_jobs ADD COLUMN id TEXT",
        "task_id": "ALTER TABLE publish_jobs ADD COLUMN task_id TEXT",
        "output_clip_id": "ALTER TABLE publish_jobs ADD COLUMN output_clip_id TEXT",
        "clip_id": "ALTER TABLE publish_jobs ADD COLUMN clip_id TEXT",
        "account_id": "ALTER TABLE publish_jobs ADD COLUMN account_id TEXT",
        "platform": "ALTER TABLE publish_jobs ADD COLUMN platform TEXT NOT NULL DEFAULT 'douyin'",
        "publish_mode": "ALTER TABLE publish_jobs ADD COLUMN publish_mode TEXT NOT NULL DEFAULT 'manual_review'",
        "video_source": "ALTER TABLE publish_jobs ADD COLUMN video_source TEXT NOT NULL DEFAULT 'original'",
        "video_file_path": "ALTER TABLE publish_jobs ADD COLUMN video_file_path TEXT NOT NULL DEFAULT ''",
        "video_path": "ALTER TABLE publish_jobs ADD COLUMN video_path TEXT NOT NULL DEFAULT ''",
        "title": "ALTER TABLE publish_jobs ADD COLUMN title TEXT NOT NULL DEFAULT ''",
        "description": "ALTER TABLE publish_jobs ADD COLUMN description TEXT",
        "caption": "ALTER TABLE publish_jobs ADD COLUMN caption TEXT",
        "tags": "ALTER TABLE publish_jobs ADD COLUMN tags TEXT",
        "hashtags": "ALTER TABLE publish_jobs ADD COLUMN hashtags TEXT",
        "cover_text": "ALTER TABLE publish_jobs ADD COLUMN cover_text TEXT",
        "risk_flags": "ALTER TABLE publish_jobs ADD COLUMN risk_flags TEXT",
        "visibility": "ALTER TABLE publish_jobs ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'",
        "cover_mode": "ALTER TABLE publish_jobs ADD COLUMN cover_mode TEXT NOT NULL DEFAULT 'auto'",
        "cover_time_seconds": "ALTER TABLE publish_jobs ADD COLUMN cover_time_seconds REAL NOT NULL DEFAULT 0",
        "allow_download": "ALTER TABLE publish_jobs ADD COLUMN allow_download INTEGER NOT NULL DEFAULT 1",
        "bilibili_tid": "ALTER TABLE publish_jobs ADD COLUMN bilibili_tid TEXT",
        "bilibili_copyright": "ALTER TABLE publish_jobs ADD COLUMN bilibili_copyright TEXT NOT NULL DEFAULT 'original'",
        "bilibili_source": "ALTER TABLE publish_jobs ADD COLUMN bilibili_source TEXT",
        "cover_file_path": "ALTER TABLE publish_jobs ADD COLUMN cover_file_path TEXT",
        "scheduled_at": "ALTER TABLE publish_jobs ADD COLUMN scheduled_at TEXT",
        "status": "ALTER TABLE publish_jobs ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'",
        "audit_status": "ALTER TABLE publish_jobs ADD COLUMN audit_status TEXT NOT NULL DEFAULT 'not_submitted'",
        "platform_item_id": "ALTER TABLE publish_jobs ADD COLUMN platform_item_id TEXT",
        "platform_upload_id": "ALTER TABLE publish_jobs ADD COLUMN platform_upload_id TEXT",
        "remote_video_id": "ALTER TABLE publish_jobs ADD COLUMN remote_video_id TEXT",
        "error_code": "ALTER TABLE publish_jobs ADD COLUMN error_code TEXT",
        "error_message": "ALTER TABLE publish_jobs ADD COLUMN error_message TEXT",
        "last_error": "ALTER TABLE publish_jobs ADD COLUMN last_error TEXT",
        "provider_response": "ALTER TABLE publish_jobs ADD COLUMN provider_response TEXT",
        "publish_result": "ALTER TABLE publish_jobs ADD COLUMN publish_result TEXT",
        "retry_count": "ALTER TABLE publish_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
        "attempt_count": "ALTER TABLE publish_jobs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
        "published_at": "ALTER TABLE publish_jobs ADD COLUMN published_at TEXT",
        "created_at": "ALTER TABLE publish_jobs ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "updated_at": "ALTER TABLE publish_jobs ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)

    columns = _get_table_columns(connection, "publish_jobs")
    if {"clip_id", "output_clip_id"}.issubset(columns):
        connection.execute("UPDATE publish_jobs SET clip_id = output_clip_id WHERE clip_id IS NULL OR clip_id = ''")
    if {"video_path", "video_file_path"}.issubset(columns):
        connection.execute("UPDATE publish_jobs SET video_path = video_file_path WHERE video_path IS NULL OR video_path = ''")
    if {"caption", "description"}.issubset(columns):
        connection.execute("UPDATE publish_jobs SET caption = description WHERE caption IS NULL OR caption = ''")
    if {"hashtags", "tags"}.issubset(columns):
        connection.execute("UPDATE publish_jobs SET hashtags = tags WHERE hashtags IS NULL OR hashtags = ''")
    if {"remote_video_id", "platform_item_id"}.issubset(columns):
        connection.execute(
            "UPDATE publish_jobs SET remote_video_id = platform_item_id WHERE remote_video_id IS NULL OR remote_video_id = ''"
        )
    if {"publish_result", "provider_response"}.issubset(columns):
        connection.execute(
            "UPDATE publish_jobs SET publish_result = provider_response WHERE publish_result IS NULL OR publish_result = ''"
        )
    if {"attempt_count", "retry_count"}.issubset(columns):
        connection.execute(
            "UPDATE publish_jobs SET attempt_count = retry_count WHERE attempt_count IS NULL OR attempt_count = 0"
        )

    if "status" in columns:
        connection.executescript(
            """
            UPDATE publish_jobs SET status = 'DRAFT' WHERE status = 'draft';
            UPDATE publish_jobs SET status = 'SCHEDULED' WHERE status IN ('ready', 'scheduled');
            UPDATE publish_jobs SET status = 'PUBLISHING' WHERE status = 'publishing';
            UPDATE publish_jobs SET status = 'PUBLISHED' WHERE status = 'published';
            UPDATE publish_jobs SET status = 'FAILED' WHERE status = 'failed';
            UPDATE publish_jobs SET status = 'CANCELLED' WHERE status = 'cancelled';
            UPDATE publish_jobs SET status = 'NEED_REVIEW' WHERE status = 'need_review';
            UPDATE publish_jobs
            SET status = 'WAITING'
            WHERE status = 'SCHEDULED' AND (scheduled_at IS NULL OR scheduled_at = '');
            UPDATE publish_jobs
            SET status = 'SCHEDULED'
            WHERE status IS NULL OR status = '' OR status NOT IN (
                'DRAFT', 'SCHEDULED', 'WAITING', 'PUBLISHING',
                'PUBLISHED', 'FAILED', 'CANCELLED', 'NEED_REVIEW'
            );
            """
        )


def _migrate_workflow_jobs_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "workflow_jobs")
    if not columns:
        return

    migrations = {
        "id": "ALTER TABLE workflow_jobs ADD COLUMN id TEXT",
        "task_id": "ALTER TABLE workflow_jobs ADD COLUMN task_id TEXT",
        "job_type": "ALTER TABLE workflow_jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT ''",
        "status": "ALTER TABLE workflow_jobs ADD COLUMN status TEXT NOT NULL DEFAULT 'queued'",
        "progress": "ALTER TABLE workflow_jobs ADD COLUMN progress INTEGER NOT NULL DEFAULT 0",
        "message": "ALTER TABLE workflow_jobs ADD COLUMN message TEXT",
        "payload_json": "ALTER TABLE workflow_jobs ADD COLUMN payload_json TEXT",
        "result_json": "ALTER TABLE workflow_jobs ADD COLUMN result_json TEXT",
        "error_message": "ALTER TABLE workflow_jobs ADD COLUMN error_message TEXT",
        "created_at": "ALTER TABLE workflow_jobs ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "updated_at": "ALTER TABLE workflow_jobs ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
        "started_at": "ALTER TABLE workflow_jobs ADD COLUMN started_at TEXT",
        "finished_at": "ALTER TABLE workflow_jobs ADD COLUMN finished_at TEXT",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)


def _migrate_cut_runs_table(connection: sqlite3.Connection) -> None:
    """cut_runs 表的列级迁移，兼容未来新增字段"""
    columns = _get_table_columns(connection, "cut_runs")
    if not columns:
        return

    migrations = {
        "id": "ALTER TABLE cut_runs ADD COLUMN id TEXT",
        "task_id": "ALTER TABLE cut_runs ADD COLUMN task_id TEXT",
        "run_number": "ALTER TABLE cut_runs ADD COLUMN run_number INTEGER NOT NULL DEFAULT 1",
        "status": "ALTER TABLE cut_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'processing'",
        "is_active": "ALTER TABLE cut_runs ADD COLUMN is_active INTEGER NOT NULL DEFAULT 0",
        "error_message": "ALTER TABLE cut_runs ADD COLUMN error_message TEXT",
        "created_at": "ALTER TABLE cut_runs ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "updated_at": "ALTER TABLE cut_runs ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    }

    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)


def _seed_ai_prompt_presets(connection: sqlite3.Connection) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    default_prompt = ""
    if DEFAULT_AI_PROMPT_PATH.exists():
        default_prompt = DEFAULT_AI_PROMPT_PATH.read_text(encoding="utf-8")
    variety_prompt = ""
    if VARIETY_AI_PROMPT_PATH.exists():
        variety_prompt = VARIETY_AI_PROMPT_PATH.read_text(encoding="utf-8")

    presets = [
        (DEFAULT_AI_PROMPT_PRESET_ID, 1, "默认直播切片分析专家", default_prompt, 1),
        ("preset_002", 2, "综艺访谈完整上下文专家", variety_prompt, 0),
        ("preset_003", 3, "3号方案", "", 0),
    ]
    for preset_id, slot, name, prompt_text, is_default in presets:
        existing = connection.execute(
            "SELECT id, prompt_text FROM ai_prompt_presets WHERE id = ?",
            (preset_id,),
        ).fetchone()
        if existing:
            if prompt_text and not (existing["prompt_text"] or "").strip():
                connection.execute(
                    """
                    UPDATE ai_prompt_presets
                    SET name = ?, prompt_text = ?, is_default = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (name, prompt_text, is_default, now, preset_id),
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

    if variety_prompt:
        preset_002 = connection.execute(
            "SELECT prompt_text FROM ai_prompt_presets WHERE id = ?",
            ("preset_002",),
        ).fetchone()
        preset_003 = connection.execute(
            "SELECT prompt_text FROM ai_prompt_presets WHERE id = ?",
            ("preset_003",),
        ).fetchone()
        if (
            preset_002
            and (preset_002["prompt_text"] or "").strip()
            and preset_003
            and not (preset_003["prompt_text"] or "").strip()
        ):
            connection.execute(
                """
                UPDATE ai_prompt_presets
                SET name = ?, prompt_text = ?, is_default = 0, updated_at = ?
                WHERE id = ?
                """,
                ("综艺访谈完整上下文专家", variety_prompt, now, "preset_003"),
            )


def _seed_subtitle_style_preset(connection: sqlite3.Connection) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    existing = connection.execute(
        "SELECT id FROM subtitle_style_presets WHERE id = ?",
        ("default",),
    ).fetchone()
    if existing:
        return
    connection.execute(
        """
        INSERT INTO subtitle_style_presets (
            id, name, font_family, font_size, position, font_color,
            stroke_color, shadow_enabled, is_default, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "default",
            "默认字幕样式",
            "Microsoft YaHei",
            42,
            "bottom_center",
            "#ffffff",
            "#111827",
            1,
            1,
            now,
            now,
        ),
    )


def _seed_publish_platform_configs(connection: sqlite3.Connection) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    defaults = [
        {
            "platform": "douyin",
            "app_name": "抖音开放平台",
            "scope": "video.create,video.upload,user_info",
            "api_base_url": "https://open.douyin.com",
            "auth_url": "https://open.douyin.com/platform/oauth/connect/",
            "token_url": "https://open.douyin.com/oauth/access_token/",
            "refresh_url": "https://open.douyin.com/oauth/refresh_token/",
            "upload_url": "https://open.douyin.com/api/douyin/v1/video/upload_video/",
            "create_url": "https://open.douyin.com/api/douyin/v1/video/create_video/",
        },
        {
            "platform": "bilibili",
            "app_name": "B站开放平台",
            "scope": "video.archive",
            "api_base_url": "https://open.bilibili.com",
            "auth_url": "",
            "token_url": "",
            "refresh_url": "",
            "upload_url": "",
            "create_url": "",
        },
    ]
    for item in defaults:
        existing = connection.execute(
            "SELECT platform FROM publish_platform_configs WHERE platform = ?",
            (item["platform"],),
        ).fetchone()
        if existing:
            continue
        connection.execute(
            """
            INSERT INTO publish_platform_configs (
                platform, app_name, scope, api_base_url, auth_url, token_url,
                refresh_url, upload_url, create_url, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["platform"],
                item["app_name"],
                item["scope"],
                item["api_base_url"],
                item["auth_url"],
                item["token_url"],
                item["refresh_url"],
                item["upload_url"],
                item["create_url"],
                "not_configured",
                now,
                now,
            ),
        )
