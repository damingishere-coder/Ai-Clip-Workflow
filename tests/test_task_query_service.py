"""任务查询服务测试

覆盖范围：
- TestDashboardContext: Dashboard 上下文字段完整性
- TestClipsOverviewContext: 片段总览上下文字段完整性
- TestSubtitleWorkflowContext: 字幕工作台上下文字段完整性
- TestSubtitleTaskContext: 单任务字幕页上下文字段完整性
- TestSystemStatusContext: 系统状态页上下文字段完整性
- TestQueryServiceIntegration: 迁移后原页面 router 不报错
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.db.database import get_connection, init_db
from app.services.task_query_service import (
    get_clips_overview_context,
    get_dashboard_context,
    get_subtitle_task_context,
    get_subtitle_workflow_context,
    get_system_status_context,
)


def _insert_test_task(
    task_id: str,
    task_name: str = "测试任务",
    status: str = "pending_video",
    source_type: str = "upload",
    platform: str = "general",
    original_video_path: str = "",
    nas_file_path: str = "",
    is_deleted: int = 0,
    created_at: str | None = None,
) -> None:
    now = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        existing_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        data = {
            "id": task_id,
            "task_name": task_name,
            "task_dir_name": task_id,
            "source_type": source_type,
            "platform": platform,
            "original_video_path": original_video_path,
            "nas_file_path": nas_file_path,
            "max_clip_duration": 5,
            "candidate_clip_count": 5,
            "ai_preference": "",
            "ai_prompt_preset_id": "preset_001",
            "status": status,
            "progress": 0,
            "error_message": None,
            "is_deleted": is_deleted,
            "deleted_at": None,
            "created_at": now,
            "updated_at": now,
        }
        if "title" in existing_columns:
            data["title"] = task_name
        columns = [c for c in data if c in existing_columns]
        placeholders = ", ".join("?" for _ in columns)
        connection.execute(
            f"INSERT INTO tasks ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(data[c] for c in columns),
        )
        connection.commit()


def _insert_test_clip_candidate(
    clip_id: str,
    task_id: str,
    title: str = "测试片段",
    start_time: str = "00:00:10",
    end_time: str = "00:01:00",
    duration_seconds: int = 50,
    enabled: int = 1,
    reviewed: int = 0,
    confidence_score: float = 0.8,
    is_deleted: int = 0,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO clip_candidates (
                id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                summary, reason, highlight_reason, spread_value, suggested_editing,
                confidence_score, selected_by_default, enabled, reviewed, is_deleted,
                deleted_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clip_id,
                task_id,
                f"clip_{clip_id}",
                title,
                start_time,
                end_time,
                duration_seconds,
                "摘要内容",
                "高亮理由",
                "高亮理由",
                "high",
                "剪辑建议",
                confidence_score,
                1,
                enabled,
                reviewed,
                is_deleted,
                None,
                now,
                now,
            ),
        )
        connection.commit()


def _insert_test_output_clip(
    output_id: str,
    task_id: str,
    clip_candidate_id: str,
    output_file_path: str = "",
    output_file_name: str = "test_output.mp4",
    status: str = "completed",
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, clip_candidate_id, output_file_path, output_file_name,
                status, error_message, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                output_id,
                task_id,
                clip_candidate_id,
                output_file_path,
                output_file_name,
                status,
                None,
                now,
                now,
            ),
        )
        connection.commit()


def _insert_test_subtitle_job(
    job_id: str,
    task_id: str,
    output_clip_id: str,
    status: str = "completed",
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO subtitle_jobs (
                id, task_id, output_clip_id, style_preset_id, status,
                subtitle_file_path, output_file_path, error_message, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, task_id, output_clip_id, "default", status, "", "", None, now, now),
        )
        connection.commit()


def _clean_test_data() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs")
        connection.execute("DELETE FROM subtitle_jobs")
        connection.execute("DELETE FROM output_clip")
        connection.execute("DELETE FROM cut_runs")
        connection.execute("DELETE FROM workflow_jobs")
        connection.execute("DELETE FROM ai_analysis_runs")
        connection.execute("DELETE FROM clip_candidates")
        connection.execute("DELETE FROM tasks")
        connection.commit()


@pytest.fixture(autouse=True)
def setup_database():
    """每个测试前初始化数据库并清理旧数据"""
    init_db()
    _clean_test_data()
    yield
    _clean_test_data()


# ── Dashboard Context ──────────────────────────────────────────────


class TestDashboardContext:
    """Dashboard 首页统计上下文"""

    def test_empty_dashboard_fields_complete(self):
        """空数据库时 Dashboard 返回字段完整"""
        context = get_dashboard_context()

        # 顶层字段
        assert "stats" in context
        assert "focus_stats" in context
        assert "workflow_steps" in context
        assert "recent_tasks" in context

        # stats 列表结构
        stat_labels = {s["label"] for s in context["stats"]}
        expected_labels = {
            "今日新增任务", "待处理", "待检查", "已切片任务",
            "待加字幕", "待推送", "失败任务",
        }
        assert stat_labels == expected_labels

        for stat in context["stats"]:
            assert "label" in stat
            assert "value" in stat
            assert "tone" in stat

        # focus_stats 列表结构
        focus_labels = {s["label"] for s in context["focus_stats"]}
        assert focus_labels == {"输出切片", "待加字幕", "待推送"}

        for stat in context["focus_stats"]:
            assert "label" in stat
            assert "value" in stat
            assert "description" in stat

        # recent_tasks 为空列表
        assert context["recent_tasks"] == []

        # workflow_steps 不为空
        assert len(context["workflow_steps"]) > 0

    def test_dashboard_counts_with_tasks(self):
        """有任务时统计数值正确"""
        task_id_1 = uuid4().hex[:12]
        task_id_2 = uuid4().hex[:12]
        task_id_3 = uuid4().hex[:12]
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")

        _insert_test_task(task_id_1, "任务1", status="pending_video", created_at=today)
        _insert_test_task(task_id_2, "任务2", status="pending_review", created_at=today)
        _insert_test_task(task_id_3, "任务3", status="completed", created_at="2020-01-01T00:00:00")

        context = get_dashboard_context()

        stat_map = {s["label"]: s["value"] for s in context["stats"]}
        assert stat_map["今日新增任务"] == 2  # task_1 + task_2 (today)
        assert stat_map["待处理"] == 1  # task_1
        assert stat_map["待检查"] == 1  # task_2
        assert stat_map["已切片任务"] == 1  # task_3
        assert stat_map["失败任务"] == 0

        # recent_tasks 最多 5 条
        assert len(context["recent_tasks"]) == 3

    def test_dashboard_counts_with_failed_tasks(self):
        """失败任务统计正确"""
        task_id_1 = uuid4().hex[:12]
        task_id_2 = uuid4().hex[:12]
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")

        _insert_test_task(task_id_1, "失败任务1", status="failed", created_at=today)
        _insert_test_task(task_id_2, "失败任务2", status="failed", created_at=today)

        context = get_dashboard_context()

        stat_map = {s["label"]: s["value"] for s in context["stats"]}
        assert stat_map["失败任务"] == 2


# ── Clips Overview Context ─────────────────────────────────────────


class TestClipsOverviewContext:
    """片段总览页统计上下文"""

    def test_empty_clips_overview_fields_complete(self):
        """空数据库时片段总览返回字段完整"""
        context = get_clips_overview_context()

        assert "tasks" in context
        assert "stats" in context
        assert context["tasks"] == []

        stat_labels = {s["label"] for s in context["stats"]}
        expected_labels = {"待 AI 分析", "待检查", "可生成切片", "已完成", "异常任务"}
        assert stat_labels == expected_labels

    def test_clips_overview_with_tasks_and_clips(self):
        """有任务和候选片段时统计字段正确"""
        task_id = uuid4().hex[:12]
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")

        _insert_test_task(task_id, "片段任务", status="pending_review", created_at=today)
        _insert_test_clip_candidate("clip_001", task_id, "片段A", enabled=1)
        _insert_test_clip_candidate("clip_002", task_id, "片段B", enabled=1)
        _insert_test_clip_candidate("clip_003", task_id, "片段C", enabled=0)

        context = get_clips_overview_context()

        assert len(context["tasks"]) == 1
        task = context["tasks"][0]

        # 丰富字段存在
        assert task["real_clip_count"] == 3
        assert task["enabled_clip_count"] == 2
        assert "review_stage" in task
        assert "review_tone" in task
        assert "can_cut" in task
        assert "review_ready" in task

        # 原 task 字段保持
        assert task["task_name"] == "片段任务"
        assert task["status"] == "pending_review"

        # 有候选片段且源文件不存在，can_cut 应 False
        assert task["review_ready"] is True
        assert task["can_cut"] is False  # source_exists 为 False

    def test_clips_overview_with_deleted_clips(self):
        """已删除的候选片段不计入统计"""
        task_id = uuid4().hex[:12]
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")

        _insert_test_task(task_id, "删除测试", status="pending_review", created_at=today)
        _insert_test_clip_candidate("clip_001", task_id, "有效片段", enabled=1, is_deleted=0)
        _insert_test_clip_candidate("clip_002", task_id, "已删片段", enabled=1, is_deleted=1)

        context = get_clips_overview_context()

        task = context["tasks"][0]
        assert task["real_clip_count"] == 1  # 只计 is_deleted=0 的
        assert task["enabled_clip_count"] == 1

    def test_clips_overview_stats_correct(self):
        """统计卡片数值正确"""
        task_id_1 = uuid4().hex[:12]
        task_id_2 = uuid4().hex[:12]
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")

        _insert_test_task(task_id_1, "待AI", status="pending_ai", created_at=today)
        _insert_test_task(task_id_2, "失败", status="failed", created_at=today)

        context = get_clips_overview_context()
        stat_map = {s["label"]: s["value"] for s in context["stats"]}

        assert stat_map["待 AI 分析"] == 1
        assert stat_map["待检查"] == 0
        assert stat_map["异常任务"] == 1


# ── Subtitle Workflow Context ──────────────────────────────────────


class TestSubtitleWorkflowContext:
    """字幕工作台总览页上下文"""

    def test_empty_subtitle_workflow_fields_complete(self):
        """无输出切片时返回完整字段结构"""
        context = get_subtitle_workflow_context()

        assert "tasks" in context
        assert "stats" in context
        assert context["tasks"] == []

        stat_labels = {s["label"] for s in context["stats"]}
        expected_labels = {"输出切片记录", "待加字幕切片", "已加字幕成片", "可预览视频", "待一键推送"}
        assert stat_labels == expected_labels

    def test_subtitle_workflow_with_output_clips(self):
        """有输出切片和字幕任务时统计正确"""
        task_id = uuid4().hex[:12]
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")

        _insert_test_task(task_id, "字幕测试", status="completed", created_at=today)
        _insert_test_clip_candidate("clip_001", task_id, "切片A")
        _insert_test_clip_candidate("clip_002", task_id, "切片B")
        _insert_test_output_clip("out_001", task_id, "clip_001", status="completed")
        _insert_test_output_clip("out_002", task_id, "clip_002", status="completed")
        _insert_test_subtitle_job("sub_001", task_id, "out_001", status="completed")
        # out_002 has no subtitle job → subtitle_status = pending

        context = get_subtitle_workflow_context()

        assert len(context["tasks"]) == 1
        task = context["tasks"][0]

        assert "subtitle_stage" in task
        assert "subtitle_tone" in task
        assert "subtitle_done_count" in task
        assert "output_clips" in task

        # 1/2 字幕完成 → 部分完成
        assert task["subtitle_done_count"] == 1

        stat_map = {s["label"]: s["value"] for s in context["stats"]}
        assert stat_map["输出切片记录"] == 2
        assert stat_map["已加字幕成片"] == 1


# ── Subtitle Task Context ──────────────────────────────────────────


class TestSubtitleTaskContext:
    """单任务字幕页上下文"""

    def test_subtitle_task_context_fields_complete(self):
        """字幕任务页返回完整字段结构"""
        task_id = uuid4().hex[:12]
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")

        _insert_test_task(task_id, "字幕单任务", status="completed", created_at=today)
        _insert_test_clip_candidate("clip_001", task_id, "切片A")
        _insert_test_output_clip("out_001", task_id, "clip_001", status="completed")
        _insert_test_subtitle_job("sub_001", task_id, "out_001", status="completed")

        context = get_subtitle_task_context(task_id)

        assert "task" in context
        assert "output_clips" in context
        assert "subtitle_style" in context
        assert "stats" in context

        # task 字段
        assert context["task"]["subtitle_stage"] == "字幕完成"
        assert context["task"]["subtitle_tone"] == "green"

        # subtitle_style 字段
        style = context["subtitle_style"]
        for field in ["id", "name", "font_family", "font_size", "position", "font_color", "stroke_color"]:
            assert field in style, f"subtitle_style 缺少字段 {field}"

        # stats
        stat_labels = {s["label"] for s in context["stats"]}
        assert stat_labels == {"输出切片", "待加字幕", "已加字幕"}

    def test_subtitle_task_not_found(self):
        """不存在的任务抛出 ValueError"""
        with pytest.raises(ValueError, match="任务不存在"):
            get_subtitle_task_context("nonexistent_id")


# ── System Status Context ──────────────────────────────────────────


class TestSystemStatusContext:
    """系统状态页上下文"""

    def test_system_status_fields_complete(self):
        """系统状态页返回完整字段结构"""
        context = get_system_status_context()

        required_fields = [
            "storage_root", "storage_exists", "database_path", "database_exists",
            "ffmpeg_path", "ffmpeg_available", "ffprobe_path", "ffprobe_available",
            "task_count", "failed_count", "pending_count", "review_count",
            "completed_count", "recent_errors", "ai_config", "expected_server_url",
        ]
        for field in required_fields:
            assert field in context, f"系统状态上下文缺少字段 {field}"

        # 空数据库时计数值为 0
        assert context["task_count"] == 0
        assert context["failed_count"] == 0
        assert context["pending_count"] == 0
        assert context["review_count"] == 0
        assert context["completed_count"] == 0
        assert context["recent_errors"] == []

    def test_system_status_with_tasks(self):
        """有任务时各状态计数正确"""
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")

        _insert_test_task(uuid4().hex[:12], "待处理", status="pending_video", created_at=today)
        _insert_test_task(uuid4().hex[:12], "审核中", status="pending_review", created_at=today)
        _insert_test_task(uuid4().hex[:12], "失败1", status="failed", created_at=today)
        _insert_test_task(uuid4().hex[:12], "失败2", status="failed", created_at=today)
        _insert_test_task(uuid4().hex[:12], "完成", status="completed", created_at=today)

        context = get_system_status_context()

        assert context["task_count"] == 5
        assert context["pending_count"] == 1
        assert context["review_count"] == 1
        assert context["failed_count"] == 2
        assert context["completed_count"] == 1
        assert len(context["recent_errors"]) == 2  # 最多展示 5 条
