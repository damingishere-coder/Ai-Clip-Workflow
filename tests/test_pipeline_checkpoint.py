"""P1.3b auto-pipeline checkpoint and restart reconciliation tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.db.database import get_connection
from app.models.task import TaskCreate, TaskStatus
from app.services import job_service, task_service
from app.services.ai import unit_checkpoint
from app.services.pipeline_checkpoint_service import (
    AUTO_PIPELINE_CHECKPOINT_KIND,
    AutoPipelineCheckpoint,
    PipelineCheckpointError,
)
from app.services.pipeline_engine import PipelineEngine, STEP_STATUSES, start_auto_pipeline
from app.services.storage_service import get_artifact_paths
from app.services.task_lifecycle_service import create_task_record


@pytest.fixture(autouse=True)
def cleanup_pipeline_checkpoint_data():
    """Keep this module's records and artifacts from leaking into later tests."""

    yield
    pattern = "test-pipeline-checkpoint-%"
    with get_connection() as connection:
        task_ids = [
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM tasks WHERE id LIKE ?",
                (pattern,),
            ).fetchall()
        ]
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM subtitle_jobs WHERE task_id LIKE ?", (pattern,))
        connection.execute(
            """
            DELETE FROM subtitle_cues WHERE revision_id IN (
                SELECT revision.id FROM subtitle_revisions AS revision
                JOIN subtitle_tracks AS track ON track.id = revision.track_id
                WHERE track.task_id LIKE ?
            )
            """,
            (pattern,),
        )
        connection.execute(
            """
            DELETE FROM subtitle_revisions WHERE track_id IN (
                SELECT id FROM subtitle_tracks WHERE task_id LIKE ?
            )
            """,
            (pattern,),
        )
        connection.execute("DELETE FROM subtitle_tracks WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM clip_feedback WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM cut_runs WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM ai_analysis_windows WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM ai_analysis_runs WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM transcription_chunks WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM transcription_runs WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM workflow_jobs WHERE task_id LIKE ?", (pattern,))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (pattern,))
        connection.commit()
    for task_id in task_ids:
        shutil.rmtree(get_artifact_paths(task_id)["task_dir"], ignore_errors=True)


def _create_auto_task(task_id: str, *, delivery_mode: str = "original") -> dict:
    create_task_record(
        TaskCreate(
            task_name=task_id,
            source_type="upload",
            platform="general",
            selection_profile="general",
            auto_mode=True,
        ),
        task_id=task_id,
        task_dir_name=task_id,
    )
    with get_connection() as connection:
        connection.execute(
            "UPDATE tasks SET auto_config_json = ? WHERE id = ?",
            (json.dumps({"subtitle_delivery_mode": delivery_mode}), task_id),
        )
        connection.commit()
    return task_service.get_task(task_id, include_video_probe=False)


def _claim_auto_job(task_id: str, start_step: TaskStatus, owner: str) -> dict:
    job = job_service.create_job(
        task_id=task_id,
        job_type=job_service.JOB_TYPE_AUTO_PIPELINE,
        payload={"retry": False, "start_step": start_step.value},
    )
    claimed = job_service.claim_job(job["id"], owner)
    assert claimed is not None
    return claimed


def _checkpoint(
    engine: PipelineEngine,
    task: dict,
    job: dict,
    start_step: TaskStatus,
) -> AutoPipelineCheckpoint:
    return AutoPipelineCheckpoint.load(
        job_id=job["id"],
        task_id=task["id"],
        start_step=start_step.value,
        run_key=engine._pipeline_run_key(task, engine._load_auto_config(task), start_step),
        ordered_steps=[step.value for step in STEP_STATUSES],
    )


def _insert_candidate(task_id: str, candidate_id: str, *, clip_key: str | None = None) -> None:
    now = task_service._now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO clip_candidates (
                id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'clip', '00:00:00', '00:00:30', 30, ?, ?)
            """,
            (candidate_id, task_id, clip_key or candidate_id, now, now),
        )
        connection.commit()


def _insert_output_clip(
    task_id: str,
    output_id: str,
    path: Path,
    *,
    cut_run_id: str | None = None,
    clip_candidate_id: str | None = None,
) -> None:
    now = task_service._now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, clip_candidate_id, output_file_path, output_file_name, status,
                cut_run_id, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'completed', ?, 1, ?, ?)
            """,
            (
                output_id,
                task_id,
                clip_candidate_id,
                str(path),
                path.name,
                cut_run_id,
                now,
                now,
            ),
        )
        connection.commit()


