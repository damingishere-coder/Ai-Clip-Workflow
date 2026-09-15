"""Batch-only human review fence. Epoch is evidence generation, not queue status."""
import hashlib
import sqlite3

VERSION = "20260915_09_production_review"
NAME = "批次人工成片审核门槛"
STATEMENTS = [
    """CREATE TABLE production_review_epochs (
        task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
        revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0)
    )""",
    """CREATE TABLE production_reviews (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        cut_run_id TEXT NOT NULL REFERENCES cut_runs(id), revision INTEGER NOT NULL,
        request_key TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
        manifest_json TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
        delivery_mode TEXT NOT NULL CHECK(delivery_mode IN ('original','subtitled')),
        source TEXT NOT NULL CHECK(source='human_confirmation'), created_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_production_reviews_task ON production_reviews(task_id,revision)",
    "CREATE TRIGGER immutable_production_review BEFORE UPDATE ON production_reviews BEGIN SELECT RAISE(ABORT,'Production review is immutable'); END",
]


def batch(task):
    return f"EXISTS(SELECT 1 FROM material_batch_items WHERE task_id={task})"


def publishing(task):
    return f"EXISTS(SELECT 1 FROM publish_jobs WHERE task_id={task} AND status='PUBLISHING')"


def bump_trigger(table, event, task, condition="1", *, columns=None):
    suffix = event.lower()
    update = "UPDATE OF " + ",".join(columns) if columns else event
    statement = f"""CREATE TRIGGER review_epoch_{table}_{suffix} BEFORE {update} ON {table}
        WHEN {batch(task)} AND ({condition}) BEGIN
        SELECT CASE WHEN {publishing(task)} THEN RAISE(ABORT,'review_boundary:任务正在发布，不能更改已确认成片或排入新处理') END;
        INSERT INTO production_review_epochs(task_id,revision) VALUES({task},1)
            ON CONFLICT(task_id) DO UPDATE SET revision=revision+1;
    END"""
    STATEMENTS.append(statement)


for table, columns in (
    ("clip_candidates", ("enabled","is_deleted","start_time","end_time","title","clip_key","source_analysis_run_id")),
    ("ai_analysis_runs", ("is_active","analysis_payload_json","provider","model","prompt_version_id","prompt_text_sha256","content_profile_version_id","content_profile_sha256","requested_clip_count")),
    ("cut_runs", ("is_active","status")),
    ("output_clip", ("is_active","status","cut_run_id","clip_candidate_id","output_file_path","output_file_name","source_start_ms","source_end_ms","source_duration_ms","source_fingerprint","snapshot_source")),
):
    bump_trigger(table, "INSERT", "NEW.task_id")
    bump_trigger(table, "UPDATE", "NEW.task_id", " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in columns), columns=columns)
    bump_trigger(table, "DELETE", "OLD.task_id")
bump_trigger("tasks", "UPDATE", "NEW.id", "OLD.original_video_path IS NOT NEW.original_video_path OR OLD.is_deleted IS NOT NEW.is_deleted", columns=("original_video_path","is_deleted"))
heavy = "NEW.job_type IN ('material_import','transcript','ai_analysis','video_cut','auto_pipeline')"
bump_trigger("workflow_jobs", "INSERT", "NEW.task_id", heavy)
bump_trigger("workflow_jobs", "UPDATE", "NEW.task_id", heavy+" AND NEW.status='queued' AND OLD.status!='queued'", columns=("status",))

# Even subtitle jobs cannot be newly queued during an external upload.
for event in ("INSERT", "UPDATE"):
    changed = "1" if event == "INSERT" else "NEW.status IN ('queued','running') AND OLD.status IS NOT NEW.status"
    STATEMENTS.append(f"""CREATE TRIGGER review_protect_job_{event.lower()} BEFORE {event} ON workflow_jobs
        WHEN {batch('NEW.task_id')} AND {publishing('NEW.task_id')} AND ({changed}) BEGIN
        SELECT RAISE(ABORT,'review_boundary:任务正在发布，不能启动新的处理'); END""")
for event in ("INSERT", "UPDATE", "DELETE"):
    track = "OLD.track_id" if event == "DELETE" else "NEW.track_id"
    STATEMENTS.append(f"""CREATE TRIGGER review_protect_revision_{event.lower()} BEFORE {event} ON subtitle_revisions
        WHEN EXISTS(SELECT 1 FROM subtitle_tracks st JOIN material_batch_items b ON b.task_id=st.task_id
            JOIN publish_jobs p ON p.task_id=st.task_id AND p.status='PUBLISHING' WHERE st.id={track}) BEGIN
        SELECT RAISE(ABORT,'review_boundary:任务正在发布，不能更改字幕版本'); END""")

# Subtitle changes do not invalidate cut consent, but cannot mutate an in-flight source.
for table in ("subtitle_tracks", "subtitle_jobs"):
    for event in ("INSERT", "UPDATE", "DELETE"):
        task = "OLD.task_id" if event == "DELETE" else "NEW.task_id"
        STATEMENTS.append(f"""CREATE TRIGGER review_protect_{table}_{event.lower()} BEFORE {event} ON {table}
            WHEN {batch(task)} AND {publishing(task)} BEGIN
            SELECT RAISE(ABORT,'review_boundary:任务正在发布，不能更改字幕来源'); END""")
STATEMENTS.append(f"""CREATE TRIGGER review_confirm_idle BEFORE INSERT ON production_reviews
    WHEN {publishing('NEW.task_id')} OR EXISTS(SELECT 1 FROM workflow_jobs WHERE task_id=NEW.task_id AND status IN ('queued','running'))
    BEGIN SELECT RAISE(ABORT,'review_boundary:任务仍在执行或发布，请等待停止后确认'); END""")


def eligible_sql(task, output, mode, path):
    # Same relation is used by the DB fence and Python readiness diagnostics.
    return f"""EXISTS(SELECT 1 FROM production_reviews r
        JOIN production_review_epochs e ON e.task_id=r.task_id AND e.revision=r.revision
        JOIN tasks t ON t.id=r.task_id AND COALESCE(t.is_deleted,0)=0
        JOIN cut_runs cr ON cr.id=r.cut_run_id AND cr.task_id=t.id AND cr.is_active=1 AND cr.status='completed'
        JOIN output_clip oc ON oc.task_id=t.id AND oc.cut_run_id=cr.id AND oc.id={output} AND oc.is_active=1 AND oc.status='completed'
        WHERE r.task_id={task} AND r.rowid=(SELECT MAX(latest.rowid) FROM production_reviews latest WHERE latest.task_id=r.task_id)
        AND r.delivery_mode={mode}
        AND EXISTS(SELECT 1 FROM json_each(r.manifest_json,'$.outputs') o WHERE json_extract(o.value,'$.id')=oc.id)
        AND NOT EXISTS(SELECT 1 FROM workflow_jobs w WHERE w.task_id=r.task_id AND w.status IN ('queued','running'))
        AND ((r.delivery_mode='original' AND {path}=oc.output_file_path)
          OR (r.delivery_mode='subtitled' AND EXISTS(SELECT 1 FROM subtitle_jobs sj
            JOIN subtitle_tracks st ON st.task_id=r.task_id AND st.output_clip_id=oc.id AND st.is_active=1 AND st.active_revision_id=sj.revision_id
            JOIN subtitle_revisions sr ON sr.id=sj.revision_id AND sr.track_id=st.id AND sr.status='approved'
            WHERE sj.task_id=r.task_id AND sj.output_clip_id=oc.id AND sj.is_active=1
              AND sj.status='completed' AND sj.validation_status='verified' AND sj.output_file_path={path}))))"""


for event in ("INSERT", "UPDATE"):
    active = "1" if event == "INSERT" else "NEW.status IN ('DRAFT','WAITING','SCHEDULED','PUBLISHING')"
    eligible = eligible_sql("NEW.task_id", "NEW.output_clip_id", "NEW.video_source", "NEW.video_file_path")
    STATEMENTS.append(f"""CREATE TRIGGER review_publish_{event.lower()} BEFORE {event} ON publish_jobs
        WHEN {batch('NEW.task_id')} AND ({active}) AND NOT ({eligible}) BEGIN
        SELECT RAISE(ABORT,'review_boundary:成片或字幕尚未人工确认，或确认版本已失效'); END""")
STATEMENTS = tuple(STATEMENTS)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True) as c:
        return not c.execute("SELECT 1 FROM sqlite_master WHERE name='production_reviews'").fetchone()


def apply(connection):
    for statement in STATEMENTS:
        connection.execute(statement)


def verify(connection):
    for statement in STATEMENTS:
        name = statement.split()[2]
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if not row or row[0] != statement:
            raise ValueError("人工成片审核约束不一致")
