"""任务查询服务测试

覆盖范围：
- TestDashboardContext: Dashboard 上下文字段完整性
- TestClipsOverviewContext: 片段总览上下文字段完整性
- TestSubtitleWorkflowContext: 字幕工作台上下文字段完整性
- TestSubtitleTaskContext: 单任务字幕页上下文字段完整性
- TestSystemStatusContext: 系统状态页上下文字段完整性
- TestQueryServiceIntegration: 迁移后原页面 router 不报错
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.db.database import get_connection, init_db
from app.core.config import settings
from app.services import task_query_service as queries
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


def _insert_test_publish_job(
    job_id: str,
    task_id: str,
    output_clip_id: str,
    status: str,
    *,
    created_at: str,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, platform, publish_mode,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, 'douyin', 'local_browser', ?, ?, ?)
            """,
            (job_id, task_id, output_clip_id, status, created_at, created_at),
        )
        connection.commit()


def _insert_test_subtitle_job(
    job_id: str,
    task_id: str,
    output_clip_id: str,
    status: str = "completed",
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    media = Path(settings.data_dir) / 'verified-subtitle-fixtures' / f'{job_id}.mp4'
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b'isolated verified subtitle fixture')
    track_id, revision_id = f'track_{job_id}', f'revision_{job_id}'
    with get_connection() as connection:
        connection.execute('''INSERT INTO subtitle_tracks(id,task_id,track_type,output_clip_id,name,active_revision_id,created_at,updated_at)
            VALUES(?,?,'clip',?,'test',?,?,?)''', (track_id,task_id,output_clip_id,revision_id,now,now))
        connection.execute('''INSERT INTO subtitle_revisions(id,track_id,revision_number,origin,status,checksum,created_at)
            VALUES(?,?,1,'test','approved','test',?)''', (revision_id,track_id,now))
        connection.execute(
            """
            INSERT INTO subtitle_jobs (
                id, task_id, output_clip_id, style_preset_id, status,
                subtitle_file_path, output_file_path, error_message, created_at, updated_at,revision_id,validation_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,?,?)
            """,
            (job_id, task_id, output_clip_id, "default", status, "", str(media), None, now, now,revision_id,'verified'),
        )
        connection.commit()


def _clean_test_data() -> None:
    sandbox_value = os.environ.get("NIUMA_PYTEST_SANDBOX_ROOT", "").strip()
    if not sandbox_value:
        raise RuntimeError("拒绝清理测试数据：缺少 NIUMA_PYTEST_SANDBOX_ROOT")
    sandbox_root = Path(sandbox_value).resolve()
    database_path = Path(settings.database_path).resolve()
    try:
        is_in_sandbox = database_path.is_relative_to(sandbox_root)
    except AttributeError:  # pragma: no cover - Python 3.8 兼容
        is_in_sandbox = str(database_path).lower().startswith(str(sandbox_root).lower() + os.sep)
    if not is_in_sandbox or database_path.name != "test_workflow.sqlite3":
        raise RuntimeError(
            "拒绝清理测试数据：数据库不在 pytest sandbox 内或文件名异常；"
            f"database={database_path}, sandbox={sandbox_root}"
        )

    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs")
        connection.execute("DELETE FROM subtitle_jobs")
        connection.execute("DELETE FROM subtitle_cues")
        connection.execute("DELETE FROM subtitle_revisions")
        connection.execute("DELETE FROM subtitle_tracks")
        connection.execute("DELETE FROM output_clip")
        connection.execute("DELETE FROM cut_runs")
        connection.execute("DELETE FROM workflow_jobs")
        connection.execute("DELETE FROM ai_analysis_runs")
        connection.execute("DELETE FROM clip_candidates")
        connection.execute("DELETE FROM tasks")
        connection.commit()


