from datetime import datetime, timezone
from uuid import uuid4

from app.db.database import get_connection
from app.models.task import TaskCreate, TaskStatus
from app.services.ai_clip_service import generate_candidate_clips_placeholder
from app.services.storage_service import create_task_directory, get_expected_subdirectories


WORKFLOW_STEPS = [
    "视频提交",
    "音频提取",
    "转写",
    "AI 分析",
    "人工审核",
    "自动切割",
    "输出完成",
]


MOCK_TASKS = [
    {
        "id": "demo-001",
        "title": "5 月 16 日直播复盘",
        "platform": "抖音",
        "duration": "02:18:34",
        "status": TaskStatus.waiting_review.value,
        "progress": 72,
        "candidate_count": 12,
        "created_at": "2026-05-16 10:20",
    },
    {
        "id": "demo-002",
        "title": "B站产品分享直播",
        "platform": "B站",
        "duration": "01:06:18",
        "status": TaskStatus.ai_analyzing.value,
        "progress": 58,
        "candidate_count": 0,
        "created_at": "2026-05-15 21:42",
    },
    {
        "id": "demo-003",
        "title": "本地素材测试任务",
        "platform": "通用",
        "duration": "00:46:11",
        "status": TaskStatus.completed.value,
        "progress": 100,
        "candidate_count": 6,
        "created_at": "2026-05-14 17:05",
    },
]


def get_workflow_steps() -> list[str]:
    return WORKFLOW_STEPS


def list_mock_tasks() -> list[dict]:
    return MOCK_TASKS


def get_mock_task(task_id: str) -> dict:
    for task in MOCK_TASKS:
        if task["id"] == task_id:
            return task
    return MOCK_TASKS[0] | {"id": task_id}


def list_mock_clips(task_id: str) -> list[dict]:
    return [clip.model_dump() for clip in generate_candidate_clips_placeholder(task_id)]


def get_dashboard_context() -> dict:
    return {
        "stats": [
            {"label": "今日新增任务", "value": 1, "tone": "blue"},
            {"label": "待处理", "value": 2, "tone": "amber"},
            {"label": "待审核", "value": 1, "tone": "purple"},
            {"label": "已完成", "value": 4, "tone": "green"},
            {"label": "失败任务", "value": 0, "tone": "red"},
        ],
        "workflow_steps": WORKFLOW_STEPS,
        "recent_tasks": MOCK_TASKS,
    }


def create_task_record(payload: TaskCreate) -> dict:
    task_id = uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    create_task_directory(task_id)
    for directory in get_expected_subdirectories(task_id).values():
        directory.mkdir(parents=True, exist_ok=True)

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, title, source_type, source_path, platform, status, progress,
                max_clip_minutes, target_clip_count, ai_preference, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                payload.title,
                payload.source_type,
                payload.source_path,
                payload.platform,
                TaskStatus.pending.value,
                0,
                payload.max_clip_minutes,
                payload.target_clip_count,
                payload.ai_preference,
                now,
                now,
            ),
        )
        connection.commit()

    return {
        "id": task_id,
        "title": payload.title,
        "status": TaskStatus.pending.value,
        "message": "任务已创建，后续会接入真实处理队列。",
    }