def test_auto_pipeline_checkpoint_is_versioned_and_fenced() -> None:
    task = _create_auto_task("test-pipeline-checkpoint-envelope")
    job = _claim_auto_job(task["id"], TaskStatus.PREPARING_SOURCE, "checkpoint-owner")
    with job_service.job_lease_context(job["id"], "checkpoint-owner", job["lease_token"]):
        checkpoint = _checkpoint(PipelineEngine(), task, job, TaskStatus.PREPARING_SOURCE)
        checkpoint.begin_step(TaskStatus.PREPARING_SOURCE.value, baseline={"before": "empty"})
        checkpoint.complete_step(
            TaskStatus.PREPARING_SOURCE.value,
            outputs={"source_reference": {"sha256": "evidence"}},
        )

    stored = job_service.get_job(job["id"])["checkpoint_json"]
    assert stored["kind"] == AUTO_PIPELINE_CHECKPOINT_KIND
    assert stored["completed_steps"] == [TaskStatus.PREPARING_SOURCE.value]
    assert stored["steps"][TaskStatus.PREPARING_SOURCE.value]["state"] == "succeeded"
    assert "config" not in stored

    with job_service.job_lease_context(job["id"], "old-owner", "old-token"):
        with pytest.raises(job_service.JobLeaseLostError):
            checkpoint.begin_step(TaskStatus.TRANSCRIBING.value)


def test_auto_pipeline_checkpoint_preserves_ai_unit_recovery_evidence() -> None:
    task = _create_auto_task("test-pipeline-checkpoint-ai-units")
    job = _claim_auto_job(task["id"], TaskStatus.AI_ANALYZING, "ai-unit-owner")
    with job_service.job_lease_context(job["id"], "ai-unit-owner", job["lease_token"]):
        checkpoint = _checkpoint(PipelineEngine(), task, job, TaskStatus.AI_ANALYZING)
        checkpoint.begin_step(TaskStatus.AI_ANALYZING.value, baseline={})
        unit_checkpoint.execute_checkpointed_ai_unit(
            task_id=task["id"],
            namespace="general_chunks",
            input_fingerprint="stable-input",
            unit_id="chunk_001",
            operation=lambda: {"clips": [{"clip_id": "confirmed"}]},
        )
        checkpoint.fail_step(TaskStatus.AI_ANALYZING.value, "模拟最终提交前中断")

    stored = job_service.get_job(job["id"])["checkpoint_json"]
    assert stored["kind"] == AUTO_PIPELINE_CHECKPOINT_KIND
    assert stored["steps"][TaskStatus.AI_ANALYZING.value]["state"] == "failed"
    unit = stored["_ai_analysis_units_v1"]["namespaces"]["general_chunks"]["units"]["chunk_001"]
    assert unit["status"] == "completed"


@pytest.mark.parametrize(
    "raw_checkpoint",
    ["{broken", {"kind": "future_auto_pipeline_v9"}],
)
def test_malformed_or_unknown_checkpoint_fails_closed(raw_checkpoint) -> None:
    task = _create_auto_task(f"test-pipeline-checkpoint-bad-{type(raw_checkpoint).__name__}")
    job = _claim_auto_job(task["id"], TaskStatus.PUBLISH_JOB_CREATING, "bad-checkpoint-owner")
    with get_connection() as connection:
        value = raw_checkpoint if isinstance(raw_checkpoint, str) else json.dumps(raw_checkpoint)
        connection.execute(
            "UPDATE workflow_jobs SET checkpoint_json = ? WHERE id = ?",
            (value, job["id"]),
        )
        connection.commit()
    engine = PipelineEngine()
    engine._create_publish_jobs = Mock(side_effect=AssertionError("未知 checkpoint 不得执行 handler"))

    with job_service.job_lease_context(job["id"], "bad-checkpoint-owner", job["lease_token"]):
        with pytest.raises(PipelineCheckpointError):
            engine.run(
                task["id"],
                start_step=TaskStatus.PUBLISH_JOB_CREATING,
                job_id=job["id"],
            )

    engine._create_publish_jobs.assert_not_called()
    assert task_service.get_task(task["id"], include_video_probe=False)["status"] == TaskStatus.CREATED.value


