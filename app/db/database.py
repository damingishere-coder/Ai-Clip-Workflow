import hashlib
import json
import sqlite3
from datetime import datetime
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from app.core.config import settings
from app.services.database_backup_service import create_publish_migration_backup, create_schema_migration_backup


DEFAULT_AI_PROMPT_PRESET_ID = "preset_001"
DEFAULT_AI_PROMPT_PATH = settings.project_root / "prompts" / "default_ai_prompt_preset_001.txt"
VARIETY_AI_PROMPT_PATH = settings.project_root / "prompts" / "variety_interview_prompt_preset_002.txt"
COMEDY_V2_AI_PROMPT_PATH = settings.project_root / "prompts" / "variety_comedy_v2_prompt.txt"

PUBLISH_ACTIVE_UNIQUE_INDEX_LEGACY_NAME = "uq_publish_jobs_active_clip_platform_mode"
PUBLISH_ACTIVE_UNIQUE_INDEX_NAME = "uq_publish_jobs_active_clip_platform_mode_v2"
PUBLISH_UNIQUE_ACTIVE_STATUSES = (
    "DRAFT",
    "WAITING",
    "SCHEDULED",
    "PUBLISHING",
    "NEED_REVIEW",
)
PUBLISH_UNIQUE_ACTIVE_STATUS_SQL = ", ".join(f"'{status}'" for status in PUBLISH_UNIQUE_ACTIVE_STATUSES)
PUBLISH_ACTIVE_UNIQUE_INDEX_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {PUBLISH_ACTIVE_UNIQUE_INDEX_NAME}
ON publish_jobs(output_clip_id, platform, publish_mode)
WHERE status IN ({PUBLISH_UNIQUE_ACTIVE_STATUS_SQL})
  AND output_clip_id IS NOT NULL AND output_clip_id <> ''
""".strip()
PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION = "20260824_01_publish_active_unique_index_v2"
PUBLISH_ACTIVE_INDEX_MIGRATION_NAME = "发布活动任务唯一索引安全切换"
PUBLISH_ACTIVE_INDEX_MIGRATION_CHECKSUM = hashlib.sha256(
    (
        f"{PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION}\n"
        f"{PUBLISH_ACTIVE_INDEX_MIGRATION_NAME}\n"
        f"{PUBLISH_ACTIVE_UNIQUE_INDEX_SQL}\n"
        f"DROP INDEX {PUBLISH_ACTIVE_UNIQUE_INDEX_LEGACY_NAME}"
    ).encode("utf-8")
).hexdigest()
TASK_UPLOAD_ONLY_MIGRATION_VERSION = "20260824_02_task_upload_only"
TASK_UPLOAD_ONLY_MIGRATION_NAME = "任务视频来源归一为本机上传"
TASK_UPLOAD_ONLY_MIGRATION_SQL = """
UPDATE tasks
SET original_video_path = CASE
        WHEN original_video_path IS NULL OR TRIM(original_video_path) = '' THEN nas_file_path
        ELSE original_video_path
    END,
    source_type = 'upload',
    nas_file_path = NULL
WHERE source_type != 'upload'
   OR source_type IS NULL
   OR (nas_file_path IS NOT NULL AND TRIM(nas_file_path) != '')
