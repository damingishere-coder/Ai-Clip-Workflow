"""v1.3.0 全自动任务流水线最小测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.main import app
from app.models.task import TaskCreate, TaskStatus
from app.services.auto_publish_service import create_auto_publish_jobs
from app.services.pipeline_engine import PipelineEngine, build_schedule_times
from app.services.storage_service import get_artifact_paths
from app.services.task_lifecycle_service import create_task_record
from app.services.task_service import get_task


@pytest.fixture(autouse=True)
def auto_pipeline_db_cleanup():
    init_db()
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE 'test-auto-%'")
        connection.execute("DELETE FROM subtitle_jobs WHERE task_id LIKE 'test-auto-%'")
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE 'test-auto-%'")
        connection.execute("DELETE FROM cut_runs WHERE task_id LIKE 'test-auto-%'")
        connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE 'test-auto-%'")
        connection.execute("DELETE FROM ai_analysis_runs WHERE task_id LIKE 'test-auto-%'")
        connection.execute("DELETE FROM tasks WHERE id LIKE 'test-auto-%'")
        connection.commit()
    yield
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE 'test-auto-%'")
        connection.execute("DELETE FROM subtitle_jobs WHERE task_id LIKE 'test-auto-%'")
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE 'test-auto-%'")
        connection.execute("DELETE FROM cut_runs WHERE task_id LIKE 'test-auto-%'")
        connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE 'test-auto-%'")
        connection.execute("DELETE FROM ai_analysis_runs WHERE task_id LIKE 'test-auto-%'")
        connection.execute("DELETE FROM tasks WHERE id LIKE 'test-auto-%'")
        connection.commit()


def _headers() -> dict[str, str]:
    if settings.local_admin_token:
        return {"Authorization": f"Bearer {settings.local_admin_token}"}
    return {}


def _fake_video(name: str = "source.mp4") -> Path:
    path = settings.tasks_dir / "_test_inputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake mp4")
    return path


def _create_auto_task(task_id: str = "test-auto-task") -> dict:
    video = _fake_video(f"{task_id}.mp4")
    payload = TaskCreate(
        task_name=task_id,
        source_type="upload",
        platform="general",
        original_video_path=str(video),
        max_clip_duration=5,
        candidate_clip_count=5,
        auto_mode=True,
    )
    create_task_record(payload, task_id=task_id)
    return get_task(task_id, include_video_probe=False)


def test_auto_mode_false_does_not_start_pipeline(monkeypatch):
    starter = Mock(return_value={"status": "started"})
    monkeypatch.setattr("app.routers.tasks.start_auto_pipeline", starter)
    video = _fake_video("manual.mp4")
    payload = {
        "task_name": "test-auto-manual",
        "source_type": "upload",
        "platform": "general",
        "original_video_path": str(video),
        "auto_mode": False,
    }
    with TestClient(app) as client:
        response = client.post("/api/tasks", json=payload, headers=_headers())
    assert response.status_code == 200
    starter.assert_not_called()


def test_auto_mode_true_starts_pipeline(monkeypatch):
    starter = Mock(return_value={"status": "started"})
    monkeypatch.setattr("app.routers.tasks.start_auto_pipeline", starter)
    video = _fake_video("auto.mp4")
    payload = {
        "task_name": "test-auto-start",
        "source_type": "upload",
        "platform": "general",
        "original_video_path": str(video),
        "auto_mode": True,
    }
    with TestClient(app) as client:
        response = client.post("/api/tasks", json=payload, headers=_headers())
    assert response.status_code == 200
    starter.assert_called_once()


def test_existing_transcript_skips_transcription(monkeypatch):
    task = _create_auto_task("test-auto-existing-transcript")
    paths = get_artifact_paths(task["id"])
    paths["transcript_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["transcript_path"].write_text("| 00:00 | 00:03 | 你好 |\n", encoding="utf-8")
    transcribe = Mock()
    monkeypatch.setattr("app.services.pipeline_engine.task_service.process_task_transcript_workflow", transcribe)
    result = PipelineEngine()._transcribe_or_read_text(task["id"], {"config": {}})
    assert result["source"] == "existing"
    transcribe.assert_not_called()


def test_missing_transcript_calls_transcription(monkeypatch):
    task = _create_auto_task("test-auto-generate-transcript")
    paths = get_artifact_paths(task["id"])
    if paths["transcript_path"].exists():
        paths["transcript_path"].unlink()

    def fake_transcribe(task_id, background_tasks=None, force=False):
        paths["transcript_path"].parent.mkdir(parents=True, exist_ok=True)
        paths["transcript_path"].write_text("| 00:00 | 00:03 | 你好 |\n", encoding="utf-8")
        return {"status": "started"}

    transcribe = Mock(side_effect=fake_transcribe)
    monkeypatch.setattr("app.services.pipeline_engine.task_service.process_task_transcript_workflow", transcribe)
    result = PipelineEngine()._transcribe_or_read_text(task["id"], {"config": {}})
    assert result["source"] == "generated"
    transcribe.assert_called_once()


def test_cut_stage_skips_subtitle_rendering(monkeypatch):
    task = _create_auto_task("test-auto-skip-subtitle")
    output_path = settings.tasks_dir / "_test_inputs" / "clip.mp4"
    output_path.write_bytes(b"clip")
    cut_result = {
        "results": [
            {"clip_candidate_id": "clip-1", "status": "completed"},
        ]
    }
    monkeypatch.setattr("app.services.pipeline_engine.task_service.process_task_video_cuts", Mock(return_value=cut_result))
    monkeypatch.setattr(
        "app.services.pipeline_engine.task_service.list_output_clips",
        Mock(return_value=[{"id": "out-1", "status": "completed", "file_exists": True, "output_file_path": str(output_path)}]),
    )
    subtitle_render = Mock()
    monkeypatch.setattr("app.services.task_service.render_subtitles_for_output_clip", subtitle_render)
    result = PipelineEngine()._cut_video(task["id"], {"config": {}})
    assert result["success_count"] == 1
    subtitle_render.assert_not_called()


def test_ai_bad_json_records_failed_status(monkeypatch):
    task = _create_auto_task("test-auto-ai-json")
    paths = get_artifact_paths(task["id"])
    paths["transcript_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["transcript_path"].write_text("| 00:00 | 00:03 | 你好 |\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.services.pipeline_engine.task_service.process_task_ai_analysis",
        Mock(side_effect=ValueError("AI 返回非法 JSON")),
    )
    result = PipelineEngine().run(task["id"])
    updated = get_task(task["id"], include_video_probe=False)
    assert result["failed_status"] == TaskStatus.FAILED_AI_ANALYZING.value
    assert updated["status"] == TaskStatus.FAILED_AI_ANALYZING.value
    assert "AI 返回非法 JSON" in updated["last_error"]


def test_single_clip_failure_does_not_block_other_clips(monkeypatch):
    task = _create_auto_task("test-auto-partial-cut")
    output_path = settings.tasks_dir / "_test_inputs" / "partial.mp4"
    output_path.write_bytes(b"clip")
    monkeypatch.setattr(
        "app.services.pipeline_engine.task_service.process_task_video_cuts",
        Mock(
            return_value={
                "results": [
                    {"clip_candidate_id": "clip-1", "status": "completed"},
                    {"clip_candidate_id": "clip-2", "status": "failed", "error_message": "bad timestamp"},
                ]
            }
        ),
    )
    monkeypatch.setattr(
        "app.services.pipeline_engine.task_service.list_output_clips",
        Mock(return_value=[{"id": "out-1", "status": "completed", "file_exists": True, "output_file_path": str(output_path)}]),
    )
    result = PipelineEngine()._cut_video(task["id"], {"config": {}})
    assert result["success_count"] == 1
    assert result["failed_count"] == 1


def test_schedule_generation_defaults_to_ten_minutes_then_three_hours():
    now = datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc)
    scheduled = build_schedule_times(2, {"auto_schedule_mode": "default"}, now=now)
    assert scheduled[0].startswith("2026-06-23T08:10:00")
    assert scheduled[1].startswith("2026-06-23T11:10:00")


def test_create_auto_publish_job_records_scheduled_at():
    task = _create_auto_task("test-auto-publish-job")
    clip_path = _fake_video("publish_clip.mp4")
    with get_connection() as connection:
        now = "2026-06-23T08:00:00+00:00"
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, clip_candidate_id, output_file_path, output_file_name,
                status, is_active, created_at, updated_at
            )
            VALUES ('out-1', ?, NULL, ?, 'publish_clip.mp4', 'completed', 1, ?, ?)
            """,
            (task["id"], str(clip_path), now, now),
        )
        connection.commit()
    scheduled_items = [
        {
            "output_clip": {"id": "out-1", "output_file_path": str(clip_path)},
            "metadata": {
                "platform": "douyin",
                "title": "康熙名场面",
                "caption": "经典综艺片段",
                "hashtags": ["康熙来了", "经典综艺"],
                "cover_text": "康熙名场面",
                "risk_flags": [],
                "source": "rule",
            },
            "scheduled_at": "2026-06-23T08:10:00+00:00",
        }
    ]
    result = create_auto_publish_jobs(task, scheduled_items)
    assert result["created_count"] == 1
    with get_connection() as connection:
        row = connection.execute(
            "SELECT scheduled_at, status, video_source FROM publish_jobs WHERE task_id = ?",
            (task["id"],),
        ).fetchone()
    assert row["scheduled_at"] == "2026-06-23T08:10:00+00:00"
    assert row["status"] == "SCHEDULED"
    assert row["video_source"] == "original"