def test_completed_metadata_restores_context_without_regeneration(tmp_path: Path) -> None:
    task = _create_auto_task("test-pipeline-checkpoint-metadata")
    paths = get_artifact_paths(task["id"])
    clip_path = paths["clips_dir"] / "checkpoint-metadata.mp4"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    clip_path.write_bytes(b"video")
    _insert_output_clip(task["id"], "checkpoint-metadata-output", clip_path)
    output_clip = task_service.list_output_clips(task["id"])[0]
    cover_path = paths["covers_dir"] / "checkpoint-cover.jpg"
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    cover_path.write_bytes(b"cover")
    metadata_items = [
        {
            "output_clip": output_clip,
            "metadata": {
                "platform": "douyin",
                "title": "checkpoint title",
                "caption": "checkpoint caption",
                "hashtags": ["checkpoint"],
                "risk_flags": [],
            },
            "cover": {"cover_file_path": str(cover_path), "cover_time_seconds": 1},
        }
    ]
    metadata_path = paths["analysis_path"].parent / "auto_publish_metadata.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata_items, ensure_ascii=False), encoding="utf-8")

    engine = PipelineEngine()
    job = _claim_auto_job(task["id"], TaskStatus.METADATA_GENERATING, "metadata-owner")
    captured_schedule: list[dict] = []
    with job_service.job_lease_context(job["id"], "metadata-owner", job["lease_token"]):
        checkpoint = _checkpoint(engine, task, job, TaskStatus.METADATA_GENERATING)
        checkpoint.begin_step(TaskStatus.METADATA_GENERATING.value)
        metadata_result = {
            "metadata_path": str(metadata_path),
            "metadata_count": 1,
            "need_review_count": 0,
            "metadata_items": metadata_items,
        }
        checkpoint.complete_step(
            TaskStatus.METADATA_GENERATING.value,
            outputs=engine._checkpoint_outputs(
                task["id"], TaskStatus.METADATA_GENERATING, metadata_result
            ),
        )
        engine._generate_metadata = Mock(side_effect=AssertionError("metadata 不应重复生成"))
        original_schedule = engine._create_schedule

        def capture_schedule(task_id: str, context: dict) -> dict:
            captured_schedule.extend(context[TaskStatus.METADATA_GENERATING.value]["metadata_items"])
            return original_schedule(task_id, context)

        engine._create_schedule = capture_schedule
        engine._write_task_summary = Mock(return_value={"summary_path": str(tmp_path / "summary.json")})
        result = engine.run(
            task["id"],
            start_step=TaskStatus.METADATA_GENERATING,
            job_id=job["id"],
        )

    assert result["status"] == "ready_to_publish"
    engine._generate_metadata.assert_not_called()
    assert captured_schedule[0]["metadata"]["title"] == "checkpoint title"
    stored = job_service.get_job(job["id"])["checkpoint_json"]
    metadata_outputs = stored["steps"][TaskStatus.METADATA_GENERATING.value]["outputs"]
    assert "metadata_items" not in metadata_outputs
    assert stored["completed_steps"] == [
        TaskStatus.METADATA_GENERATING.value,
        TaskStatus.SCHEDULE_CREATING.value,
        TaskStatus.PUBLISH_JOB_CREATING.value,
    ]
    publish_outputs = stored["steps"][TaskStatus.PUBLISH_JOB_CREATING.value]["outputs"]
    with job_service.job_lease_context(job["id"], "metadata-owner", job["lease_token"]):
        recovered_publish = engine._reconcile_interrupted_step(
            task["id"],
            TaskStatus.PUBLISH_JOB_CREATING,
            {"baseline": {"schedule": publish_outputs["schedule_input"]}},
        )
    assert recovered_publish is not None
    assert recovered_publish["created_count"] == 1
    assert recovered_publish["created"][0]["id"] == publish_outputs["created_ids"][0]
    expected_job = publish_outputs["job_evidence"][0]
    with get_connection() as connection:
        connection.execute(
            "UPDATE output_clip SET is_active = 0 WHERE id = ?",
            (expected_job["output_clip_id"],),
        )
        connection.commit()
    with pytest.raises(PipelineCheckpointError, match="切片已失活"):
        engine._restore_checkpoint_step(
            task["id"],
            TaskStatus.PUBLISH_JOB_CREATING,
            publish_outputs,
        )
    with get_connection() as connection:
        connection.execute(
            "UPDATE output_clip SET is_active = 1 WHERE id = ?",
            (expected_job["output_clip_id"],),
        )
        connection.commit()
    with get_connection() as connection:
        connection.execute(
            "UPDATE publish_jobs SET platform = 'bilibili' WHERE id = ?",
            (publish_outputs["created_ids"][0],),
        )
        connection.commit()
    with pytest.raises(PipelineCheckpointError, match="草稿字段不一致"):
        engine._restore_checkpoint_step(
            task["id"],
            TaskStatus.PUBLISH_JOB_CREATING,
            publish_outputs,
        )
    with get_connection() as connection:
        connection.execute(
            "UPDATE publish_jobs SET platform = ?, title = 'tampered title' WHERE id = ?",
            (expected_job["platform"], publish_outputs["created_ids"][0]),
        )
        connection.commit()
    with pytest.raises(PipelineCheckpointError, match="草稿字段不一致"):
        engine._restore_checkpoint_step(
            task["id"],
            TaskStatus.PUBLISH_JOB_CREATING,
            publish_outputs,
        )