""".strip()
TASK_UPLOAD_ONLY_MIGRATION_CHECKSUM = hashlib.sha256(
    (
        f"{TASK_UPLOAD_ONLY_MIGRATION_VERSION}\n"
        f"{TASK_UPLOAD_ONLY_MIGRATION_NAME}\n"
        f"{TASK_UPLOAD_ONLY_MIGRATION_SQL}"
    ).encode("utf-8")
).hexdigest()
CONTENT_REVIEW_MIGRATION_VERSION = "20260828_01_content_review_v1"
CONTENT_REVIEW_MIGRATION_NAME = "内容复盘归因与指标快照基础结构"
CONTENT_REVIEW_REQUIRED_INDEXES = (
    "idx_clip_candidates_source_analysis_run",
    "idx_ai_analysis_runs_prompt_version",
    "idx_clip_feedback_candidate_created",
    "idx_content_metric_import_batches_account_created",
    "idx_douyin_account_daily_account_date",
    "idx_douyin_item_metrics_account_published",
    "idx_douyin_item_metrics_match_status",
)
CONTENT_REVIEW_MIGRATION_SPEC = "\n".join(
    (
        CONTENT_REVIEW_MIGRATION_VERSION,
        CONTENT_REVIEW_MIGRATION_NAME,
        "clip_candidates.source_analysis_run_id",
        "ai_analysis_runs.prompt_version_id",
        "ai_analysis_runs.prompt_text_sha256",
        "clip_feedback.decision_source",
        "ai_prompt_versions",
        "content_metric_import_batches",
        "douyin_account_daily_metric_snapshots",
        "douyin_item_metric_snapshots",
        *CONTENT_REVIEW_REQUIRED_INDEXES,
        "backfill-candidate-only-when-one-analysis-run",
        "do-not-guess-historical-prompt-version",
    )
)
CONTENT_REVIEW_MIGRATION_CHECKSUM = hashlib.sha256(
    CONTENT_REVIEW_MIGRATION_SPEC.encode("utf-8")
).hexdigest()
DOUYIN_ITEM_EXPORT_MIGRATION_VERSION = "20260829_01_douyin_official_item_export"
DOUYIN_ITEM_EXPORT_MIGRATION_NAME = "抖音官方作品报表完整指标"
DOUYIN_ITEM_EXPORT_COLUMNS = {
    "completion_rate": "REAL",
    "home_visit_count": "INTEGER",
    "follower_gain_count": "INTEGER",
    "content_genre": "TEXT",
    "audit_status": "TEXT",
}
DOUYIN_ITEM_EXPORT_MIGRATION_SPEC = "\n".join(
    (
        DOUYIN_ITEM_EXPORT_MIGRATION_VERSION,
        DOUYIN_ITEM_EXPORT_MIGRATION_NAME,
        *(f"douyin_item_metric_snapshots.{name}:{column_type}" for name, column_type in DOUYIN_ITEM_EXPORT_COLUMNS.items()),
        "preserve-20260828_01-checksum",
        "no-raw-xlsx-or-browser-credentials",
    )
)
DOUYIN_ITEM_EXPORT_MIGRATION_CHECKSUM = hashlib.sha256(
    DOUYIN_ITEM_EXPORT_MIGRATION_SPEC.encode("utf-8")
).hexdigest()
CONTENT_FEEDBACK_LOOP_MIGRATION_VERSION = "20260829_02_content_feedback_loop"
CONTENT_FEEDBACK_LOOP_MIGRATION_NAME = "内容复盘诊断与实验闭环"
CONTENT_FEEDBACK_LOOP_REQUIRED_TABLES = (
    "content_improvement_experiments",
    "content_improvement_experiment_items",
)
CONTENT_FEEDBACK_LOOP_REQUIRED_INDEXES = (
    "idx_content_experiments_account_status",
    "idx_content_experiment_items_experiment",
)
CONTENT_FEEDBACK_LOOP_MIGRATION_SPEC = "\n".join(
    (
        CONTENT_FEEDBACK_LOOP_MIGRATION_VERSION,
        CONTENT_FEEDBACK_LOOP_MIGRATION_NAME,
        *CONTENT_FEEDBACK_LOOP_REQUIRED_TABLES,
        *CONTENT_FEEDBACK_LOOP_REQUIRED_INDEXES,
        "one-experiment-per-publish-job",
        "freeze-baseline-at-creation",
        "no-automatic-prompt-or-publish-actions",
    )
)
CONTENT_FEEDBACK_LOOP_MIGRATION_CHECKSUM = hashlib.sha256(
    CONTENT_FEEDBACK_LOOP_MIGRATION_SPEC.encode("utf-8")
).hexdigest()
AI_PROMPT_VERSION_FK_MIGRATION_VERSION = "20260830_01_ai_prompt_version_fk"
AI_PROMPT_VERSION_FK_MIGRATION_NAME = "AI 分析 Prompt 版本外键一致性"
AI_PROMPT_VERSION_FK_MIGRATION_SPEC = "\n".join(
    (
        AI_PROMPT_VERSION_FK_MIGRATION_VERSION,
        AI_PROMPT_VERSION_FK_MIGRATION_NAME,
        "ai_analysis_runs.prompt_version_id->ai_prompt_versions.id",
        "on-update-no-action",
        "on-delete-no-action",
        "preserve-ai-analysis-run-data-indexes-triggers",
        "reject-orphan-prompt-version-references",
        "foreign-key-check-before-ledger-commit",
    )
)
AI_PROMPT_VERSION_FK_MIGRATION_CHECKSUM = hashlib.sha256(
    AI_PROMPT_VERSION_FK_MIGRATION_SPEC.encode("utf-8")
).hexdigest()
AI_ANALYSIS_RUN_COLUMNS = (
    "id",
    "task_id",
    "run_number",
    "provider",
    "provider_label",
    "model",
    "ai_prompt_preset_id",
    "ai_prompt_preset_name",
    "prompt_version_id",
    "prompt_text_sha256",
    "requested_clip_count",
    "clip_count",
    "analysis_summary",
    "fallback_notice",
    "analysis_payload_json",
    "created_at",
    "is_active",
)


class SchemaMigrationError(RuntimeError):
    """数据库结构迁移或不变量验证失败。"""


@dataclass(frozen=True)
class SchemaMigration:
    version: str
    name: str
    checksum: str
    apply: Callable[[sqlite3.Connection], None]
    verify: Callable[[sqlite3.Connection], None]
    requires_foreign_keys_off: bool = False


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

    needs_long_live_backup = _requires_long_live_schema_migration(settings.database_path)
    needs_workflow_fencing_backup = _requires_workflow_job_fencing_migration(settings.database_path)
    needs_subtitle_editor_backup = _requires_subtitle_editor_schema_migration(settings.database_path)
    needs_subtitle_auto_backup = _requires_subtitle_auto_schema_migration(settings.database_path)
    needs_publish_index_backup = _requires_publish_active_index_migration(settings.database_path)
    needs_task_upload_only_backup = _requires_task_upload_only_migration(settings.database_path)
    needs_content_review_backup = _requires_content_review_schema_migration(settings.database_path)
    needs_douyin_item_export_backup = _requires_douyin_item_export_migration(settings.database_path)
    needs_content_feedback_loop_backup = _requires_content_feedback_loop_migration(
        settings.database_path
    )
    needs_ai_prompt_version_fk_backup = _requires_ai_prompt_version_fk_migration(
        settings.database_path
    )
    if needs_long_live_backup:
        create_schema_migration_backup(
            settings.database_path,
            settings.data_dir / "backups",
            "long-live-foundation",
        )
    if needs_workflow_fencing_backup and not needs_long_live_backup:
        create_schema_migration_backup(
            settings.database_path,
            settings.data_dir / "backups",
            "workflow-job-fencing",
        )
    if needs_subtitle_editor_backup:
        create_schema_migration_backup(
            settings.database_path,
            settings.data_dir / "backups",
            "subtitle-editor-rebuild",
        )
    if needs_subtitle_auto_backup:
        create_schema_migration_backup(
            settings.database_path,
            settings.data_dir / "backups",
            "subtitle-auto-workflow",
        )
    if needs_publish_index_backup and not any(
        (
            needs_long_live_backup,
            needs_workflow_fencing_backup,
            needs_subtitle_editor_backup,
            needs_subtitle_auto_backup,
        )
    ):
        create_schema_migration_backup(
            settings.database_path,
            settings.data_dir / "backups",
            "publish-active-unique-index-v2",
        )
    if needs_task_upload_only_backup:
        create_schema_migration_backup(
            settings.database_path,
            settings.data_dir / "backups",
            "task-upload-only",
        )
    if needs_content_review_backup:
        create_schema_migration_backup(
            settings.database_path,
            settings.data_dir / "backups",
            "content-review-v1",
        )
    if needs_douyin_item_export_backup and not needs_content_review_backup:
        create_schema_migration_backup(
            settings.database_path,
            settings.data_dir / "backups",
            "douyin-official-item-export",
        )
    if (
        needs_content_feedback_loop_backup
        and not needs_content_review_backup
        and not needs_douyin_item_export_backup
    ):
        create_schema_migration_backup(
            settings.database_path,
            settings.data_dir / "backups",
            "content-feedback-loop",
        )
    if needs_ai_prompt_version_fk_backup and not any(
        (
            needs_content_review_backup,
            needs_douyin_item_export_backup,
            needs_content_feedback_loop_backup,
        )
    ):
        create_schema_migration_backup(
            settings.database_path,
            settings.data_dir / "backups",
            "ai-prompt-version-fk",
        )

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
                max_clip_duration INTEGER NOT NULL DEFAULT 10,
                candidate_clip_count INTEGER NOT NULL DEFAULT 12,
                selection_profile TEXT NOT NULL DEFAULT 'general',
                final_clip_target INTEGER NOT NULL DEFAULT 5,
                highlight_density_per_hour INTEGER NOT NULL DEFAULT 4,
                highlight_total_limit INTEGER NOT NULL DEFAULT 30,
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

            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clip_candidates (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                clip_key TEXT,
                title TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                cover_time_seconds REAL,
                summary TEXT,
                reason TEXT,
                highlight_reason TEXT,
                spread_value TEXT,
                suggested_editing TEXT,
                confidence_score REAL NOT NULL DEFAULT 0,
                quality_tier TEXT NOT NULL DEFAULT '',
                quality_score REAL NOT NULL DEFAULT 0,
                text_quality_score REAL NOT NULL DEFAULT 0,
                humor_score REAL NOT NULL DEFAULT 0,
                completeness_score REAL NOT NULL DEFAULT 0,
                audio_reaction_score REAL NOT NULL DEFAULT 0,
                topic_key TEXT,
                key_moment_time TEXT,
                quality_evidence_json TEXT,
                rejection_reason TEXT,
                selected_by_default INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                reviewed INTEGER NOT NULL DEFAULT 0,
                source_analysis_run_id TEXT,
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
                source_start_ms INTEGER,
                source_end_ms INTEGER,
                source_duration_ms INTEGER,
                source_fingerprint TEXT,
                snapshot_source TEXT NOT NULL DEFAULT 'legacy_inferred',
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

            CREATE TABLE IF NOT EXISTS ai_prompt_versions (
                id TEXT PRIMARY KEY,
                preset_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                preset_name_snapshot TEXT NOT NULL,
                prompt_text TEXT NOT NULL,
                prompt_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(preset_id, version_number),
                UNIQUE(preset_id, prompt_sha256),
                FOREIGN KEY(preset_id) REFERENCES ai_prompt_presets(id)
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
                prompt_version_id TEXT,
                prompt_text_sha256 TEXT,
                requested_clip_count INTEGER NOT NULL DEFAULT 5,
                clip_count INTEGER NOT NULL DEFAULT 0,
                analysis_summary TEXT,
                fallback_notice TEXT,
                analysis_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id),
                FOREIGN KEY(prompt_version_id) REFERENCES ai_prompt_versions(id)
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
                outline_width REAL NOT NULL DEFAULT 3,
                shadow_depth REAL NOT NULL DEFAULT 1,
                safe_area_percent REAL NOT NULL DEFAULT 5,
                speaker_styles_json TEXT NOT NULL DEFAULT '{}',
                is_default INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subtitle_jobs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                output_clip_id TEXT NOT NULL,
                revision_id TEXT,
                workflow_job_id TEXT,
                style_preset_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                subtitle_file_path TEXT,
                output_file_path TEXT,
                error_message TEXT,
                validation_status TEXT NOT NULL DEFAULT 'legacy_unverified',
                validation_json TEXT NOT NULL DEFAULT '{}',
                encoder TEXT NOT NULL DEFAULT '',
                verified_at TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id),
                FOREIGN KEY(output_clip_id) REFERENCES output_clip(id),
                FOREIGN KEY(revision_id) REFERENCES subtitle_revisions(id),
                FOREIGN KEY(workflow_job_id) REFERENCES workflow_jobs(id),
                FOREIGN KEY(style_preset_id) REFERENCES subtitle_style_presets(id)
            );

            CREATE TABLE IF NOT EXISTS subtitle_tracks (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                track_type TEXT NOT NULL,
                output_clip_id TEXT,
                name TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'zh-CN',
                source_track_id TEXT,
                source_revision_id TEXT,
                source_fingerprint TEXT NOT NULL DEFAULT '',
                active_revision_id TEXT,
                sync_status TEXT NOT NULL DEFAULT 'up_to_date',
                has_manual_edits INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(task_id, track_type, output_clip_id),
                FOREIGN KEY(task_id) REFERENCES tasks(id),
                FOREIGN KEY(output_clip_id) REFERENCES output_clip(id),
                FOREIGN KEY(source_track_id) REFERENCES subtitle_tracks(id)
            );

            CREATE TABLE IF NOT EXISTS subtitle_revisions (
                id TEXT PRIMARY KEY,
                track_id TEXT NOT NULL,
                revision_number INTEGER NOT NULL,
                origin TEXT NOT NULL,
                parent_revision_id TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                note TEXT,
                cue_count INTEGER NOT NULL DEFAULT 0,
                checksum TEXT NOT NULL,
                created_at TEXT NOT NULL,
                approved_at TEXT,
                UNIQUE(track_id, revision_number),
                FOREIGN KEY(track_id) REFERENCES subtitle_tracks(id) ON DELETE CASCADE,
                FOREIGN KEY(parent_revision_id) REFERENCES subtitle_revisions(id)
            );

            CREATE TABLE IF NOT EXISTS subtitle_cues (
                id TEXT PRIMARY KEY,
                revision_id TEXT NOT NULL,
                cue_index INTEGER NOT NULL,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                text TEXT NOT NULL,
                confidence REAL,
                speaker TEXT NOT NULL DEFAULT '',
                source_cue_id TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(revision_id, cue_index),
                FOREIGN KEY(revision_id) REFERENCES subtitle_revisions(id) ON DELETE CASCADE
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
                auth_type TEXT NOT NULL DEFAULT 'browser_profile',
                login_status TEXT NOT NULL DEFAULT 'login_required',
                login_checked_at TEXT,
                login_message TEXT,
                last_login_at TEXT,
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
                schedule_timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                next_attempt_at TEXT,
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
                max_attempts INTEGER NOT NULL DEFAULT 3,
                claimed_at TEXT,
                started_at TEXT,
                finished_at TEXT,
                worker_id TEXT,
                execution_id TEXT,
                execution_phase TEXT,
                retry_of_job_id TEXT,
                platform_url TEXT,
                needs_manual_review INTEGER NOT NULL DEFAULT 0,
                published_at TEXT,
                history_hidden INTEGER NOT NULL DEFAULT 0,
                history_hidden_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id),
                FOREIGN KEY(output_clip_id) REFERENCES output_clip(id),
                FOREIGN KEY(account_id) REFERENCES publish_accounts(id)
            );

            CREATE TABLE IF NOT EXISTS publish_job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                worker_id TEXT,
                error_code TEXT,
                message TEXT,
                payload TEXT,
                occurred_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES publish_jobs(id) ON DELETE CASCADE
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
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                next_attempt_at TEXT,
                lease_owner TEXT,
                lease_token TEXT,
                lease_expires_at TEXT,
                heartbeat_at TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                checkpoint_json TEXT,
                checkpoint_updated_at TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS transcription_runs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                device TEXT NOT NULL DEFAULT '',
                compute_type TEXT NOT NULL DEFAULT '',
                chunk_seconds INTEGER NOT NULL,
                overlap_seconds INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                total_chunks INTEGER NOT NULL DEFAULT 0,
                completed_chunks INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS transcription_chunks (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                result_checksum TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(run_id, chunk_index),
                FOREIGN KEY(run_id) REFERENCES transcription_runs(id) ON DELETE CASCADE,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS ai_analysis_windows (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                transcript_fingerprint TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                window_index INTEGER NOT NULL,
                start_seconds INTEGER NOT NULL,
                end_seconds INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                result_checksum TEXT,
                error_message TEXT,
                next_retry_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(
                    task_id, transcript_fingerprint, provider, model,
                    window_index, start_seconds, end_seconds
                ),
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

            CREATE TABLE IF NOT EXISTS clip_feedback (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                clip_candidate_id TEXT NOT NULL,
                analysis_run_id TEXT,
                selection_profile TEXT NOT NULL DEFAULT 'general',
                decision TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                decision_source TEXT NOT NULL DEFAULT 'explicit_feedback',
                note TEXT,
                title_snapshot TEXT,
                summary_snapshot TEXT,
                start_time TEXT,
                end_time TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id),
                FOREIGN KEY(analysis_run_id) REFERENCES ai_analysis_runs(id)
            );

            CREATE TABLE IF NOT EXISTS content_metric_import_batches (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_filename TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'previewed',
                period_start TEXT,
                period_end TEXT,
                normalized_payload_json TEXT NOT NULL DEFAULT '[]',
                row_count INTEGER NOT NULL DEFAULT 0,
                matched_count INTEGER NOT NULL DEFAULT 0,
                ambiguous_count INTEGER NOT NULL DEFAULT 0,
                invalid_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                committed_at TEXT,
                expires_at TEXT,
                UNIQUE(account_id, source_kind, source_sha256),
                FOREIGN KEY(account_id) REFERENCES publish_accounts(id)
            );

            CREATE TABLE IF NOT EXISTS douyin_account_daily_metric_snapshots (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                metric_date TEXT NOT NULL,
                post_count INTEGER NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0,
                like_count INTEGER NOT NULL DEFAULT 0,
                share_count INTEGER NOT NULL DEFAULT 0,
                comment_count INTEGER NOT NULL DEFAULT 0,
                five_second_completion_rate REAL,
                two_second_bounce_rate REAL,
                cover_click_rate REAL,
                average_watch_seconds REAL,
                created_at TEXT NOT NULL,
                UNIQUE(batch_id, metric_date),
                FOREIGN KEY(batch_id) REFERENCES content_metric_import_batches(id) ON DELETE CASCADE,
                FOREIGN KEY(account_id) REFERENCES publish_accounts(id)
            );

            CREATE TABLE IF NOT EXISTS douyin_item_metric_snapshots (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                publish_job_id TEXT,
                account_id TEXT NOT NULL,
                aweme_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                published_at TEXT,
                duration_seconds REAL,
                captured_at TEXT NOT NULL,
                play_count INTEGER,
                like_count INTEGER,
                comment_count INTEGER,
                share_count INTEGER,
                collect_count INTEGER,
                completion_rate REAL,
                five_second_completion_rate REAL,
                two_second_bounce_rate REAL,
                cover_click_rate REAL,
                average_watch_seconds REAL,
                home_visit_count INTEGER,
                follower_gain_count INTEGER,
                content_genre TEXT,
                audit_status TEXT,
                match_status TEXT NOT NULL DEFAULT 'unmatched',
                match_method TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(batch_id, aweme_id),
                FOREIGN KEY(batch_id) REFERENCES content_metric_import_batches(id) ON DELETE CASCADE,
                FOREIGN KEY(publish_job_id) REFERENCES publish_jobs(id),
                FOREIGN KEY(account_id) REFERENCES publish_accounts(id)
            );

            CREATE TABLE IF NOT EXISTS content_improvement_experiments (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                recommendation_id TEXT NOT NULL,
                diagnosis_code TEXT NOT NULL,
                title TEXT NOT NULL,
                hypothesis TEXT NOT NULL,
                action_text TEXT NOT NULL,
                primary_metric TEXT NOT NULL,
                primary_direction TEXT NOT NULL,
                guardrail_metrics_json TEXT NOT NULL DEFAULT '[]',
                baseline_batch_id TEXT NOT NULL,
                baseline_json TEXT NOT NULL,
                target_sample_size INTEGER NOT NULL DEFAULT 20,
                minimum_baseline_size INTEGER NOT NULL DEFAULT 20,
                minimum_weeks INTEGER NOT NULL DEFAULT 3,
                status TEXT NOT NULL DEFAULT 'active',
                decision TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(account_id, recommendation_id),
                FOREIGN KEY(account_id) REFERENCES publish_accounts(id),
                FOREIGN KEY(baseline_batch_id) REFERENCES content_metric_import_batches(id)
            );

            CREATE TABLE IF NOT EXISTS content_improvement_experiment_items (
                id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                publish_job_id TEXT NOT NULL UNIQUE,
                assigned_at TEXT NOT NULL,
                FOREIGN KEY(experiment_id) REFERENCES content_improvement_experiments(id) ON DELETE CASCADE,
                FOREIGN KEY(publish_job_id) REFERENCES publish_jobs(id)
            );
            """
        )
        _migrate_tasks_table(connection)
        _migrate_clip_candidates_table(connection)
        _migrate_output_clip_table(connection)
        _migrate_ai_analysis_runs_table(connection)
        _migrate_subtitle_style_presets_table(connection)
        _migrate_subtitle_jobs_table(connection)
        _migrate_subtitle_editor_tables(connection)
        _migrate_publish_platform_configs_table(connection)
        _migrate_publish_accounts_table(connection)
        _migrate_publish_jobs_table(connection)
        _migrate_publish_job_events_table(connection)
        _restore_legacy_user_cancelled_publish_jobs(connection)
        _migrate_workflow_jobs_table(connection)
        _guard_unfenced_running_workflow_jobs(connection)
        _migrate_transcription_tables(connection)
        _migrate_ai_analysis_windows_table(connection)
        _migrate_cut_runs_table(connection)
        _seed_ai_prompt_presets(connection)
        _seed_subtitle_style_preset(connection)
        _seed_publish_platform_configs(connection)
        _create_indexes(connection)
        connection.commit()
        _run_schema_migrations(connection)


