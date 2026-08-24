"""阶段 P1-4：拆分 task_service 后的服务层单元测试

覆盖 task_log_service / task_lifecycle_service /
字幕 / 切片 / AI 分析的入口校验与错误路径。
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models.task import TaskCreate, TaskStatus
from app.services.task_log_service import append_task_log, read_task_log_tail
from app.services.task_lifecycle_service import (
    create_task_record,
    delete_task_permanently,
    update_task_candidate_clip_count,
    update_task_status,
)
from app.services.storage_service import get_artifact_paths


# ---------------------------------------------------------------------------
# task_log_service
# ---------------------------------------------------------------------------

class TestTaskLogService:
    def test_append_and_read_tail(self, tmp_path):
        """写入一条日志后应能从尾部读取到"""
        task_id = "test-log-001"
        # 临时覆盖 storage root 让日志写入 tmp_path
        with patch("app.services.task_log_service.get_artifact_paths") as mock_paths:
            log_file = tmp_path / "task.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            mock_paths.return_value = {"log_path": log_file}

            append_task_log(task_id, "第一条日志")
            append_task_log(task_id, "第二条日志")

            tail = read_task_log_tail(task_id, limit=1)
            assert len(tail) == 1
            assert "第二条日志" in tail[0]

    def test_read_tail_file_not_exists(self):
        """日志文件不存在时应返回空列表"""
        with patch("app.services.task_log_service.get_artifact_paths") as mock_paths:
            mock_paths.return_value = {"log_path": Path("/nonexistent/task.log")}
            tail = read_task_log_tail("no-such-task")
            assert tail == []

    def test_append_creates_parent_dir(self, tmp_path):
        """写入日志时应自动创建父目录"""
        task_id = "test-log-002"
        log_file = tmp_path / "subdir" / "task.log"
        assert not log_file.parent.exists()

        with patch("app.services.task_log_service.get_artifact_paths") as mock_paths:
            mock_paths.return_value = {"log_path": log_file}
            append_task_log(task_id, "测试日志")

        assert log_file.parent.exists()
        assert log_file.exists()


# ---------------------------------------------------------------------------
# task_lifecycle_service
# ---------------------------------------------------------------------------

class TestTaskLifecycle:
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """每个测试前后清理数据库"""
        from app.db.database import get_connection

        yield
        # 清理测试数据
        with get_connection() as conn:
            conn.execute("DELETE FROM tasks WHERE id LIKE 'test-%'")
            conn.commit()

    def test_create_task_record_minimal(self):
        """用最少字段创建任务应成功并返回完整字段"""
        payload = TaskCreate(
            task_name="测试任务-最小",
            source_type="upload",
            platform="general",
            selection_profile="general",
            max_clip_duration=5,
            candidate_clip_count=8,
            ai_preference="",
        )
        result = create_task_record(payload, task_id="test-create-001")
        assert result["id"] == "test-create-001"
        assert result["task_name"] == "测试任务-最小"
        assert result["status"] in {
            TaskStatus.pending_video.value,
            TaskStatus.pending_processing.value,
        }
        assert "detail_url" in result

    def test_task_create_model_no_longer_exposes_nas_source(self):
        """新建任务模型只接收上传后的原片路径，不再暴露 NAS 参数。"""
        fields = getattr(TaskCreate, "model_fields", getattr(TaskCreate, "__fields__", {}))
        assert "source_type" not in fields
        assert "nas_file_path" not in fields

    def test_update_task_status_ok(self):
        """更新已存在任务的状态"""
        # 先创建
        payload = TaskCreate(
            task_name="状态更新测试",
            source_type="upload",
            platform="general",
            selection_profile="general",
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        create_task_record(payload, task_id="test-status-001")

        # 更新状态
        updated = update_task_status("test-status-001", TaskStatus.completed)
        assert updated is not None
        assert updated["status"] == TaskStatus.completed.value
        assert updated["progress"] == 100

    def test_update_task_status_nonexistent(self):
        """更新不存在的任务返回 None"""
        result = update_task_status("nonexistent-id", TaskStatus.completed)
        assert result is None

    def test_permanent_delete_task(self):
        """永久删除任务文件并保留隐藏数据库记录"""
        payload = TaskCreate(
            task_name="删除测试",
            source_type="upload",
            platform="general",
            selection_profile="general",
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        create_task_record(payload, task_id="test-delete-001")

        result = delete_task_permanently("test-delete-001")
        assert result["status"] == "deleted"
        assert "永久删除" in result["message"]

        result2 = delete_task_permanently("test-delete-001")
        assert result2["status"] == "already_deleted"

    def test_update_candidate_clip_count_invalid(self):
        """候选片段数量超出范围应报错"""
        payload = TaskCreate(
            task_name="数量测试",
            source_type="upload",
            platform="general",
            selection_profile="general",
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        create_task_record(payload, task_id="test-count-001")

        with pytest.raises(ValueError, match="1 到 50"):
            update_task_candidate_clip_count("test-count-001", 0)

        with pytest.raises(ValueError, match="1 到 50"):
            update_task_candidate_clip_count("test-count-001", 51)


# ---------------------------------------------------------------------------
# AI 分析入口校验
# ---------------------------------------------------------------------------

class TestAIAnalysisEntry:
    @pytest.fixture(autouse=True)
    def cleanup_ai_rows(self):
        from app.db.database import get_connection

        yield
        with get_connection() as connection:
            connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE 'test-ai-%'")
            connection.execute("DELETE FROM workflow_jobs WHERE task_id LIKE 'test-ai-%'")
            connection.execute("DELETE FROM output_clip WHERE task_id LIKE 'test-ai-%'")
            connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE 'test-ai-%'")
            connection.execute("DELETE FROM ai_analysis_runs WHERE task_id LIKE 'test-ai-%'")
            connection.execute("DELETE FROM tasks WHERE id LIKE 'test-ai-%'")
            connection.commit()

    def test_missing_transcript_triggers_error(self):
        """没有转写文件时 AI 分析应报错"""
        from app.services.ai_analysis_workflow_service import process_task_ai_analysis
        from app.services.task_lifecycle_service import create_task_record
        from app.models.task import TaskCreate

        payload = TaskCreate(
            task_name="AI 测试-无转写",
            source_type="upload",
            platform="general",
            selection_profile="general",
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        create_task_record(payload, task_id="test-ai-001")
        update_task_status("test-ai-001", TaskStatus.pending_ai)

        with pytest.raises(ValueError, match="转写"):
            process_task_ai_analysis("test-ai-001")

    def test_analysis_status_idle(self):
        """刚创建的任务 AI 分析状态应为 idle"""
        from app.services.ai_analysis_workflow_service import get_task_ai_analysis_status
        from app.services.task_lifecycle_service import create_task_record
        from app.models.task import TaskCreate

        payload = TaskCreate(
            task_name="AI 状态测试",
            source_type="upload",
            platform="general",
            selection_profile="general",
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        create_task_record(payload, task_id="test-ai-status-001")

        status = get_task_ai_analysis_status("test-ai-status-001")
        assert status["status"] == "idle"
        assert status["analysis_exists"] is False
        assert status["is_running"] is False

    def test_replace_clip_candidates_keeps_new_rows(self):
        from app.services.ai_analysis_workflow_service import _replace_clip_candidates
        from app.services.task_lifecycle_service import create_task_record
        from app.services.task_service import list_clip_candidates

        task_id = "test-ai-replace"
        create_task_record(
            TaskCreate(task_name="候选替换测试", source_type="upload", platform="general", selection_profile="general"),
            task_id=task_id,
        )
        _replace_clip_candidates(
            task_id,
            [
                {
                    "clip_id": "clip-001",
                    "title": "新片段",
                    "start_time": "00:10",
                    "end_time": "00:40",
                    "duration_seconds": 30,
                    "summary": "摘要",
                    "highlight_reason": "亮点",
                    "spread_value": "高",
                    "suggested_editing": "直接切",
                    "confidence_score": 0.9,
                    "selected_by_default": True,
                }
            ],
        )

        clips = list_clip_candidates(task_id)
        assert len(clips) == 1
        assert clips[0]["title"] == "新片段"

    def test_replace_clip_candidates_rolls_back_when_insert_fails(self):
        import sqlite3

        from app.db.database import get_connection
        from app.services.ai_analysis_workflow_service import _replace_clip_candidates
        from app.services.task_lifecycle_service import create_task_record
        from app.services.task_service import _now_iso, list_clip_candidates

        task_id = "test-ai-replace-rollback"
        create_task_record(
            TaskCreate(task_name="候选回滚测试", source_type="upload", platform="general", selection_profile="general"),
            task_id=task_id,
        )
        now = _now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO clip_candidates (
                    id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                    selected_by_default, enabled, reviewed, created_at, updated_at
                )
                VALUES (?, ?, 'old', '旧片段', '00:00', '00:20', 20, 1, 1, 0, ?, ?)
                """,
                (f"{task_id}_old", task_id, now, now),
            )
            connection.commit()

        duplicate_clips = [
            {
                "clip_id": "same",
                "title": "新片段",
                "start_time": "00:10",
                "end_time": "00:40",
                "duration_seconds": 30,
                "summary": "摘要",
                "highlight_reason": "亮点",
                "spread_value": "高",
                "suggested_editing": "直接切",
                "confidence_score": 0.9,
                "selected_by_default": True,
            },
            {
                "clip_id": "same",
                "title": "重复片段",
                "start_time": "00:50",
                "end_time": "01:20",
                "duration_seconds": 30,
                "summary": "摘要",
                "highlight_reason": "亮点",
                "spread_value": "中",
                "suggested_editing": "直接切",
                "confidence_score": 0.8,
                "selected_by_default": True,
            },
        ]

        with pytest.raises(sqlite3.IntegrityError):
            _replace_clip_candidates(task_id, duplicate_clips)

        clips = list_clip_candidates(task_id)
        assert len(clips) == 1
        assert clips[0]["title"] == "旧片段"

    def test_replace_clip_candidates_rejects_existing_output_reference(self):
        from app.db.database import get_connection
        from app.services.ai_analysis_workflow_service import (
            AIAnalysisConflictError,
            _replace_clip_candidates,
        )
        from app.services.task_service import list_clip_candidates

        task_id = "test-ai-output-reference"
        create_task_record(TaskCreate(task_name="已有切片", selection_profile="general"), task_id=task_id)
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO clip_candidates (
                    id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                    selected_by_default, enabled, reviewed, created_at, updated_at
                ) VALUES ('test-ai-output-reference_clip_001', ?, 'old', '旧候选',
                          '00:00', '00:30', 30, 1, 1, 1, 'now', 'now')
                """,
                (task_id,),
            )
            connection.execute(
                """
                INSERT INTO output_clip (
                    id, task_id, clip_candidate_id, output_file_path, output_file_name,
                    status, is_active, created_at, updated_at
                ) VALUES ('test-ai-output-reference-out', ?, 'test-ai-output-reference_clip_001',
                          'clip.mp4', 'clip.mp4', 'completed', 1, 'now', 'now')
                """,
                (task_id,),
            )
            connection.commit()

        with pytest.raises(AIAnalysisConflictError, match="已有切片引用"):
            _replace_clip_candidates(task_id, [])

        assert [clip["title"] for clip in list_clip_candidates(task_id)] == ["旧候选"]
        with get_connection() as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    def test_manual_ai_reentry_is_blocked_before_status_change(self):
        from app.db.database import get_connection
        from app.services.ai_analysis_workflow_service import AIAnalysisConflictError, process_task_ai_analysis
        from app.services.task_service import get_task

        task_id = "test-ai-manual-reentry"
        create_task_record(TaskCreate(task_name="AI 重入保护", selection_profile="general"), task_id=task_id)
        update_task_status(task_id, TaskStatus.completed)
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO output_clip (
                    id, task_id, output_file_path, output_file_name, status, is_active, created_at, updated_at
                ) VALUES ('test-ai-manual-reentry-out', ?, 'clip.mp4', 'clip.mp4',
                          'completed', 1, 'now', 'now')
                """,
                (task_id,),
            )
            connection.commit()
        before = get_task(task_id, include_video_probe=False)

        with pytest.raises(AIAnalysisConflictError, match="已经生成切片"):
            process_task_ai_analysis(task_id)

        after = get_task(task_id, include_video_probe=False)
        assert after["status"] == before["status"]
        assert after["error_message"] == before["error_message"]

    def test_restore_ai_history_recreates_candidates(self):
        from app.services.ai_analysis_workflow_service import (
            _insert_ai_analysis_run,
            restore_ai_analysis_run,
        )
        from app.services.task_lifecycle_service import create_task_record
        from app.services.task_service import list_clip_candidates

        task_id = "test-ai-restore-history"
        create_task_record(
            TaskCreate(task_name="历史恢复测试", source_type="upload", platform="general", selection_profile="general"),
            task_id=task_id,
        )
        payload = {
            "analysis_summary": "历史结果",
            "clips": [
                {
                    "clip_id": "history-001",
                    "title": "历史候选",
                    "start_time": "00:20",
                    "end_time": "01:00",
                    "duration_seconds": 40,
                    "summary": "摘要",
                    "highlight_reason": "亮点",
                    "spread_value": "高",
                    "suggested_editing": "直接切",
                    "confidence_score": 0.95,
                    "selected_by_default": True,
                }
            ],
        }
        run = _insert_ai_analysis_run(
            task_id=task_id,
            analysis_payload=payload,
            provider="remote",
            provider_label="远程 AI",
            model="test-model",
            fallback_notice="",
            prompt_preset={"id": "preset_001", "name": "测试 Prompt"},
            requested_clip_count=1,
        )

        result = restore_ai_analysis_run(task_id, run["id"])

        assert result["status"] == "ok"
        clips = list_clip_candidates(task_id)
        assert len(clips) == 1
        assert clips[0]["title"] == "历史候选"