def test_interrupted_ai_step_reconciles_new_analysis_run_without_provider_call() -> None:
    task = _create_auto_task("test-pipeline-checkpoint-ai")
    engine = PipelineEngine()
    job = _claim_auto_job(task["id"], TaskStatus.AI_ANALYZING, "ai-checkpoint-owner")
    paths = get_artifact_paths(task["id"])
    paths["transcript_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["transcript_path"].write_text(
        "00:00:00 --> 00:00:30\ncheckpoint transcript",
        encoding="utf-8",
    )
    analysis_payload = {
        "analysis_summary": "recovered",
        "analysis_meta": {"provider": "test", "generated_at": task_service._now_iso()},
        "clips": [{"clip_id": "clip-1", "title": "clip"}],
    }
    with job_service.job_lease_context(job["id"], "ai-checkpoint-owner", job["lease_token"]):
        checkpoint = _checkpoint(engine, task, job, TaskStatus.AI_ANALYZING)
        checkpoint.begin_step(
            TaskStatus.AI_ANALYZING.value,
            baseline=engine._checkpoint_baseline(task["id"], TaskStatus.AI_ANALYZING),
        )
        paths["analysis_path"].parent.mkdir(parents=True, exist_ok=True)
        paths["analysis_path"].write_text(json.dumps(analysis_payload), encoding="utf-8")
        now = task_service._now_iso()
        _insert_candidate(task["id"], "checkpoint-ai-clip", clip_key="clip-1")
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO ai_analysis_runs (
                    id, task_id, run_number, provider, provider_label, model,
                    requested_clip_count, clip_count, analysis_payload_json,
                    is_active, created_at
                ) VALUES ('checkpoint-ai-run', ?, 1, 'test', 'test', 'test', 1, 1, ?, 1, ?)
                """,
                (task["id"], json.dumps(analysis_payload), now),
            )
            connection.commit()
        engine._run_ai_analysis = Mock(side_effect=AssertionError("AI Provider 不应重复调用"))
        engine._select_clips = Mock(side_effect=RuntimeError("stop after AI recovery"))
        result = engine.run(
            task["id"],
            start_step=TaskStatus.AI_ANALYZING,
            job_id=job["id"],
        )

    assert result["failed_step"] == TaskStatus.CLIP_SELECTING.value
    engine._run_ai_analysis.assert_not_called()
    stored = job_service.get_job(job["id"])["checkpoint_json"]
    assert stored["completed_steps"] == [TaskStatus.AI_ANALYZING.value]
    assert stored["steps"][TaskStatus.AI_ANALYZING.value]["recovered"] is True


def test_auto_retry_reuses_failed_job_checkpoint_without_provider_call() -> None:
    task = _create_auto_task("test-pipeline-checkpoint-ai-retry")
    engine = PipelineEngine()
    first_job = _claim_auto_job(task["id"], TaskStatus.AI_ANALYZING, "retry-old-owner")
    paths = get_artifact_paths(task["id"])
    paths["transcript_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["transcript_path"].write_text("retry transcript", encoding="utf-8")
    analysis_payload = {
        "analysis_summary": "recover after failed job",
        "analysis_meta": {"provider": "test", "generated_at": task_service._now_iso()},
        "clips": [{"clip_id": "retry-clip", "title": "retry"}],
    }
    with job_service.job_lease_context(
        first_job["id"],
        "retry-old-owner",
        first_job["lease_token"],
    ):
        checkpoint = _checkpoint(engine, task, first_job, TaskStatus.AI_ANALYZING)
        checkpoint.begin_step(
            TaskStatus.AI_ANALYZING.value,
            baseline=engine._checkpoint_baseline(task["id"], TaskStatus.AI_ANALYZING),
        )
        paths["analysis_path"].write_text(json.dumps(analysis_payload), encoding="utf-8")
        _insert_candidate(task["id"], "checkpoint-ai-retry-clip", clip_key="retry-clip")
        now = task_service._now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO ai_analysis_runs (
                    id, task_id, run_number, provider, provider_label, model,
                    requested_clip_count, clip_count, analysis_payload_json,
                    is_active, created_at
                ) VALUES ('checkpoint-ai-retry-run', ?, 1, 'test', 'test', 'test', 1, 1, ?, 1, ?)
                """,
                (task["id"], json.dumps(analysis_payload), now),
            )
            connection.commit()
        job_service.mark_job_failed(first_job["id"], "worker exited after provider result")

    queued = start_auto_pipeline(task["id"], background_tasks=object(), retry=True)
    assert queued["job_id"] == first_job["id"]
    assert queued["status"] == job_service.JOB_STATUS_QUEUED
    assert job_service.get_job(first_job["id"])["checkpoint_json"]["current_step"] == (
        TaskStatus.AI_ANALYZING.value
    )

    reclaimed = job_service.claim_job(first_job["id"], "retry-new-owner")
    assert reclaimed is not None
    engine._run_ai_analysis = Mock(side_effect=AssertionError("AI Provider 不应重复调用"))
    engine._select_clips = Mock(side_effect=RuntimeError("stop after retry recovery"))
    with job_service.job_lease_context(
        reclaimed["id"],
        "retry-new-owner",
        reclaimed["lease_token"],
    ):
        result = engine.run(
            task["id"],
            start_step=TaskStatus.AI_ANALYZING,
            job_id=reclaimed["id"],
        )

    assert result["failed_step"] == TaskStatus.CLIP_SELECTING.value
    engine._run_ai_analysis.assert_not_called()
    assert job_service.get_job(reclaimed["id"])["checkpoint_json"]["completed_steps"] == [
        TaskStatus.AI_ANALYZING.value
    ]