def _requires_long_live_schema_migration(database_path) -> bool:
    """只对已经存在且确实缺少新结构的数据库创建一次迁移前备份。"""
    if not database_path.exists() or database_path.stat().st_size == 0:
        return False
    try:
        connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        task_columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_jobs)").fetchall()}
        table_names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        if "connection" in locals():
            connection.close()
    return (
        "highlight_density_per_hour" not in task_columns
        or "lease_owner" not in job_columns
        or "transcription_runs" not in table_names
        or "transcription_chunks" not in table_names
        or "ai_analysis_windows" not in table_names
    )


def _requires_workflow_job_fencing_migration(database_path) -> bool:
    """已有 Workflow Job 表缺少 claim 代际 token 时，先创建可移植备份。"""
    if not database_path.exists() or database_path.stat().st_size == 0:
        return False
    connection = None
    try:
        connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_jobs)").fetchall()}
    finally:
        if connection is not None:
            connection.close()
    return bool(columns) and "lease_token" not in columns


def _requires_subtitle_editor_schema_migration(database_path) -> bool:
    """已有数据库缺少字幕 revision 结构时，先做在线备份。"""
    if not database_path.exists() or database_path.stat().st_size == 0:
        return False
    connection = None
    try:
        connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        output_columns = {row[1] for row in connection.execute("PRAGMA table_info(output_clip)").fetchall()}
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(subtitle_jobs)").fetchall()}
        table_names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        if connection:
            connection.close()
    return (
        "source_start_ms" not in output_columns
        or "revision_id" not in job_columns
        or not {"subtitle_tracks", "subtitle_revisions", "subtitle_cues"} <= table_names
    )


def _requires_subtitle_auto_schema_migration(database_path) -> bool:
    """已有字幕任务缺少异步渲染验证字段时，迁移前先备份。"""
    if not database_path.exists() or database_path.stat().st_size == 0:
        return False
    connection = None
    try:
        connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(subtitle_jobs)").fetchall()}
    finally:
        if connection:
            connection.close()
    return bool(columns) and not {
        "workflow_job_id",
        "validation_status",
        "validation_json",
        "encoder",
        "verified_at",
    } <= columns


def _requires_publish_active_index_migration(database_path) -> bool:
    """已有发布表尚未完成 P1.3c 迁移时，启动写入前先做可移植备份。"""
    if not database_path.exists() or database_path.stat().st_size == 0:
        return False
    connection = None
    try:
        connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        publish_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'publish_jobs'"
        ).fetchone()
        if publish_table is None:
            return False
        index_row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
            (PUBLISH_ACTIVE_UNIQUE_INDEX_NAME,),
        ).fetchone()
        ledger_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        ledger_row = None
        if ledger_table is not None:
            try:
                ledger_row = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ? AND checksum = ?",
                    (
                        PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION,
                        PUBLISH_ACTIVE_INDEX_MIGRATION_CHECKSUM,
                    ),
                ).fetchone()
            except sqlite3.Error:
                # 异常账本同样属于迁移风险：先要求备份，后续由结构验证给出明确错误。
                return True
        return index_row is None or ledger_row is None
    finally:
        if connection is not None:
            connection.close()


def _requires_task_upload_only_migration(database_path) -> bool:
    """只有确实存在旧 NAS 来源数据时才在归一化前备份。"""
    if not database_path.exists() or database_path.stat().st_size == 0:
        return False
    connection = None
    try:
        connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
        if not {"source_type", "nas_file_path"} <= columns:
            return False
        row = connection.execute(
            """
            SELECT 1 FROM tasks
            WHERE source_type != 'upload'
               OR source_type IS NULL
               OR (nas_file_path IS NOT NULL AND TRIM(nas_file_path) != '')
            LIMIT 1
            """
        ).fetchone()
        return row is not None
    finally:
        if connection is not None:
            connection.close()


def _requires_content_review_schema_migration(database_path) -> bool:
    """已有库缺少内容复盘基础结构时，账本迁移前先创建可恢复备份。"""
    if not database_path.exists() or database_path.stat().st_size == 0:
        return False
    connection = None
    try:
        connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        table_names = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "clip_candidates" not in table_names:
            return False
        clip_columns = {row[1] for row in connection.execute("PRAGMA table_info(clip_candidates)").fetchall()}
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(ai_analysis_runs)").fetchall()}
        feedback_columns = {row[1] for row in connection.execute("PRAGMA table_info(clip_feedback)").fetchall()}
        index_names = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        ledger_row = None
        if "schema_migrations" in table_names:
            try:
                ledger_row = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ? AND checksum = ?",
                    (CONTENT_REVIEW_MIGRATION_VERSION, CONTENT_REVIEW_MIGRATION_CHECKSUM),
                ).fetchone()
            except sqlite3.Error:
                return True
        return (
            "source_analysis_run_id" not in clip_columns
            or not {"prompt_version_id", "prompt_text_sha256"} <= run_columns
            or "decision_source" not in feedback_columns
            or not {
                "ai_prompt_versions",
                "content_metric_import_batches",
                "douyin_account_daily_metric_snapshots",
                "douyin_item_metric_snapshots",
            } <= table_names
            or not set(CONTENT_REVIEW_REQUIRED_INDEXES) <= index_names
            or ledger_row is None
        )
    finally:
        if connection is not None:
            connection.close()


def _requires_douyin_item_export_migration(database_path) -> bool:
    """已有作品快照表缺少官方报表字段或账本记录时先创建备份。"""
    if not database_path.exists() or database_path.stat().st_size == 0:
        return False
    connection = None
    try:
        connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        table_names = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "douyin_item_metric_snapshots" not in table_names:
            return False
        item_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(douyin_item_metric_snapshots)").fetchall()
        }
        ledger_row = None
        if "schema_migrations" in table_names:
            try:
                ledger_row = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ? AND checksum = ?",
                    (
                        DOUYIN_ITEM_EXPORT_MIGRATION_VERSION,
                        DOUYIN_ITEM_EXPORT_MIGRATION_CHECKSUM,
                    ),
                ).fetchone()
            except sqlite3.Error:
                return True
        return not set(DOUYIN_ITEM_EXPORT_COLUMNS) <= item_columns or ledger_row is None
    finally:
        if connection is not None:
            connection.close()


def _requires_content_feedback_loop_migration(database_path) -> bool:
    """实验闭环表或账本缺失时，启动前先生成安全备份。"""
    if not database_path.exists() or database_path.stat().st_size == 0:
        return False
    connection = None
    try:
        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=10,
        )
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "content_metric_import_batches" not in table_names:
            return False
        index_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        ledger_row = None
        if "schema_migrations" in table_names:
            try:
                ledger_row = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ? AND checksum = ?",
                    (
                        CONTENT_FEEDBACK_LOOP_MIGRATION_VERSION,
                        CONTENT_FEEDBACK_LOOP_MIGRATION_CHECKSUM,
                    ),
                ).fetchone()
            except sqlite3.Error:
                return True
        return (
            not set(CONTENT_FEEDBACK_LOOP_REQUIRED_TABLES) <= table_names
            or not set(CONTENT_FEEDBACK_LOOP_REQUIRED_INDEXES) <= index_names
            or ledger_row is None
        )
    finally:
        if connection is not None:
            connection.close()


def _requires_ai_prompt_version_fk_migration(database_path) -> bool:
    """旧 AI Run 表缺少 Prompt 外键或新迁移账本时，重建前先备份。"""
    if not database_path.exists() or database_path.stat().st_size == 0:
        return False
    connection = None
    try:
        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=10,
        )
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "ai_analysis_runs" not in table_names:
            return False
        ledger_row = None
        if "schema_migrations" in table_names:
            try:
                ledger_row = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ? AND checksum = ?",
                    (
                        AI_PROMPT_VERSION_FK_MIGRATION_VERSION,
                        AI_PROMPT_VERSION_FK_MIGRATION_CHECKSUM,
                    ),
                ).fetchone()
            except sqlite3.Error:
                return True
        return not _has_ai_prompt_version_fk(connection) or ledger_row is None
    finally:
        if connection is not None:
            connection.close()


