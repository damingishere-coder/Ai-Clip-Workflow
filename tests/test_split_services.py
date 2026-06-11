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
    soft_delete_task,
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

    def test_create_task_record_with_nas_source(self):
        """NAS 来源任务应有 pending_video 状态（因为没有文件校验）"""
        payload = TaskCreate(
            task_name="NAS 测试",
            source_type="nas",
            platform="bilibili",
            nas_file_path="/not/exist/video.mp4",
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        # NAS 路径不存在时会抛出 ValueError
        with pytest.raises(ValueError, match="文件不存在|路径不存在"):
            create_task_record(payload, task_id="test-nas-001")

    def test_update_task_status_ok(self):
        """更新已存在任务的状态"""
        # 先创建
        payload = TaskCreate(
            task_name="状态更新测试",
            source_type="upload",
            platform="general",
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

    def test_soft_delete_task(self):
        """软删除任务标记 is_deleted=1"""
        payload = TaskCreate(
            task_name="删除测试",
            source_type="upload",
            platform="general",
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        create_task_record(payload, task_id="test-delete-001")

        result = soft_delete_task("test-delete-001")
        assert "已隐藏" in result["message"]

        # 再次删除应提示无需重复操作
        result2 = soft_delete_task("test-delete-001")
        assert "无需重复" in result2["message"]

    def test_update_candidate_clip_count_invalid(self):
        """候选片段数量超出范围应报错"""
        payload = TaskCreate(
            task_name="数量测试",
            source_type="upload",
            platform="general",
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
    def test_missing_transcript_triggers_error(self):
        """没有转写文件时 AI 分析应报错"""
        from app.services.ai_analysis_workflow_service import process_task_ai_analysis
        from app.services.task_lifecycle_service import create_task_record
        from app.models.task import TaskCreate

        payload = TaskCreate(
            task_name="AI 测试-无转写",
            source_type="upload",
            platform="general",
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        create_task_record(payload, task_id="test-ai-001")

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
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        create_task_record(payload, task_id="test-ai-status-001")

        status = get_task_ai_analysis_status("test-ai-status-001")
        assert status["status"] == "idle"
        assert status["analysis_exists"] is False
        assert status["is_running"] is False


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
            max_clip_duration=3,
            candidate_clip_count=5,
            ai_preference="",
        )
        create_task_record(payload, task_id="test-trans-001")

        with pytest.raises(ValueError, match="音频"):
            process_task_transcript("test-trans-001")