def test_job_id_without_active_lease_fails_before_handler() -> None:
    task = _create_auto_task("test-pipeline-checkpoint-no-lease")
    job = job_service.create_job(
        task_id=task["id"],
        job_type=job_service.JOB_TYPE_AUTO_PIPELINE,
        payload={"start_step": TaskStatus.AI_ANALYZING.value},
    )
    engine = PipelineEngine()
    engine._run_ai_analysis = Mock(side_effect=AssertionError("无租约不得执行 handler"))

    with pytest.raises(job_service.JobLeaseLostError):
        engine.run(
            task["id"],
            start_step=TaskStatus.AI_ANALYZING,
            job_id=job["id"],
        )

    engine._run_ai_analysis.assert_not_called()
    assert task_service.get_task(task["id"], include_video_probe=False)["status"] == (
        TaskStatus.CREATED.value
    )


def test_interrupted_transcript_step_reconciles_final_markdown() -> None:
    task = _create_auto_task("test-pipeline-checkpoint-transcript")
    engine = PipelineEngine()
    job = _claim_auto_job(task["id"], TaskStatus.TRANSCRIBING, "transcript-owner")
    paths = get_artifact_paths(task["id"])

    with job_service.job_lease_context(job["id"], "transcript-owner", job["lease_token"]):
        checkpoint = _checkpoint(engine, task, job, TaskStatus.TRANSCRIBING)
        checkpoint.begin_step(
            TaskStatus.TRANSCRIBING.value,
            baseline=engine._checkpoint_baseline(task["id"], TaskStatus.TRANSCRIBING),
        )
        paths["transcript_path"].parent.mkdir(parents=True, exist_ok=True)
        paths["transcript_path"].write_text(
            "00:00:00 --> 00:00:30\ncompleted transcript",
            encoding="utf-8",
        )
        engine._transcribe_or_read_text = Mock(
            side_effect=AssertionError("最终 Markdown 已存在时不应重复转写")
        )
        engine._run_ai_analysis = Mock(side_effect=RuntimeError("stop after transcript recovery"))
        result = engine.run(
            task["id"],
            start_step=TaskStatus.TRANSCRIBING,
            job_id=job["id"],
        )

    assert result["failed_step"] == TaskStatus.AI_ANALYZING.value
    engine._transcribe_or_read_text.assert_not_called()
    stored = job_service.get_job(job["id"])["checkpoint_json"]
    assert stored["completed_steps"] == [TaskStatus.TRANSCRIBING.value]
    assert stored["steps"][TaskStatus.TRANSCRIBING.value]["recovered"] is True