def _has_ai_prompt_version_fk(connection: sqlite3.Connection) -> bool:
    for row in connection.execute("PRAGMA foreign_key_list(ai_analysis_runs)").fetchall():
        table_name = row["table"] if isinstance(row, sqlite3.Row) else row[2]
        source_column = row["from"] if isinstance(row, sqlite3.Row) else row[3]
        target_column = row["to"] if isinstance(row, sqlite3.Row) else row[4]
        on_update = row["on_update"] if isinstance(row, sqlite3.Row) else row[5]
        on_delete = row["on_delete"] if isinstance(row, sqlite3.Row) else row[6]
        if (
            table_name == "ai_prompt_versions"
            and source_column == "prompt_version_id"
            and target_column == "id"
            and str(on_update).upper() == "NO ACTION"
            and str(on_delete).upper() == "NO ACTION"
        ):
            return True
    return False


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
        "CREATE INDEX IF NOT EXISTS idx_clip_feedback_profile_created ON clip_feedback(selection_profile, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_clip_feedback_task_clip ON clip_feedback(task_id, clip_candidate_id)",
        # 字幕任务（按任务、输出切片、状态）
        "CREATE INDEX IF NOT EXISTS idx_subtitle_jobs_task_output_status ON subtitle_jobs(task_id, output_clip_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_subtitle_jobs_workflow_job ON subtitle_jobs(workflow_job_id)",
        "CREATE INDEX IF NOT EXISTS idx_subtitle_tracks_task_type ON subtitle_tracks(task_id, track_type, is_active)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_subtitle_tracks_active_source ON subtitle_tracks(task_id) WHERE track_type = 'source' AND is_active = 1",
        "CREATE INDEX IF NOT EXISTS idx_subtitle_revisions_track_created ON subtitle_revisions(track_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_subtitle_cues_revision_time ON subtitle_cues(revision_id, start_ms, cue_index)",
        # 发布任务（按状态、平台、时间；按任务、输出切片）
        "CREATE INDEX IF NOT EXISTS idx_publish_jobs_status_platform_created ON publish_jobs(status, platform, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_publish_jobs_task_output ON publish_jobs(task_id, output_clip_id)",
        "CREATE INDEX IF NOT EXISTS idx_publish_jobs_status_scheduled ON publish_jobs(status, scheduled_at)",
        "CREATE INDEX IF NOT EXISTS idx_publish_jobs_due_retry ON publish_jobs(status, next_attempt_at, scheduled_at)",
        "CREATE INDEX IF NOT EXISTS idx_publish_jobs_execution ON publish_jobs(execution_id)",
        "CREATE INDEX IF NOT EXISTS idx_publish_jobs_history_visibility ON publish_jobs(history_hidden, platform, status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_publish_job_events_job_time ON publish_job_events(job_id, occurred_at)",
        "CREATE INDEX IF NOT EXISTS idx_workflow_jobs_claim ON workflow_jobs(status, next_attempt_at, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_workflow_jobs_task_type_status ON workflow_jobs(task_id, job_type, status)",
        "CREATE INDEX IF NOT EXISTS idx_transcription_runs_task_active ON transcription_runs(task_id, is_active, updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_transcription_chunks_run_status ON transcription_chunks(run_id, status, chunk_index)",
        "CREATE INDEX IF NOT EXISTS idx_ai_analysis_windows_resume ON ai_analysis_windows(task_id, transcript_fingerprint, provider, model, status, window_index)",
        # OAuth state 过期清理
        "CREATE INDEX IF NOT EXISTS idx_oauth_states_expires ON oauth_states(expires_at)",
    ]
    for sql in indexes:
        connection.execute(sql)


def _normalize_schema_sql(sql: str | None) -> str:
    normalized = " ".join(str(sql or "").strip().rstrip(";").lower().split())
    return normalized.replace(" if not exists ", " ")


def _ensure_schema_migrations_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    table_info = connection.execute("PRAGMA table_info(schema_migrations)").fetchall()
    columns = {row[1] for row in table_info}
    required_columns = {"version", "name", "checksum", "applied_at"}
    missing_columns = sorted(required_columns - columns)
    if missing_columns:
        raise SchemaMigrationError(
            "schema_migrations 账本结构不完整，缺少字段：" + ", ".join(missing_columns)
        )
    version_column = next(row for row in table_info if row[1] == "version")
    if version_column[5] != 1:
        raise SchemaMigrationError("schema_migrations.version 不是主键，已拒绝继续迁移")


def _assert_no_duplicate_active_publish_jobs(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        f"""
        SELECT output_clip_id, platform, publish_mode, COUNT(*) AS duplicate_count
        FROM publish_jobs
        WHERE status IN ({PUBLISH_UNIQUE_ACTIVE_STATUS_SQL})
          AND output_clip_id IS NOT NULL AND output_clip_id <> ''
        GROUP BY output_clip_id, platform, publish_mode
        HAVING COUNT(*) > 1
        ORDER BY output_clip_id, platform, publish_mode
        LIMIT 5
        """
    ).fetchall()
    if not rows:
        return
    samples = "; ".join(
        f"{row['output_clip_id']}/{row['platform']}/{row['publish_mode']} x{row['duplicate_count']}"
        for row in rows
    )
    raise SchemaMigrationError(
        "检测到重复的活动发布任务，拒绝建立唯一索引；请先人工核对后再迁移。示例：" + samples
    )


def _verify_publish_active_unique_index(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        (PUBLISH_ACTIVE_UNIQUE_INDEX_NAME,),
    ).fetchone()
    if row is None:
        raise SchemaMigrationError(
            f"关键唯一索引 {PUBLISH_ACTIVE_UNIQUE_INDEX_NAME} 不存在，已拒绝启动"
        )
    actual_sql = row["sql"] if isinstance(row, sqlite3.Row) else row[0]
    if _normalize_schema_sql(actual_sql) != _normalize_schema_sql(PUBLISH_ACTIVE_UNIQUE_INDEX_SQL):
        raise SchemaMigrationError(
            f"关键唯一索引 {PUBLISH_ACTIVE_UNIQUE_INDEX_NAME} 定义发生漂移，已拒绝启动"
        )


def _verify_publish_active_unique_index_migration(connection: sqlite3.Connection) -> None:
    _verify_publish_active_unique_index(connection)
    legacy_row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
        (PUBLISH_ACTIVE_UNIQUE_INDEX_LEGACY_NAME,),
    ).fetchone()
    if legacy_row is not None:
        raise SchemaMigrationError(
            f"旧版唯一索引 {PUBLISH_ACTIVE_UNIQUE_INDEX_LEGACY_NAME} 仍存在，迁移未完整完成"
        )


def _apply_publish_active_unique_index_migration(connection: sqlite3.Connection) -> None:
    # 先建立和验证新版索引；任一步失败时旧版索引仍保留在同一事务中。
    _assert_no_duplicate_active_publish_jobs(connection)
    connection.execute(PUBLISH_ACTIVE_UNIQUE_INDEX_SQL)
    _verify_publish_active_unique_index(connection)
    connection.execute(f"DROP INDEX IF EXISTS {PUBLISH_ACTIVE_UNIQUE_INDEX_LEGACY_NAME}")


def _apply_task_upload_only_migration(connection: sqlite3.Connection) -> None:
    connection.execute(TASK_UPLOAD_ONLY_MIGRATION_SQL)


def _verify_task_upload_only_migration(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """
        SELECT 1 FROM tasks
        WHERE source_type != 'upload'
           OR source_type IS NULL
           OR (nas_file_path IS NOT NULL AND TRIM(nas_file_path) != '')
        LIMIT 1
        """
    ).fetchone()
    if row is not None:
        raise SchemaMigrationError("仍存在未归一化的 NAS 视频来源记录")