# ---------------------------------------------------------------------------
# 视频切片入口校验
# ---------------------------------------------------------------------------

class TestVideoCutEntry:
    def test_no_enabled_clips_triggers_error(self):
        """没有启用片段时切割应报错"""
        from app.services.video_cut_workflow_service import process_task_video_cuts
        from app.services.task_lifecycle_service import create_task_record
        from app.models.task import TaskCreate

        payload = TaskCreate(
            task_name="切片测试-无片段",
            source_type="upload",
            platform="general",
            selection_profile="general",
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        create_task_record(payload, task_id="test-cut-001")

        # 没有视频源文件也没有启用片段 → 应报错
        with pytest.raises(ValueError):
            process_task_video_cuts("test-cut-001")


# ---------------------------------------------------------------------------
# 字幕入口校验
# ---------------------------------------------------------------------------

class TestSubtitleEntry:
    def test_get_default_style(self):
        """获取默认字幕样式应返回必要字段"""
        from app.services.subtitle_workflow_service import get_default_subtitle_style

        style = get_default_subtitle_style()
        assert "font_family" in style
        assert "font_size" in style
        assert "position" in style
        assert "font_color" in style

    def test_render_missing_output_clip(self):
        """不存在的切片记录加字幕应报错"""
        from app.services.subtitle_workflow_service import render_subtitles_for_output_clip
        from app.services.task_lifecycle_service import create_task_record
        from app.models.task import TaskCreate

        payload = TaskCreate(
            task_name="字幕测试",
            source_type="upload",
            platform="general",
            selection_profile="general",
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        create_task_record(payload, task_id="test-sub-001")

        with pytest.raises(ValueError, match="切片记录不存在"):
            render_subtitles_for_output_clip("test-sub-001", "nonexistent-clip")


# ---------------------------------------------------------------------------
# 转写入口校验
# ---------------------------------------------------------------------------

class TestTranscriptEntry:
    def test_missing_audio_triggers_error(self):
        """没有音频文件时转写应报错"""
        from app.services.transcript_workflow_service import process_task_transcript
        from app.services.task_lifecycle_service import create_task_record
        from app.models.task import TaskCreate

        payload = TaskCreate(
            task_name="转写测试-无音频",
            source_type="upload",
            platform="general",
            selection_profile="general",
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        create_task_record(payload, task_id="test-trans-001")

        with pytest.raises(ValueError, match="音频"):
            process_task_transcript("test-trans-001")