def test_interrupted_ai_step_reruns_when_transcript_changed() -> None:
    task = _create_auto_task("test-pipeline-checkpoint-ai-input-changed")
    engine = PipelineEngine()
    job = _claim_auto_job(task["id"], TaskStatus.AI_ANALYZING, "ai-input-owner")
    paths = get_artifact_paths(task["id"])
    paths["transcript_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["transcript_path"].write_text("original transcript", encoding="utf-8")
    analysis_payload = {
        "analysis_summary": "stale result",
        "analysis_meta": {"provider": "test", "generated_at": task_service._now_iso()},
        "clips": [{"clip_id": "clip-stale", "title": "stale"}],
    }

    with job_service.job_lease_context(job["id"], "ai-input-owner", job["lease_token"]):
        checkpoint = _checkpoint(engine, task, job, TaskStatus.AI_ANALYZING)
        checkpoint.begin_step(
            TaskStatus.AI_ANALYZING.value,
            baseline=engine._checkpoint_baseline(task["id"], TaskStatus.AI_ANALYZING),
        )
        paths["transcript_path"].write_text("changed transcript", encoding="utf-8")
        paths["analysis_path"].write_text(json.dumps(analysis_payload), encoding="utf-8")
        _insert_candidate(task["id"], "checkpoint-ai-stale-clip", clip_key="clip-stale")
        now = task_service._now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO ai_analysis_runs (
                    id, task_id, run_number, provider, provider_label, model,
                    requested_clip_count, clip_count, analysis_payload_json,
                    is_active, created_at
                ) VALUES ('checkpoint-ai-stale-run', ?, 1, 'test', 'test', 'test', 1, 1, ?, 1, ?)
                """,
                (task["id"], json.dumps(analysis_payload), now),
            )
            connection.commit()
        engine._run_ai_analysis = Mock(side_effect=RuntimeError("provider rerun required"))
        result = engine.run(
            task["id"],
            start_step=TaskStatus.AI_ANALYZING,
            job_id=job["id"],
        )

    assert result["failed_step"] == TaskStatus.AI_ANALYZING.value
    assert result["last_error"] == "provider rerun required"
    engine._run_ai_analysis.assert_called_once()
    stored = job_service.get_job(job["id"])["checkpoint_json"]
    assert stored["completed_steps"] == []
    assert stored["steps"][TaskStatus.AI_ANALYZING.value]["state"] == "failed"


def test_interrupted_cut_step_reconciles_active_run_without_ffmpeg(tmp_path: Path) -> None:
    task = _create_auto_task("test-pipeline-checkpoint-cut")
    engine = PipelineEngine()
    job = _claim_auto_job(task["id"], TaskStatus.VIDEO_CUTTING, "cut-checkpoint-owner")
    paths = get_artifact_paths(task["id"])
    output_path = paths["clips_dir"] / "run_0001_checkpoint-cut-run" / "clip.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"video")
    _insert_candidate(task["id"], "checkpoint-cut-candidate")
    with job_service.job_lease_context(job["id"], "cut-checkpoint-owner", job["lease_token"]):
        checkpoint = _checkpoint(engine, task, job, TaskStatus.VIDEO_CUTTING)
        checkpoint.begin_step(
            TaskStatus.VIDEO_CUTTING.value,
            baseline=engine._checkpoint_baseline(task["id"], TaskStatus.VIDEO_CUTTING),
        )
        now = task_service._now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO cut_runs (
                    id, task_id, run_number, status, is_active, created_at, updated_at
                ) VALUES ('checkpoint-cut-run', ?, 1, 'completed', 1, ?, ?)
                """,
                (task["id"], now, now),
            )
            connection.commit()
        _insert_output_clip(
            task["id"],
            "checkpoint-cut-output",
            output_path,
            cut_run_id="checkpoint-cut-run",
            clip_candidate_id="checkpoint-cut-candidate",
        )
        engine._cut_video = Mock(side_effect=AssertionError("FFmpeg 不应重复执行"))
        engine._prepare_subtitle_drafts = Mock(side_effect=RuntimeError("stop after cut recovery"))
        result = engine.run(
            task["id"],
            start_step=TaskStatus.VIDEO_CUTTING,
            job_id=job["id"],
        )

    assert result["failed_step"] == TaskStatus.SUBTITLE_DRAFTING.value
    engine._cut_video.assert_not_called()
    stored = job_service.get_job(job["id"])["checkpoint_json"]
    assert stored["completed_steps"] == [TaskStatus.VIDEO_CUTTING.value]
    assert stored["steps"][TaskStatus.VIDEO_CUTTING.value]["recovered"] is True