def _apply_content_review_migration(connection: sqlite3.Connection) -> None:
    clip_columns = _get_table_columns(connection, "clip_candidates")
    if "source_analysis_run_id" not in clip_columns:
        connection.execute("ALTER TABLE clip_candidates ADD COLUMN source_analysis_run_id TEXT")

    run_columns = _get_table_columns(connection, "ai_analysis_runs")
    if "prompt_version_id" not in run_columns:
        connection.execute("ALTER TABLE ai_analysis_runs ADD COLUMN prompt_version_id TEXT")
    if "prompt_text_sha256" not in run_columns:
        connection.execute("ALTER TABLE ai_analysis_runs ADD COLUMN prompt_text_sha256 TEXT")

    feedback_columns = _get_table_columns(connection, "clip_feedback")
    if "decision_source" not in feedback_columns:
        connection.execute(
            "ALTER TABLE clip_feedback ADD COLUMN decision_source "
            "TEXT NOT NULL DEFAULT 'explicit_feedback'"
        )

    schema_sql = """
        CREATE TABLE IF NOT EXISTS ai_prompt_versions (
            id TEXT PRIMARY KEY,
            preset_id TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            preset_name_snapshot TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(preset_id, version_number),
            UNIQUE(preset_id, prompt_sha256),
            FOREIGN KEY(preset_id) REFERENCES ai_prompt_presets(id)
        );

        CREATE TABLE IF NOT EXISTS content_metric_import_batches (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_filename TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'previewed',
            period_start TEXT,
            period_end TEXT,
            normalized_payload_json TEXT NOT NULL DEFAULT '[]',
            row_count INTEGER NOT NULL DEFAULT 0,
            matched_count INTEGER NOT NULL DEFAULT 0,
            ambiguous_count INTEGER NOT NULL DEFAULT 0,
            invalid_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            committed_at TEXT,
            expires_at TEXT,
            UNIQUE(account_id, source_kind, source_sha256),
            FOREIGN KEY(account_id) REFERENCES publish_accounts(id)
        );

        CREATE TABLE IF NOT EXISTS douyin_account_daily_metric_snapshots (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            metric_date TEXT NOT NULL,
            post_count INTEGER NOT NULL DEFAULT 0,
            play_count INTEGER NOT NULL DEFAULT 0,
            like_count INTEGER NOT NULL DEFAULT 0,
            share_count INTEGER NOT NULL DEFAULT 0,
            comment_count INTEGER NOT NULL DEFAULT 0,
            five_second_completion_rate REAL,
            two_second_bounce_rate REAL,
            cover_click_rate REAL,
            average_watch_seconds REAL,
            created_at TEXT NOT NULL,
            UNIQUE(batch_id, metric_date),
            FOREIGN KEY(batch_id) REFERENCES content_metric_import_batches(id) ON DELETE CASCADE,
            FOREIGN KEY(account_id) REFERENCES publish_accounts(id)
        );

        CREATE TABLE IF NOT EXISTS douyin_item_metric_snapshots (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            publish_job_id TEXT,
            account_id TEXT NOT NULL,
            aweme_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            duration_seconds REAL,
            captured_at TEXT NOT NULL,
            play_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER,
            share_count INTEGER,
            collect_count INTEGER,
            five_second_completion_rate REAL,
            two_second_bounce_rate REAL,
            cover_click_rate REAL,
            average_watch_seconds REAL,
            match_status TEXT NOT NULL DEFAULT 'unmatched',
            match_method TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(batch_id, aweme_id),
            FOREIGN KEY(batch_id) REFERENCES content_metric_import_batches(id) ON DELETE CASCADE,
            FOREIGN KEY(publish_job_id) REFERENCES publish_jobs(id),
            FOREIGN KEY(account_id) REFERENCES publish_accounts(id)
        );

        CREATE INDEX IF NOT EXISTS idx_clip_candidates_source_analysis_run
            ON clip_candidates(source_analysis_run_id);
        CREATE INDEX IF NOT EXISTS idx_ai_analysis_runs_prompt_version
            ON ai_analysis_runs(prompt_version_id);
        CREATE INDEX IF NOT EXISTS idx_clip_feedback_candidate_created
            ON clip_feedback(clip_candidate_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_content_metric_import_batches_account_created
            ON content_metric_import_batches(account_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_douyin_account_daily_account_date
            ON douyin_account_daily_metric_snapshots(account_id, metric_date DESC);
        CREATE INDEX IF NOT EXISTS idx_douyin_item_metrics_account_published
            ON douyin_item_metric_snapshots(account_id, published_at DESC);
        CREATE INDEX IF NOT EXISTS idx_douyin_item_metrics_match_status
            ON douyin_item_metric_snapshots(match_status, created_at DESC);
        """
    # sqlite3.executescript() 会隐式提交，账本迁移必须逐条执行以保持同一事务。
    for statement in schema_sql.split(";"):
        normalized = statement.strip()
        if normalized:
            connection.execute(normalized)

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    presets = connection.execute(
        "SELECT id, name, prompt_text FROM ai_prompt_presets ORDER BY slot"
    ).fetchall()
    for preset in presets:
        prompt_text = str(preset["prompt_text"] or "").strip()
        prompt_sha256 = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        existing = connection.execute(
            "SELECT 1 FROM ai_prompt_versions WHERE preset_id = ? AND prompt_sha256 = ?",
            (preset["id"], prompt_sha256),
        ).fetchone()
        if existing is not None:
            continue
        version_number = int(
            connection.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 FROM ai_prompt_versions WHERE preset_id = ?",
                (preset["id"],),
            ).fetchone()[0]
        )
        version_id = f"promptv_{preset['id']}_{version_number:03d}"
        connection.execute(
            """
            INSERT INTO ai_prompt_versions (
                id, preset_id, version_number, preset_name_snapshot,
                prompt_text, prompt_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                preset["id"],
                version_number,
                str(preset["name"] or "未命名方案"),
                prompt_text,
                prompt_sha256,
                now,
            ),
        )

    connection.execute(
        """
        UPDATE clip_candidates
        SET source_analysis_run_id = (
            SELECT MIN(r.id) FROM ai_analysis_runs r WHERE r.task_id = clip_candidates.task_id
        )
        WHERE source_analysis_run_id IS NULL
          AND 1 = (
              SELECT COUNT(*) FROM ai_analysis_runs r WHERE r.task_id = clip_candidates.task_id
          )
        """
    )


def _verify_content_review_migration(connection: sqlite3.Connection) -> None:
    required_columns = {
        "clip_candidates": {"source_analysis_run_id"},
        "ai_analysis_runs": {"prompt_version_id", "prompt_text_sha256"},
        "clip_feedback": {"decision_source"},
    }
    for table_name, expected in required_columns.items():
        missing = expected - _get_table_columns(connection, table_name)
        if missing:
            raise SchemaMigrationError(
                f"内容复盘迁移后的 {table_name} 缺少字段：{', '.join(sorted(missing))}"
            )

    required_tables = {
        "ai_prompt_versions",
        "content_metric_import_batches",
        "douyin_account_daily_metric_snapshots",
        "douyin_item_metric_snapshots",
    }
    actual_tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    missing_tables = sorted(required_tables - actual_tables)
    if missing_tables:
        raise SchemaMigrationError("内容复盘迁移缺少数据表：" + ", ".join(missing_tables))

    actual_indexes = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    }
    missing_indexes = sorted(set(CONTENT_REVIEW_REQUIRED_INDEXES) - actual_indexes)
    if missing_indexes:
        raise SchemaMigrationError("内容复盘迁移缺少索引：" + ", ".join(missing_indexes))

    stale_candidates = connection.execute(
        """
        SELECT 1
        FROM clip_candidates c
        WHERE c.source_analysis_run_id IS NULL
          AND 1 = (SELECT COUNT(*) FROM ai_analysis_runs r WHERE r.task_id = c.task_id)
        LIMIT 1
        """
    ).fetchone()
    if stale_candidates is not None:
        raise SchemaMigrationError("存在可唯一归因但尚未关联 AI 分析记录的历史候选片段")

    for row in connection.execute("SELECT id, prompt_text, prompt_sha256 FROM ai_prompt_versions"):
        actual_hash = hashlib.sha256(str(row["prompt_text"] or "").encode("utf-8")).hexdigest()
        if actual_hash != row["prompt_sha256"]:
            raise SchemaMigrationError(f"Prompt 版本 {row['id']} 的 SHA-256 校验失败")


def _apply_douyin_item_export_migration(connection: sqlite3.Connection) -> None:
    existing_columns = _get_table_columns(connection, "douyin_item_metric_snapshots")
    for column_name, column_type in DOUYIN_ITEM_EXPORT_COLUMNS.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE douyin_item_metric_snapshots ADD COLUMN {column_name} {column_type}"
            )


def _verify_douyin_item_export_migration(connection: sqlite3.Connection) -> None:
    missing = set(DOUYIN_ITEM_EXPORT_COLUMNS) - _get_table_columns(
        connection,
        "douyin_item_metric_snapshots",
    )
    if missing:
        raise SchemaMigrationError(
            "抖音官方作品报表迁移缺少字段：" + ", ".join(sorted(missing))
        )


def _apply_content_feedback_loop_migration(connection: sqlite3.Connection) -> None:
    schema_sql = """
        CREATE TABLE IF NOT EXISTS content_improvement_experiments (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            recommendation_id TEXT NOT NULL,
            diagnosis_code TEXT NOT NULL,
            title TEXT NOT NULL,
            hypothesis TEXT NOT NULL,
            action_text TEXT NOT NULL,
            primary_metric TEXT NOT NULL,
            primary_direction TEXT NOT NULL,
            guardrail_metrics_json TEXT NOT NULL DEFAULT '[]',
            baseline_batch_id TEXT NOT NULL,
            baseline_json TEXT NOT NULL,
            target_sample_size INTEGER NOT NULL DEFAULT 20,
            minimum_baseline_size INTEGER NOT NULL DEFAULT 20,
            minimum_weeks INTEGER NOT NULL DEFAULT 3,
            status TEXT NOT NULL DEFAULT 'active',
            decision TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            UNIQUE(account_id, recommendation_id),
            FOREIGN KEY(account_id) REFERENCES publish_accounts(id),
            FOREIGN KEY(baseline_batch_id) REFERENCES content_metric_import_batches(id)
        );

        CREATE TABLE IF NOT EXISTS content_improvement_experiment_items (
            id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            publish_job_id TEXT NOT NULL UNIQUE,
            assigned_at TEXT NOT NULL,
            FOREIGN KEY(experiment_id) REFERENCES content_improvement_experiments(id) ON DELETE CASCADE,
            FOREIGN KEY(publish_job_id) REFERENCES publish_jobs(id)
        );

        CREATE INDEX IF NOT EXISTS idx_content_experiments_account_status
            ON content_improvement_experiments(account_id, status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_content_experiment_items_experiment
            ON content_improvement_experiment_items(experiment_id, assigned_at DESC);
        """
    # executescript() 会先隐式提交，账本迁移必须逐条执行以保持整体可回滚。
    for statement in schema_sql.split(";"):
        normalized = statement.strip()
        if normalized:
            connection.execute(normalized)


def _verify_content_feedback_loop_migration(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    missing_tables = sorted(set(CONTENT_FEEDBACK_LOOP_REQUIRED_TABLES) - tables)
    missing_indexes = sorted(set(CONTENT_FEEDBACK_LOOP_REQUIRED_INDEXES) - indexes)
    if missing_tables:
        raise SchemaMigrationError(
            "内容实验迁移缺少数据表：" + ", ".join(missing_tables)
        )
    if missing_indexes:
        raise SchemaMigrationError(
            "内容实验迁移缺少索引：" + ", ".join(missing_indexes)
        )


def _apply_ai_prompt_version_fk_migration(connection: sqlite3.Connection) -> None:
    if _has_ai_prompt_version_fk(connection):
        return
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
        raise SchemaMigrationError("重建 AI Run 表前未关闭当前连接的外键检查")

    orphan = connection.execute(
        """
        SELECT r.id, r.prompt_version_id
        FROM ai_analysis_runs r
        LEFT JOIN ai_prompt_versions p ON p.id = r.prompt_version_id
        WHERE r.prompt_version_id IS NOT NULL
          AND TRIM(r.prompt_version_id) != ''
          AND p.id IS NULL
        ORDER BY r.id
        LIMIT 1
        """
    ).fetchone()
    if orphan is not None:
        raise SchemaMigrationError(
            "AI Run 存在无法验证的 Prompt 版本引用，已拒绝自动重建："
            f"{orphan['id']} -> {orphan['prompt_version_id']}"
        )

    table_info = connection.execute("PRAGMA table_info(ai_analysis_runs)").fetchall()
    actual_columns = {row["name"] for row in table_info}
    expected_columns = set(AI_ANALYSIS_RUN_COLUMNS)
    missing_columns = sorted(expected_columns - actual_columns)
    unknown_columns = sorted(actual_columns - expected_columns)
    if missing_columns:
        raise SchemaMigrationError(
            "AI Run 表缺少规范字段，已拒绝自动重建：" + ", ".join(missing_columns)
        )
    if unknown_columns:
        raise SchemaMigrationError(
            "AI Run 表存在未知字段，已拒绝自动重建以避免数据丢失："
            + ", ".join(unknown_columns)
        )

    replacement_table = "ai_analysis_runs_prompt_fk_new"
    replacement_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (replacement_table,),
    ).fetchone()
    if replacement_exists is not None:
        raise SchemaMigrationError(f"检测到残留临时表 {replacement_table}，已拒绝覆盖")

    schema_objects = connection.execute(
        """
        SELECT type, name, sql
        FROM sqlite_master
        WHERE tbl_name = 'ai_analysis_runs'
          AND type IN ('index', 'trigger')
          AND sql IS NOT NULL
        ORDER BY type, name
        """
    ).fetchall()
    original_count = connection.execute("SELECT COUNT(*) FROM ai_analysis_runs").fetchone()[0]
    connection.execute(
        f"""
        CREATE TABLE {replacement_table} (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            run_number INTEGER NOT NULL,
            provider TEXT NOT NULL,
            provider_label TEXT NOT NULL,
            model TEXT NOT NULL,
            ai_prompt_preset_id TEXT,
            ai_prompt_preset_name TEXT,
            prompt_version_id TEXT,
            prompt_text_sha256 TEXT,
            requested_clip_count INTEGER NOT NULL DEFAULT 5,
            clip_count INTEGER NOT NULL DEFAULT 0,
            analysis_summary TEXT,
            fallback_notice TEXT,
            analysis_payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(task_id) REFERENCES tasks(id),
            FOREIGN KEY(prompt_version_id) REFERENCES ai_prompt_versions(id)
        )
        """
    )
    columns_sql = ", ".join(AI_ANALYSIS_RUN_COLUMNS)
    connection.execute(
        f"INSERT INTO {replacement_table} ({columns_sql}) "
        f"SELECT {columns_sql} FROM ai_analysis_runs"
    )
    copied_count = connection.execute(
        f"SELECT COUNT(*) FROM {replacement_table}"
    ).fetchone()[0]
    if copied_count != original_count:
        raise SchemaMigrationError(
            f"AI Run 表重建行数不一致：原表 {original_count}，新表 {copied_count}"
        )

    connection.execute("DROP TABLE ai_analysis_runs")
    connection.execute(f"ALTER TABLE {replacement_table} RENAME TO ai_analysis_runs")
    for schema_object in schema_objects:
        connection.execute(schema_object["sql"])


def _verify_ai_prompt_version_fk_migration(connection: sqlite3.Connection) -> None:
    if not _has_ai_prompt_version_fk(connection):
        raise SchemaMigrationError("AI Run 的 Prompt 版本外键不存在或删除语义不一致")
    task_fk_exists = False
    for row in connection.execute("PRAGMA foreign_key_list(ai_analysis_runs)").fetchall():
        if (
            row["table"] == "tasks"
            and row["from"] == "task_id"
            and row["to"] == "id"
            and str(row["on_update"]).upper() == "NO ACTION"
            and str(row["on_delete"]).upper() == "NO ACTION"
        ):
            task_fk_exists = True
            break
    if not task_fk_exists:
        raise SchemaMigrationError("AI Run 表重建后丢失任务外键")

    required_indexes = {
        "idx_ai_analysis_runs_task_created",
        "idx_ai_analysis_runs_prompt_version",
    }
    actual_indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='ai_analysis_runs'"
        ).fetchall()
    }
    missing_indexes = sorted(required_indexes - actual_indexes)
    if missing_indexes:
        raise SchemaMigrationError(
            "AI Run 表重建后缺少索引：" + ", ".join(missing_indexes)
        )

    orphan = connection.execute(
        """
        SELECT 1
        FROM ai_analysis_runs r
        LEFT JOIN ai_prompt_versions p ON p.id = r.prompt_version_id
        WHERE r.prompt_version_id IS NOT NULL
          AND TRIM(r.prompt_version_id) != ''
          AND p.id IS NULL
        LIMIT 1
        """
    ).fetchone()
    if orphan is not None:
        raise SchemaMigrationError("AI Run 表仍存在孤儿 Prompt 版本引用")
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        samples = "; ".join(
            f"{row[0]} rowid={row[1]} parent={row[2]}"
            for row in violations[:5]
        )
        raise SchemaMigrationError("外键检查失败，已拒绝记录迁移账本：" + samples)


def _registered_schema_migrations() -> tuple[SchemaMigration, ...]:
    return (
        SchemaMigration(
            version=PUBLISH_ACTIVE_INDEX_MIGRATION_VERSION,
            name=PUBLISH_ACTIVE_INDEX_MIGRATION_NAME,
            checksum=PUBLISH_ACTIVE_INDEX_MIGRATION_CHECKSUM,
            apply=_apply_publish_active_unique_index_migration,
            verify=_verify_publish_active_unique_index_migration,
        ),
        SchemaMigration(
            version=TASK_UPLOAD_ONLY_MIGRATION_VERSION,
            name=TASK_UPLOAD_ONLY_MIGRATION_NAME,
            checksum=TASK_UPLOAD_ONLY_MIGRATION_CHECKSUM,
            apply=_apply_task_upload_only_migration,
            verify=_verify_task_upload_only_migration,
        ),
        SchemaMigration(
            version=CONTENT_REVIEW_MIGRATION_VERSION,
            name=CONTENT_REVIEW_MIGRATION_NAME,
            checksum=CONTENT_REVIEW_MIGRATION_CHECKSUM,
            apply=_apply_content_review_migration,
            verify=_verify_content_review_migration,
        ),
        SchemaMigration(
            version=DOUYIN_ITEM_EXPORT_MIGRATION_VERSION,
            name=DOUYIN_ITEM_EXPORT_MIGRATION_NAME,
            checksum=DOUYIN_ITEM_EXPORT_MIGRATION_CHECKSUM,
            apply=_apply_douyin_item_export_migration,
            verify=_verify_douyin_item_export_migration,
        ),
        SchemaMigration(
            version=CONTENT_FEEDBACK_LOOP_MIGRATION_VERSION,
            name=CONTENT_FEEDBACK_LOOP_MIGRATION_NAME,
            checksum=CONTENT_FEEDBACK_LOOP_MIGRATION_CHECKSUM,
            apply=_apply_content_feedback_loop_migration,
            verify=_verify_content_feedback_loop_migration,
        ),
        SchemaMigration(
            version=AI_PROMPT_VERSION_FK_MIGRATION_VERSION,
            name=AI_PROMPT_VERSION_FK_MIGRATION_NAME,
            checksum=AI_PROMPT_VERSION_FK_MIGRATION_CHECKSUM,
            apply=_apply_ai_prompt_version_fk_migration,
            verify=_verify_ai_prompt_version_fk_migration,
            requires_foreign_keys_off=True,
        ),
    )


def _run_schema_migrations(connection: sqlite3.Connection) -> None:
    """串行执行有账本的新迁移；历史列探测迁移继续作为兼容层保留。"""
    if connection.in_transaction:
        raise SchemaMigrationError("执行账本迁移前存在未提交事务，已拒绝继续")

    for migration in _registered_schema_migrations():
        version = migration.version
        original_foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        try:
            if migration.requires_foreign_keys_off:
                connection.execute("PRAGMA foreign_keys = OFF")
                if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
                    raise SchemaMigrationError(
                        f"数据库迁移 {version} 无法临时关闭当前连接的外键检查"
                    )
            connection.execute("BEGIN IMMEDIATE")
            _ensure_schema_migrations_table(connection)
            applied = connection.execute(
                "SELECT name, checksum FROM schema_migrations WHERE version = ?",
                (version,),
            ).fetchone()
            if applied is not None:
                applied_checksum = applied["checksum"] if isinstance(applied, sqlite3.Row) else applied[1]
                if applied_checksum != migration.checksum:
                    raise SchemaMigrationError(
                        f"迁移 {version} 的 checksum 与账本不一致，已拒绝启动"
                    )
                migration.verify(connection)
                connection.commit()
                continue

            migration.apply(connection)
            migration.verify(connection)
            connection.execute(
                """
                INSERT INTO schema_migrations (version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    version,
                    migration.name,
                    migration.checksum,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            if isinstance(exc, SchemaMigrationError):
                raise
            raise SchemaMigrationError(f"数据库迁移 {version} 执行失败：{exc}") from exc
        finally:
            if migration.requires_foreign_keys_off and original_foreign_keys:
                if connection.in_transaction:
                    connection.rollback()
                connection.execute("PRAGMA foreign_keys = ON")
                if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                    raise SchemaMigrationError(
                        f"数据库迁移 {version} 后无法恢复当前连接的外键检查"
                    )


def _migrate_tasks_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "tasks")
    migrations = {
        "task_name": "ALTER TABLE tasks ADD COLUMN task_name TEXT",
        "task_dir_name": "ALTER TABLE tasks ADD COLUMN task_dir_name TEXT",
        "source_type": "ALTER TABLE tasks ADD COLUMN source_type TEXT NOT NULL DEFAULT 'upload'",
        "platform": "ALTER TABLE tasks ADD COLUMN platform TEXT NOT NULL DEFAULT 'general'",
        "original_video_path": "ALTER TABLE tasks ADD COLUMN original_video_path TEXT",
        "nas_file_path": "ALTER TABLE tasks ADD COLUMN nas_file_path TEXT",
        "max_clip_duration": "ALTER TABLE tasks ADD COLUMN max_clip_duration INTEGER NOT NULL DEFAULT 10",
        "candidate_clip_count": "ALTER TABLE tasks ADD COLUMN candidate_clip_count INTEGER NOT NULL DEFAULT 12",
        "selection_profile": "ALTER TABLE tasks ADD COLUMN selection_profile TEXT NOT NULL DEFAULT 'general'",
        "final_clip_target": "ALTER TABLE tasks ADD COLUMN final_clip_target INTEGER NOT NULL DEFAULT 5",
        "highlight_density_per_hour": "ALTER TABLE tasks ADD COLUMN highlight_density_per_hour INTEGER NOT NULL DEFAULT 4",
        "highlight_total_limit": "ALTER TABLE tasks ADD COLUMN highlight_total_limit INTEGER NOT NULL DEFAULT 30",
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
        UPDATE tasks SET selection_profile = 'general' WHERE selection_profile NOT IN ('general', 'variety_comedy', 'long_live_talk') OR selection_profile IS NULL OR selection_profile = '';
        UPDATE tasks SET final_clip_target = 5 WHERE final_clip_target IS NULL OR final_clip_target < 1 OR final_clip_target > 12;
        UPDATE tasks SET highlight_density_per_hour = 4 WHERE highlight_density_per_hour IS NULL OR highlight_density_per_hour < 1 OR highlight_density_per_hour > 10;
        UPDATE tasks SET highlight_total_limit = 30 WHERE highlight_total_limit IS NULL OR highlight_total_limit < 1 OR highlight_total_limit > 50;
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
            'CLIP_SELECTING', 'VIDEO_CUTTING', 'SUBTITLE_DRAFTING',
            'PENDING_SUBTITLE_REVIEW', 'METADATA_GENERATING',
            'SCHEDULE_CREATING', 'PUBLISH_JOB_CREATING', 'READY_TO_PUBLISH',
            'COMPLETED', 'CANCELLED', 'FAILED_PREPARING_SOURCE', 'FAILED_TRANSCRIBING',
            'FAILED_AI_ANALYZING', 'FAILED_CLIP_SELECTING', 'FAILED_VIDEO_CUTTING',
            'FAILED_SUBTITLE_DRAFTING',
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
        "cover_time_seconds": "ALTER TABLE clip_candidates ADD COLUMN cover_time_seconds REAL",
        "confidence_score": "ALTER TABLE clip_candidates ADD COLUMN confidence_score REAL NOT NULL DEFAULT 0",
        "quality_tier": "ALTER TABLE clip_candidates ADD COLUMN quality_tier TEXT NOT NULL DEFAULT ''",
        "quality_score": "ALTER TABLE clip_candidates ADD COLUMN quality_score REAL NOT NULL DEFAULT 0",
        "text_quality_score": "ALTER TABLE clip_candidates ADD COLUMN text_quality_score REAL NOT NULL DEFAULT 0",
        "humor_score": "ALTER TABLE clip_candidates ADD COLUMN humor_score REAL NOT NULL DEFAULT 0",
        "completeness_score": "ALTER TABLE clip_candidates ADD COLUMN completeness_score REAL NOT NULL DEFAULT 0",
        "audio_reaction_score": "ALTER TABLE clip_candidates ADD COLUMN audio_reaction_score REAL NOT NULL DEFAULT 0",
        "topic_key": "ALTER TABLE clip_candidates ADD COLUMN topic_key TEXT",
        "key_moment_time": "ALTER TABLE clip_candidates ADD COLUMN key_moment_time TEXT",
        "quality_evidence_json": "ALTER TABLE clip_candidates ADD COLUMN quality_evidence_json TEXT",
        "rejection_reason": "ALTER TABLE clip_candidates ADD COLUMN rejection_reason TEXT",
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
        "source_start_ms": "ALTER TABLE output_clip ADD COLUMN source_start_ms INTEGER",
        "source_end_ms": "ALTER TABLE output_clip ADD COLUMN source_end_ms INTEGER",
        "source_duration_ms": "ALTER TABLE output_clip ADD COLUMN source_duration_ms INTEGER",
        "source_fingerprint": "ALTER TABLE output_clip ADD COLUMN source_fingerprint TEXT",
        "snapshot_source": "ALTER TABLE output_clip ADD COLUMN snapshot_source TEXT NOT NULL DEFAULT 'legacy_inferred'",
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
        "outline_width": "ALTER TABLE subtitle_style_presets ADD COLUMN outline_width REAL NOT NULL DEFAULT 3",
        "shadow_depth": "ALTER TABLE subtitle_style_presets ADD COLUMN shadow_depth REAL NOT NULL DEFAULT 1",
        "safe_area_percent": "ALTER TABLE subtitle_style_presets ADD COLUMN safe_area_percent REAL NOT NULL DEFAULT 5",
        "speaker_styles_json": "ALTER TABLE subtitle_style_presets ADD COLUMN speaker_styles_json TEXT NOT NULL DEFAULT '{}'",
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
        "revision_id": "ALTER TABLE subtitle_jobs ADD COLUMN revision_id TEXT",
        "workflow_job_id": "ALTER TABLE subtitle_jobs ADD COLUMN workflow_job_id TEXT",
        "style_preset_id": "ALTER TABLE subtitle_jobs ADD COLUMN style_preset_id TEXT",
        "status": "ALTER TABLE subtitle_jobs ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'",
        "subtitle_file_path": "ALTER TABLE subtitle_jobs ADD COLUMN subtitle_file_path TEXT",
        "output_file_path": "ALTER TABLE subtitle_jobs ADD COLUMN output_file_path TEXT",
        "error_message": "ALTER TABLE subtitle_jobs ADD COLUMN error_message TEXT",
        "validation_status": "ALTER TABLE subtitle_jobs ADD COLUMN validation_status TEXT NOT NULL DEFAULT 'legacy_unverified'",
        "validation_json": "ALTER TABLE subtitle_jobs ADD COLUMN validation_json TEXT NOT NULL DEFAULT '{}'",
        "encoder": "ALTER TABLE subtitle_jobs ADD COLUMN encoder TEXT NOT NULL DEFAULT ''",
        "verified_at": "ALTER TABLE subtitle_jobs ADD COLUMN verified_at TEXT",
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


def _migrate_subtitle_editor_tables(connection: sqlite3.Connection) -> None:
    """创建不可变字幕轨、revision 与 cue 数据层。"""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS subtitle_tracks (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, track_type TEXT NOT NULL,
            output_clip_id TEXT, name TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'zh-CN',
            source_track_id TEXT, source_revision_id TEXT, source_fingerprint TEXT NOT NULL DEFAULT '',
            active_revision_id TEXT,
            sync_status TEXT NOT NULL DEFAULT 'up_to_date',
            has_manual_edits INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(task_id, track_type, output_clip_id),
            FOREIGN KEY(task_id) REFERENCES tasks(id),
            FOREIGN KEY(output_clip_id) REFERENCES output_clip(id),
            FOREIGN KEY(source_track_id) REFERENCES subtitle_tracks(id)
        );
        CREATE TABLE IF NOT EXISTS subtitle_revisions (
            id TEXT PRIMARY KEY, track_id TEXT NOT NULL, revision_number INTEGER NOT NULL,
            origin TEXT NOT NULL, parent_revision_id TEXT, status TEXT NOT NULL DEFAULT 'draft',
            note TEXT, cue_count INTEGER NOT NULL DEFAULT 0, checksum TEXT NOT NULL,
            created_at TEXT NOT NULL, approved_at TEXT,
            UNIQUE(track_id, revision_number),
            FOREIGN KEY(track_id) REFERENCES subtitle_tracks(id) ON DELETE CASCADE,
            FOREIGN KEY(parent_revision_id) REFERENCES subtitle_revisions(id)
        );
        CREATE TABLE IF NOT EXISTS subtitle_cues (
            id TEXT PRIMARY KEY, revision_id TEXT NOT NULL, cue_index INTEGER NOT NULL,
            start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, text TEXT NOT NULL,
            confidence REAL, speaker TEXT NOT NULL DEFAULT '', source_cue_id TEXT,
            created_at TEXT NOT NULL, UNIQUE(revision_id, cue_index),
            FOREIGN KEY(revision_id) REFERENCES subtitle_revisions(id) ON DELETE CASCADE
        );
        """
    )
    track_columns = _get_table_columns(connection, "subtitle_tracks")
    if "source_fingerprint" not in track_columns:
        connection.execute(
            "ALTER TABLE subtitle_tracks ADD COLUMN source_fingerprint TEXT NOT NULL DEFAULT ''"
        )


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
        "auth_type": "ALTER TABLE publish_accounts ADD COLUMN auth_type TEXT NOT NULL DEFAULT 'browser_profile'",
        "login_status": "ALTER TABLE publish_accounts ADD COLUMN login_status TEXT NOT NULL DEFAULT 'login_required'",
        "login_checked_at": "ALTER TABLE publish_accounts ADD COLUMN login_checked_at TEXT",
        "login_message": "ALTER TABLE publish_accounts ADD COLUMN login_message TEXT",
        "last_login_at": "ALTER TABLE publish_accounts ADD COLUMN last_login_at TEXT",
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
        "schedule_timezone": "ALTER TABLE publish_jobs ADD COLUMN schedule_timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai'",
        "timezone": "ALTER TABLE publish_jobs ADD COLUMN timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai'",
        "next_attempt_at": "ALTER TABLE publish_jobs ADD COLUMN next_attempt_at TEXT",
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
        "max_attempts": "ALTER TABLE publish_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3",
        "claimed_at": "ALTER TABLE publish_jobs ADD COLUMN claimed_at TEXT",
        "started_at": "ALTER TABLE publish_jobs ADD COLUMN started_at TEXT",
        "finished_at": "ALTER TABLE publish_jobs ADD COLUMN finished_at TEXT",
        "worker_id": "ALTER TABLE publish_jobs ADD COLUMN worker_id TEXT",
        "execution_id": "ALTER TABLE publish_jobs ADD COLUMN execution_id TEXT",
        "execution_phase": "ALTER TABLE publish_jobs ADD COLUMN execution_phase TEXT",
        "retry_of_job_id": "ALTER TABLE publish_jobs ADD COLUMN retry_of_job_id TEXT",
        "platform_url": "ALTER TABLE publish_jobs ADD COLUMN platform_url TEXT",
        "needs_manual_review": "ALTER TABLE publish_jobs ADD COLUMN needs_manual_review INTEGER NOT NULL DEFAULT 0",
        "published_at": "ALTER TABLE publish_jobs ADD COLUMN published_at TEXT",
        "history_hidden": "ALTER TABLE publish_jobs ADD COLUMN history_hidden INTEGER NOT NULL DEFAULT 0",
        "history_hidden_at": "ALTER TABLE publish_jobs ADD COLUMN history_hidden_at TEXT",
        "created_at": "ALTER TABLE publish_jobs ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "updated_at": "ALTER TABLE publish_jobs ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)

    columns = _get_table_columns(connection, "publish_jobs")
    requires_serialized_migration = _publish_database_requires_data_migration(connection)
    if requires_serialized_migration:
        # 提交上方的加列操作，再用 SQLite 写锁串行化“备份 + 数据修复”。
        # 备份服务会使用独立只读连接，因此快照仍是数据修复前的完整状态。
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
    try:
        _run_publish_jobs_data_migrations(connection, columns)
    except Exception:
        if requires_serialized_migration:
            connection.rollback()
        raise
    else:
        if requires_serialized_migration:
            connection.commit()


def _run_publish_jobs_data_migrations(
    connection: sqlite3.Connection,
    columns: set[str],
) -> None:
    _backup_publish_database_before_data_migration(connection)
    _migrate_publish_platform_and_mode_values(connection)
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
    if "history_hidden" in columns:
        connection.execute("UPDATE publish_jobs SET history_hidden = 0 WHERE history_hidden IS NULL")

    if "status" in columns:
        status_migrations = (
            "UPDATE publish_jobs SET status = 'DRAFT' WHERE status = 'draft'",
            "UPDATE publish_jobs SET status = 'SCHEDULED' WHERE status IN ('ready', 'scheduled')",
            "UPDATE publish_jobs SET status = 'PUBLISHING' WHERE status = 'publishing'",
            "UPDATE publish_jobs SET status = 'PUBLISHED' WHERE status = 'published'",
            "UPDATE publish_jobs SET status = 'EXPORTED' WHERE status = 'exported'",
            "UPDATE publish_jobs SET status = 'FAILED' WHERE status = 'failed'",
            "UPDATE publish_jobs SET status = 'CANCELLED' WHERE status = 'cancelled'",
            "UPDATE publish_jobs SET status = 'NEED_REVIEW' WHERE status = 'need_review'",
            """
            UPDATE publish_jobs
            SET status = 'WAITING'
            WHERE status = 'SCHEDULED' AND (scheduled_at IS NULL OR scheduled_at = '')
            """,
            """
            UPDATE publish_jobs
            SET status = 'SCHEDULED'
            WHERE status IS NULL OR status = '' OR status NOT IN (
                'DRAFT', 'SCHEDULED', 'WAITING', 'PUBLISHING',
                'PUBLISHED', 'EXPORTED', 'FAILED', 'CANCELLED', 'NEED_REVIEW'
            )
            """,
        )
        for statement in status_migrations:
            connection.execute(statement)
    _cancel_duplicate_active_publish_jobs(connection)


def _migrate_publish_job_events_table(connection: sqlite3.Connection) -> None:
    columns = _get_table_columns(connection, "publish_job_events")
    if not columns:
        return
    migrations = {
        "job_id": "ALTER TABLE publish_job_events ADD COLUMN job_id TEXT NOT NULL DEFAULT ''",
        "event_type": "ALTER TABLE publish_job_events ADD COLUMN event_type TEXT NOT NULL DEFAULT ''",
        "from_status": "ALTER TABLE publish_job_events ADD COLUMN from_status TEXT",
        "to_status": "ALTER TABLE publish_job_events ADD COLUMN to_status TEXT",
        "worker_id": "ALTER TABLE publish_job_events ADD COLUMN worker_id TEXT",
        "error_code": "ALTER TABLE publish_job_events ADD COLUMN error_code TEXT",
        "message": "ALTER TABLE publish_job_events ADD COLUMN message TEXT",
        "payload": "ALTER TABLE publish_job_events ADD COLUMN payload TEXT",
        "occurred_at": "ALTER TABLE publish_job_events ADD COLUMN occurred_at TEXT NOT NULL DEFAULT ''",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)


def _restore_legacy_user_cancelled_publish_jobs(connection: sqlite3.Connection) -> None:
    """旧版“取消任务”应按新语义回到内容准备，系统取消和主动移出保持不变。"""
    rows = connection.execute(
        """
        SELECT cancelled.id, cancelled.status
        FROM publish_jobs AS cancelled
        WHERE cancelled.status = 'CANCELLED'
          AND (
              cancelled.error_message = '用户取消任务'
              OR cancelled.last_error = '用户取消任务'
          )
          AND cancelled.id = (
              SELECT candidate.id
              FROM publish_jobs AS candidate
              WHERE candidate.output_clip_id = cancelled.output_clip_id
                AND candidate.platform = cancelled.platform
                AND candidate.status = 'CANCELLED'
                AND (
                    candidate.error_message = '用户取消任务'
                    OR candidate.last_error = '用户取消任务'
                )
              ORDER BY COALESCE(NULLIF(candidate.updated_at, ''), candidate.created_at) DESC,
                       candidate.created_at DESC,
                       candidate.id DESC
              LIMIT 1
          )
          AND NOT EXISTS (
              SELECT 1
              FROM publish_jobs AS active
              WHERE active.id <> cancelled.id
                AND active.output_clip_id = cancelled.output_clip_id
                AND active.platform = cancelled.platform
                AND active.status IN ('DRAFT', 'WAITING', 'SCHEDULED', 'PUBLISHING', 'NEED_REVIEW')
          )
        """
    ).fetchall()
    if not rows:
        return

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for row in rows:
        cursor = connection.execute(
            """
            UPDATE publish_jobs
            SET status = 'WAITING', scheduled_at = '', next_attempt_at = NULL,
                claimed_at = NULL, started_at = NULL, finished_at = NULL,
                worker_id = NULL, execution_id = NULL, execution_phase = '',
                error_code = '', error_message = '', last_error = '',
                needs_manual_review = 0, updated_at = ?
            WHERE id = ? AND status = 'CANCELLED'
              AND (error_message = '用户取消任务' OR last_error = '用户取消任务')
            """,
            (now, row["id"]),
        )
        if cursor.rowcount:
            connection.execute(
                """
                INSERT INTO publish_job_events (
                    job_id, event_type, from_status, to_status, message, payload, occurred_at
                ) VALUES (?, 'legacy_cancel_restored', 'CANCELLED', 'WAITING', ?, ?, ?)
                """,
                (
                    row["id"],
                    "旧版取消发送记录已自动返回内容准备",
                    json.dumps(
                        {"scheduled_at_cleared": True, "files_deleted": False},
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )


def _publish_database_requires_data_migration(connection: sqlite3.Connection) -> bool:
    legacy_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM publish_jobs
        WHERE platform NOT IN ('douyin', 'bilibili')
           OR publish_mode NOT IN ('opencli_publish', 'manual_export', 'api_publish', 'local_browser')
        """
    ).fetchone()[0]
    duplicate_count = connection.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT output_clip_id, platform, publish_mode
            FROM publish_jobs
            WHERE status IN ('DRAFT', 'WAITING', 'SCHEDULED', 'PUBLISHING')
            GROUP BY output_clip_id, platform, publish_mode
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    return bool(legacy_count or duplicate_count)


def _backup_publish_database_before_data_migration(connection: sqlite3.Connection) -> None:
    """仅在发现旧值或有效重复任务时创建受限频率的完整迁移前备份。"""
    if not _publish_database_requires_data_migration(connection):
        return
    database_path = settings.database_path
    if not database_path.exists():
        return
    backup_dir = settings.data_dir / "backups"
    create_publish_migration_backup(database_path, backup_dir)


def _provider_target_platform(raw_value: str | None) -> str:
    try:
        payload = json.loads(raw_value or "{}")
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    target = str(payload.get("target_platform") or "").strip().lower()
    return target if target in {"douyin", "bilibili"} else ""


def _migrate_publish_platform_and_mode_values(connection: sqlite3.Connection) -> None:
    default_mode = str(settings.publish_default_mode or "opencli_publish").strip().lower()
    if default_mode not in {"opencli_publish", "manual_export", "api_publish", "local_browser"}:
        default_mode = "opencli_publish"
    rows = connection.execute(
        """
        SELECT publish_jobs.id, publish_jobs.platform, publish_jobs.publish_mode,
               publish_jobs.provider_response, tasks.platform AS task_platform
        FROM publish_jobs
        LEFT JOIN tasks ON tasks.id = publish_jobs.task_id
        WHERE publish_jobs.platform NOT IN ('douyin', 'bilibili')
           OR publish_jobs.publish_mode NOT IN ('opencli_publish', 'manual_export', 'api_publish', 'local_browser')
        """
    ).fetchall()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for row in rows:
        platform = _provider_target_platform(row["provider_response"])
        if not platform:
            task_platform = str(row["task_platform"] or "").strip().lower()
            platform = task_platform if task_platform in {"douyin", "bilibili"} else "douyin"
        old_platform = str(row["platform"] or "").strip().lower()
        old_mode = str(row["publish_mode"] or "").strip().lower()
        mode = old_mode if old_mode in {"opencli_publish", "manual_export", "api_publish", "local_browser"} else default_mode
        if old_platform in {"manual_export", "local_browser"}:
            mode = default_mode
        connection.execute(
            "UPDATE publish_jobs SET platform = ?, publish_mode = ?, updated_at = ? WHERE id = ?",
            (platform, mode, now, row["id"]),
        )


def _cancel_duplicate_active_publish_jobs(connection: sqlite3.Connection) -> None:
    groups = connection.execute(
        """
        SELECT output_clip_id, platform, publish_mode
        FROM publish_jobs
        WHERE status IN ('DRAFT', 'WAITING', 'SCHEDULED', 'PUBLISHING')
        GROUP BY output_clip_id, platform, publish_mode
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for group in groups:
        rows = connection.execute(
            """
            SELECT id, provider_response
            FROM publish_jobs
            WHERE output_clip_id = ? AND platform = ? AND publish_mode = ?
              AND status IN ('DRAFT', 'WAITING', 'SCHEDULED', 'PUBLISHING')
            ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC, created_at DESC, id DESC
            """,
            (group["output_clip_id"], group["platform"], group["publish_mode"]),
        ).fetchall()
        for duplicate in rows[1:]:
            migration_payload = {
                "migration_reason": "duplicate_active_publish_job",
                "message": "迁移时发现同一切片、平台和执行方式的重复未发布任务，已保留最新一条。",
                "previous_provider_response": duplicate["provider_response"] or "",
            }
            connection.execute(
                """
                UPDATE publish_jobs
                SET status = 'CANCELLED', error_code = 'migration_duplicate_cancelled',
                    error_message = ?, last_error = ?, provider_response = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    migration_payload["message"],
                    migration_payload["message"],
                    json.dumps(migration_payload, ensure_ascii=False),
                    now,
                    duplicate["id"],
                ),
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
        "attempt_count": "ALTER TABLE workflow_jobs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
        "max_attempts": "ALTER TABLE workflow_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3",
        "next_attempt_at": "ALTER TABLE workflow_jobs ADD COLUMN next_attempt_at TEXT",
        "lease_owner": "ALTER TABLE workflow_jobs ADD COLUMN lease_owner TEXT",
        "lease_token": "ALTER TABLE workflow_jobs ADD COLUMN lease_token TEXT",
        "lease_expires_at": "ALTER TABLE workflow_jobs ADD COLUMN lease_expires_at TEXT",
        "heartbeat_at": "ALTER TABLE workflow_jobs ADD COLUMN heartbeat_at TEXT",
        "cancel_requested": "ALTER TABLE workflow_jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0",
        "checkpoint_json": "ALTER TABLE workflow_jobs ADD COLUMN checkpoint_json TEXT",
        "checkpoint_updated_at": "ALTER TABLE workflow_jobs ADD COLUMN checkpoint_updated_at TEXT",
    }
    for column, statement in migrations.items():
        if column in columns:
            continue
        try:
            connection.execute(statement)
        except sqlite3.OperationalError:
            # 两个本地进程可能同时初始化同一个旧库。另一个进程若已完成
            # 同一 ADD COLUMN，本进程把它视为幂等成功；其他错误继续抛出。
            if column not in _get_table_columns(connection, "workflow_jobs"):
                raise
        columns = _get_table_columns(connection, "workflow_jobs")


def _guard_unfenced_running_workflow_jobs(connection: sqlite3.Connection) -> None:
    """部署切换不完整时拒绝启动，避免旧 Worker 与新 Worker 重叠写入。"""
    rows = connection.execute(
        """
        SELECT id
        FROM workflow_jobs
        WHERE status = 'running' AND (lease_token IS NULL OR lease_token = '')
        ORDER BY created_at
        LIMIT 10
        """
    ).fetchall()
    if not rows:
        return
    job_ids = ", ".join(str(row["id"]) for row in rows)
    raise RuntimeError(
        "检测到未带 lease_token 的运行中 Workflow Job，已拒绝启动以防旧 Worker 覆盖新执行。"
        f"请先停止旧版本服务并处理这些任务：{job_ids}"
    )


def _migrate_transcription_tables(connection: sqlite3.Connection) -> None:
    """创建转写分块 checkpoint 表；重复启动不会覆盖现有结果。"""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS transcription_runs (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
            provider TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', device TEXT NOT NULL DEFAULT '',
            compute_type TEXT NOT NULL DEFAULT '', chunk_seconds INTEGER NOT NULL,
            overlap_seconds INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'processing',
            total_chunks INTEGER NOT NULL DEFAULT 0, completed_chunks INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 0, error_message TEXT, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, completed_at TEXT, FOREIGN KEY(task_id) REFERENCES tasks(id)
        );
        CREATE TABLE IF NOT EXISTS transcription_chunks (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, task_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', attempt_count INTEGER NOT NULL DEFAULT 0,
            result_json TEXT, result_checksum TEXT, error_message TEXT, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, UNIQUE(run_id, chunk_index),
            FOREIGN KEY(run_id) REFERENCES transcription_runs(id) ON DELETE CASCADE,
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        );
        """
    )


def _migrate_ai_analysis_windows_table(connection: sqlite3.Connection) -> None:
    """创建长直播 AI 窗口 checkpoint 表；成功窗口可跨进程复用。"""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_analysis_windows (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
            transcript_fingerprint TEXT NOT NULL, provider TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '', window_index INTEGER NOT NULL,
            start_seconds INTEGER NOT NULL, end_seconds INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', attempt_count INTEGER NOT NULL DEFAULT 0,
            result_json TEXT, result_checksum TEXT, error_message TEXT, next_retry_at TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT,
            UNIQUE(
                task_id, transcript_fingerprint, provider, model,
                window_index, start_seconds, end_seconds
            ),
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        );
        """
    )


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
    comedy_v2_prompt = ""
    if COMEDY_V2_AI_PROMPT_PATH.exists():
        comedy_v2_prompt = COMEDY_V2_AI_PROMPT_PATH.read_text(encoding="utf-8")

    presets = [
        (DEFAULT_AI_PROMPT_PRESET_ID, 1, "默认直播切片分析专家", default_prompt, 1),
        ("preset_002", 2, "综艺访谈完整上下文专家", variety_prompt, 0),
        ("preset_003", 3, "3号方案", "", 0),
        ("preset_004", 4, "康熙笑点优先 V2", comedy_v2_prompt, 0),
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