def test_clean_test_data_refuses_database_outside_pytest_sandbox():
    """危险整表清理在路径异常时必须先中止，不能连接活动库。"""
    original_database_path = settings.database_path
    try:
        object.__setattr__(
            settings,
            "database_path",
            Path(__file__).resolve().parents[1] / "data" / "workflow.sqlite3",
        )
        with pytest.raises(RuntimeError, match="拒绝清理测试数据"):
            _clean_test_data()
    finally:
        object.__setattr__(settings, "database_path", original_database_path)


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
        assert "weekly_summary" in context
        assert "recent_tasks" in context

        # stats 列表结构
        stat_labels = {s["label"] for s in context["stats"]}
        expected_labels = {
            "本周新增任务", "已切片任务", "待推送任务", "失败任务",
        }
        assert stat_labels == expected_labels

        for stat in context["stats"]:
            assert "label" in stat
            assert "value" in stat
            assert "tone" in stat

        assert context["weekly_summary"]["total"] == 0
        assert "days" not in context["weekly_summary"]

        # recent_tasks 为空列表
        assert context["recent_tasks"] == []

    def test_dashboard_counts_with_tasks(self):
        """有任务时统计数值正确"""
        task_id_1 = uuid4().hex[:12]
        task_id_2 = uuid4().hex[:12]
        task_id_3 = uuid4().hex[:12]
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")

        _insert_test_task(task_id_1, "任务1", status="pending_video", created_at=today)
        _insert_test_task(task_id_2, "任务2", status="pending_review", created_at=today)
        _insert_test_task(task_id_3, "任务3", status="completed", created_at="2020-01-01T00:00:00")
        _insert_test_clip_candidate("clip-dashboard-completed", task_id_3)
        _insert_test_output_clip("output-dashboard-completed", task_id_3, "clip-dashboard-completed")

        context = get_dashboard_context()

        stat_map = {s["label"]: s["value"] for s in context["stats"]}
        assert stat_map["本周新增任务"] == 2
        assert stat_map["已切片任务"] == 1  # task_3
        assert stat_map["待推送任务"] == 1  # 已切片但还没有发布记录
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

    def test_dashboard_week_uses_shanghai_monday_boundary(self):
        now = datetime(2026, 8, 26, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        _insert_test_task(
            "week-before", "上周日任务", created_at="2026-08-23T15:59:59+00:00"
        )
        _insert_test_task(
            "week-monday", "本周一任务", created_at="2026-08-23T16:00:00+00:00"
        )
        _insert_test_task(
            "week-after", "下周一任务", created_at="2026-08-30T16:00:00+00:00"
        )

        context = get_dashboard_context(now=now)

        assert context["weekly_summary"]["range_label"] == "08.24 - 08.30"
        assert context["weekly_summary"]["total"] == 1
        assert "days" not in context["weekly_summary"]

    def test_dashboard_pending_publish_uses_latest_job_state(self):
        task_id = "dashboard-publish-state"
        _insert_test_task(task_id, "发布状态任务", status="completed")
        for index in range(1, 4):
            candidate_id = f"dashboard-publish-candidate-{index}"
            output_id = f"dashboard-publish-output-{index}"
            _insert_test_clip_candidate(candidate_id, task_id)
            _insert_test_output_clip(output_id, task_id, candidate_id)

        _insert_test_publish_job(
            "dashboard-job-waiting-old",
            task_id,
            "dashboard-publish-output-1",
            "WAITING",
            created_at="2026-08-26T01:00:00+00:00",
        )
        _insert_test_publish_job(
            "dashboard-job-published-new",
            task_id,
            "dashboard-publish-output-1",
            "PUBLISHED",
            created_at="2026-08-26T02:00:00+00:00",
        )
        _insert_test_publish_job(
            "dashboard-job-published-old",
            task_id,
            "dashboard-publish-output-2",
            "PUBLISHED",
            created_at="2026-08-26T01:00:00+00:00",
        )
        _insert_test_publish_job(
            "dashboard-job-scheduled-new",
            task_id,
            "dashboard-publish-output-2",
            "SCHEDULED",
            created_at="2026-08-26T02:00:00+00:00",
        )

        context = get_dashboard_context()
        stat_map = {s["label"]: s for s in context["stats"]}

        assert stat_map["待推送任务"]["value"] == 1
        assert "2 条" in stat_map["待推送任务"]["note"]


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
        expected_labels = {"累计审核任务", "已通过视频", "已完成任务"}
        assert stat_labels == expected_labels
        assert all(stat["value"] == 0 for stat in context["stats"])

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

        stat_map = {stat["label"]: stat for stat in context["stats"]}
        assert stat_map["累计审核任务"]["value"] == 1
        assert stat_map["已通过视频"]["value"] == 2
        assert stat_map["已通过视频"]["note"] == "当前启用的视频片段"
        assert stat_map["已完成任务"]["value"] == 0

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
        """累计审核、启用片段和完成任务统计正确，且排除已删除数据"""
        task_id_1 = uuid4().hex[:12]
        task_id_2 = uuid4().hex[:12]
        task_id_3 = uuid4().hex[:12]
        task_id_4 = uuid4().hex[:12]
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")

        _insert_test_task(task_id_1, "待检查", status="pending_review", created_at=today)
        _insert_test_task(task_id_2, "大写完成", status="COMPLETED", created_at=today)
        _insert_test_task(
            task_id_3,
            "部分完成",
            status="completed_with_errors",
            created_at=today,
        )
        _insert_test_task(
            task_id_4,
            "已删除任务",
            status="completed",
            is_deleted=1,
            created_at=today,
        )
        _insert_test_clip_candidate("clip_stats_1", task_id_1, "启用片段", enabled=1)
        _insert_test_clip_candidate("clip_stats_2", task_id_1, "未启用片段", enabled=0)
        _insert_test_clip_candidate("clip_stats_3", task_id_2, "完成片段", enabled=1)
        _insert_test_clip_candidate("clip_stats_4", task_id_4, "已删除任务片段", enabled=1)
        _insert_test_clip_candidate(
            "clip_stats_5",
            task_id_1,
            "已删除片段",
            enabled=1,
            is_deleted=1,
        )

        context = get_clips_overview_context()
        stat_map = {s["label"]: s["value"] for s in context["stats"]}
        task_map = {task["id"]: task for task in context["tasks"]}

        assert stat_map == {
            "累计审核任务": 2,
            "已通过视频": 2,
            "已完成任务": 2,
        }
        assert task_map[task_id_2]["review_stage"] == "已完成"


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
        expected_labels = {"输出切片记录", "待加字幕切片", "已加字幕成片", "可预览视频"}
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
            "storage_root", "storage_exists", "tasks_dir", "tasks_dir_exists",
            "upload_temp_dir", "upload_temp_dir_exists",
            "publish_export_dir", "publish_export_dir_exists",
            "database_path", "database_exists",
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


@pytest.mark.parametrize('filters,scope,rows,total,page', [
    ({'page':2}, list(range(38,13,-1)), list(range(38,13,-1)), 64, 2),
    ({'page':999}, list(range(13,-1,-1)), list(range(13,-1,-1)), 64, 3),
    ({'q':' e1873 ', 'platform':'douyin', 'stage':'error', 'sort':'created_asc'},
     list(range(40)), list(range(2,40,3)), 13, 1),
    ({'q':'COST-063', 'platform':'bilibili'}, [63], [63], 1, 1),
    ({'q':'no-matching-title', 'stage':'error'}, [], [], 0, 1),
])
def test_task_list_projects_only_the_required_scope(monkeypatch, filters, scope, rows, total, page):
    """Only matching page rows need projection; never format the legacy full list."""
    start = datetime(2026,10,8,tzinfo=timezone.utc)
    tasks = [dict(id=f'cost-{i:03d}', task_name=('E1873 素材' if i < 40 else '其他素材'),
                  platform='douyin' if i < 50 else 'bilibili', candidate_count=999,
                  created_at=(start + timedelta(minutes=i)).isoformat(),
                  updated_at=(start + timedelta(minutes=i)).isoformat()) for i in range(64)]
    calls, connections, statements = [], [], []
    get_connection_original = queries.get_connection

    @contextmanager
    def observed_connection():
        with get_connection_original() as connection:
            connections.append(connection)
            connection.set_trace_callback(statements.append)
            yield connection

    def project(*, connection, rows):
        assert connection is connections[0]
        ids = [row['id'] for row in rows]
        calls.append(ids)
        return {task_id:dict(stage='error' if int(task_id[-3:]) % 3 == 2 else 'waiting',
                            candidates=int(task_id[-3:])+1) for task_id in ids}

    monkeypatch.setattr(queries, 'get_connection', observed_connection)
    monkeypatch.setattr(queries, 'load_task_rows', lambda connection:[dict(task) for task in tasks])
    monkeypatch.setattr(queries, 'list_tasks', lambda:pytest.fail('Legacy full list formatter used'))
    monkeypatch.setattr(queries, 'task_projections', project)
    context = queries.get_tasks_page_context(**filters)
    expected_scope = [f'cost-{i:03d}' for i in scope]
    assert calls == ([expected_scope] if expected_scope else [])
    assert [task['id'] for task in context['tasks']] == [f'cost-{i:03d}' for i in rows]
    assert len(connections) == 1
    count_queries = [statement for statement in statements if 'COUNT(*) AS cnt FROM output_clip' in statement]
    assert len(count_queries) == (1 if rows else 0)
    if rows:
        assert all(f"'cost-{i:03d}'" in count_queries[0] for i in rows)
        assert all(f"'cost-{i:03d}'" not in count_queries[0] for i in range(64) if i not in rows)
    assert context['pagination'] == dict(page=page, pages=max(1,(total+24)//25), page_size=25, total=total)
    assert all(task['candidate_count'] == int(task['id'][-3:])+1 for task in context['tasks'])
    assert context['filters']['q'] == filters.get('q','').strip()
    assert context['stages'] == queries.STAGES


def test_task_list_light_rows_preserve_labels_dates_and_active_output_counts(monkeypatch):
    """Page-only formatting keeps public fields and active counts without filesystem probes."""
    from app.services import task_service

    created = '2026-10-08T02:31:42+00:00'
    _insert_test_task('light-title', 'E1873 测试素材', platform='douyin', created_at=created)
    _insert_test_task('light-fallback', '', platform='', created_at=created)
    _insert_test_task('light-deleted', 'E1873 已删除', is_deleted=1, created_at=created)
    for i, status in enumerate(('completed', 'pending', 'completed')):
        _insert_test_clip_candidate(f'light-candidate-{i}', 'light-title')
        _insert_test_output_clip(f'light-output-{i}', 'light-title', f'light-candidate-{i}', status=status)
    with get_connection() as connection:
        connection.execute("UPDATE output_clip SET is_active=0 WHERE id='light-output-2'")
        connection.commit()

    monkeypatch.setattr(queries, 'list_tasks', lambda:pytest.fail('Legacy full list used'))
    monkeypatch.setattr(task_service, '_row_to_task', lambda *args, **kwargs:pytest.fail('Legacy formatter used'))
    monkeypatch.setattr(task_service, 'count_output_clips', lambda *args:pytest.fail('Per-task count used'))
    monkeypatch.setattr(task_service, 'get_source_video_path', lambda *args:pytest.fail('Unused source probe'))
    monkeypatch.setattr(task_service, 'get_artifact_paths', lambda *args:pytest.fail('Unused artifact probe'))

    def project(*, connection, rows):
        return {row['id']:dict(stage='waiting', candidates=3) for row in rows}

    monkeypatch.setattr(queries, 'task_projections', project)
    context = queries.get_tasks_page_context(q=' E1873 ', platform='douyin')
    assert context['pagination']['total'] == 1
    task = context['tasks'][0]
    assert {key: task[key] for key in ('id', 'title', 'platform', 'platform_label')} == dict(
        id='light-title', title='E1873 测试素材', platform='douyin', platform_label='抖音')
    assert task['created_at'] == task_service._format_datetime(created)
    assert task['created_at_raw'] == created
    assert task['updated_at'] == task_service._format_datetime(created)
    assert task['candidate_count'] == 3
    assert task['output_clip_count'] == 2  # All current outputs, including pending; excludes retired versions.
    assert task['ui']['stage'] == 'waiting'
    fallback = queries.get_tasks_page_context(q='light-fallback', platform='general')['tasks'][0]
    assert fallback['title'] == '未命名任务'
    assert fallback['platform'] == 'general' and fallback['platform_label'] == '通用'