def test_cut_checkpoint_rejects_empty_file_and_wrong_run() -> None:
    task = _create_auto_task("test-pipeline-checkpoint-cut-evidence")
    engine = PipelineEngine()
    paths = get_artifact_paths(task["id"])
    output_path = paths["clips_dir"] / "run_0001_checkpoint-cut-evidence" / "clip.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"valid-video-evidence")
    _insert_candidate(task["id"], "checkpoint-cut-evidence-candidate")
    now = task_service._now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO cut_runs (
                id, task_id, run_number, status, is_active, created_at, updated_at
            ) VALUES ('checkpoint-cut-evidence-run', ?, 1, 'completed', 1, ?, ?)
            """,
            (task["id"], now, now),
        )
        connection.commit()
    _insert_output_clip(
        task["id"],
        "checkpoint-cut-evidence-output",
        output_path,
        cut_run_id="checkpoint-cut-evidence-run",
        clip_candidate_id="checkpoint-cut-evidence-candidate",
    )
    outputs = engine._checkpoint_outputs(
        task["id"],
        TaskStatus.VIDEO_CUTTING,
        {"cut_run_id": "checkpoint-cut-evidence-run", "cut_run_number": 1},
    )

    output_path.write_bytes(b"")
    with pytest.raises(PipelineCheckpointError, match="文件为空"):
        engine._restore_checkpoint_step(task["id"], TaskStatus.VIDEO_CUTTING, outputs)

    output_path.write_bytes(b"valid-video-evidence")
    with get_connection() as connection:
        connection.execute(
            "UPDATE output_clip SET cut_run_id = 'another-run' WHERE id = ?",
            ("checkpoint-cut-evidence-output",),
        )
        connection.commit()
    with pytest.raises(PipelineCheckpointError, match="不属于当前 cut run"):
        engine._restore_checkpoint_step(task["id"], TaskStatus.VIDEO_CUTTING, outputs)


def test_interrupted_cut_step_reruns_when_selection_changed() -> None:
    task = _create_auto_task("test-pipeline-checkpoint-cut-input-changed")
    engine = PipelineEngine()
    job = _claim_auto_job(task["id"], TaskStatus.VIDEO_CUTTING, "cut-input-owner")
    paths = get_artifact_paths(task["id"])
    output_path = paths["clips_dir"] / "run_0001_checkpoint-cut-stale" / "clip.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"video")
    _insert_candidate(task["id"], "checkpoint-cut-old-candidate")

    with job_service.job_lease_context(job["id"], "cut-input-owner", job["lease_token"]):
        checkpoint = _checkpoint(engine, task, job, TaskStatus.VIDEO_CUTTING)
        checkpoint.begin_step(
            TaskStatus.VIDEO_CUTTING.value,
            baseline=engine._checkpoint_baseline(task["id"], TaskStatus.VIDEO_CUTTING),
        )
        now = task_service._now_iso()
        with get_connection() as connection:
            connection.execute(
                "UPDATE clip_candidates SET enabled = 0, updated_at = ? WHERE task_id = ?",
                (now, task["id"]),
            )
            connection.commit()
        _insert_candidate(task["id"], "checkpoint-cut-new-candidate")
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO cut_runs (
                    id, task_id, run_number, status, is_active, created_at, updated_at
                ) VALUES ('checkpoint-cut-stale', ?, 1, 'completed', 1, ?, ?)
                """,
                (task["id"], now, now),
            )
            connection.commit()
        _insert_output_clip(
            task["id"],
            "checkpoint-cut-stale-output",
            output_path,
            cut_run_id="checkpoint-cut-stale",
            clip_candidate_id="checkpoint-cut-new-candidate",
        )
        engine._cut_video = Mock(side_effect=RuntimeError("ffmpeg rerun required"))
        result = engine.run(
            task["id"],
            start_step=TaskStatus.VIDEO_CUTTING,
            job_id=job["id"],
        )

    assert result["failed_step"] == TaskStatus.VIDEO_CUTTING.value
    assert result["last_error"] == "ffmpeg rerun required"
    engine._cut_video.assert_called_once()
    stored = job_service.get_job(job["id"])["checkpoint_json"]
    assert stored["completed_steps"] == []
    assert stored["steps"][TaskStatus.VIDEO_CUTTING.value]["state"] == "failed"