def test_create_auto_publish_job_without_schedule_waits_for_send_center():
    task = _create_auto_task("test-auto-publish-waiting")
    clip_path = _fake_video("publish_waiting_clip.mp4")
    with get_connection() as connection:
        now = "2026-06-23T08:00:00+00:00"
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, clip_candidate_id, output_file_path, output_file_name,
                status, is_active, created_at, updated_at
            )
            VALUES ('out-waiting', ?, NULL, ?, 'publish_waiting_clip.mp4', 'completed', 1, ?, ?)
            """,
            (task["id"], str(clip_path), now, now),
        )
        connection.commit()
    result = create_auto_publish_jobs(
        task,
        [
            {
                "output_clip": {"id": "out-waiting", "output_file_path": str(clip_path)},
                "metadata": {
                    "platform": "douyin",
                    "title": "待排期片段",
                    "caption": "等待发送中心设置时间",
                    "hashtags": ["待排期"],
                    "cover_text": "待排期",
                    "risk_flags": [],
                    "source": "rule",
                },
                "scheduled_at": "",
            }
        ],
    )
    assert result["created_count"] == 1
    with get_connection() as connection:
        row = connection.execute(
            "SELECT scheduled_at, status FROM publish_jobs WHERE task_id = ?",
            (task["id"],),
        ).fetchone()
    assert row["scheduled_at"] == ""
    assert row["status"] == "WAITING"


def test_prepare_source_uses_pathlib_and_writes_reference():
    task = _create_auto_task("test-auto-windows-path")
    result = PipelineEngine()._prepare_source(task["id"], {"config": {}})
    reference_path = get_artifact_paths(task["id"])["task_dir"] / "source" / "source_reference.json"
    assert Path(result["source_path"]).exists()
    assert reference_path.exists()


def test_auto_selection_uses_candidate_count_and_task_max_duration():
    task_id = "test-auto-selection-rules"
    video = _fake_video(f"{task_id}.mp4")
    payload = TaskCreate(
        task_name=task_id,
        source_type="upload",
        platform="general",
        original_video_path=str(video),
        max_clip_duration=3,
        candidate_clip_count=2,
        auto_mode=True,
        auto_min_clip_seconds=120,
        auto_max_clip_seconds=7200,
    )
    create_task_record(payload, task_id=task_id)
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        for clip_id, start_time, end_time, confidence in [
            ("short", "00:00", "00:30", 0.8),
            ("medium", "01:00", "02:40", 0.9),
            ("too-long", "03:00", "06:20", 1.0),
        ]:
            connection.execute(
                """
                INSERT INTO clip_candidates (
                    id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                    summary, reason, highlight_reason, spread_value, suggested_editing,
                    confidence_score, selected_by_default, enabled, reviewed, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, '', '', '', '', '', ?, 1, 1, 0, ?, ?)
                """,
                (f"{task_id}_{clip_id}", task_id, clip_id, clip_id, start_time, end_time, confidence, now, now),
            )
        connection.commit()

    result = PipelineEngine()._select_clips(task_id, {"config": {}})

    assert result["target_count"] == 2
    assert result["selected_count"] == 2
    assert {item["clip_id"] for item in result["selected"]} == {
        f"{task_id}_short",
        f"{task_id}_medium",
    }
    assert result["skipped_count"] == 1


def test_auto_resume_endpoint_starts_from_clip_selection(monkeypatch):
    task = _create_auto_task("test-auto-resume")
    paths = get_artifact_paths(task["id"])
    paths["analysis_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["analysis_path"].write_text('{"clips": []}', encoding="utf-8")
    starter = Mock(return_value={"status": "started"})
    monkeypatch.setattr("app.routers.tasks.start_auto_pipeline", starter)

    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{task['id']}/process/auto-resume", headers=_headers())

    assert response.status_code == 200
    assert starter.call_args.args[0] == task["id"]
    assert starter.call_args.kwargs["start_step"] == TaskStatus.CLIP_SELECTING
    assert starter.call_args.kwargs["background_tasks"] is not None
