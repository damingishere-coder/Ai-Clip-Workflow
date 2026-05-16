from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    waiting_video = "待提交视频"
    pending = "待处理"
    extracting_audio = "音频提取中"
    transcribing = "转写中"
    waiting_ai = "待 AI 分析"
    ai_analyzing = "AI 分析中"
    waiting_review = "待人工审核"
    cutting = "切割中"
    completed = "已完成"
    failed = "失败"


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    source_type: str = Field(default="upload")
    source_path: Optional[str] = None
    platform: str = Field(default="通用")
    max_clip_minutes: int = Field(default=2, ge=1, le=60)
    target_clip_count: int = Field(default=8, ge=1, le=50)
    ai_preference: Optional[str] = None


class TaskSummary(BaseModel):
    id: str
    title: str
    platform: str
    status: TaskStatus
    progress: int = Field(ge=0, le=100)
    max_clip_minutes: int
    target_clip_count: int


class ClipCandidate(BaseModel):
    id: str
    task_id: str
    title: str
    start_time: str
    end_time: str
    duration_seconds: int
    summary: str
    reason: str
    spread_value: str
    enabled: bool = True