def test_completed_subtitle_checkpoint_keeps_manual_review_gate(tmp_path: Path) -> None:
    task = _create_auto_task("test-pipeline-checkpoint-subtitle")
    engine = PipelineEngine()
    job = _claim_auto_job(task["id"], TaskStatus.SUBTITLE_DRAFTING, "subtitle-checkpoint-owner")
    with get_connection() as connection:
        connection.execute(
            "UPDATE tasks SET status = ? WHERE id = ?",
            (TaskStatus.PENDING_SUBTITLE_REVIEW.value, task["id"]),
        )
        connection.commit()
    with job_service.job_lease_context(job["id"], "subtitle-checkpoint-owner", job["lease_token"]):
        checkpoint = _checkpoint(engine, task, job, TaskStatus.SUBTITLE_DRAFTING)
        checkpoint.begin_step(TaskStatus.SUBTITLE_DRAFTING.value)
        checkpoint.complete_step(TaskStatus.SUBTITLE_DRAFTING.value, outputs={"track_ids": ["track"]})
        engine._restore_checkpoint_step = Mock(return_value={"status": "pending_subtitle_review"})
        engine._prepare_subtitle_drafts = Mock(side_effect=AssertionError("字幕草稿不应重复生成"))
        engine._generate_metadata = Mock(side_effect=AssertionError("人工审核前不得继续文案步骤"))
        engine._write_task_summary = Mock(return_value={"summary_path": str(tmp_path / "summary.json")})
        result = engine.run(
            task["id"],
            start_step=TaskStatus.SUBTITLE_DRAFTING,
            job_id=job["id"],
        )

    assert result["status"] == "pending_subtitle_review"
    engine._prepare_subtitle_drafts.assert_not_called()
    engine._generate_metadata.assert_not_called()
