from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, validator


class TaskStatus(str, Enum):
    pending_video = "pending_video"
    pending_processing = "pending_processing"
    audio_extracting = "audio_extracting"
    transcribing = "transcribing"
    pending_ai = "pending_ai"
    ai_analyzing = "ai_analyzing"
    pending_review = "pending_review"
    cutting = "cutting"
    completed = "completed"
    completed_with_errors = "completed_with_errors"
    failed = "failed"


class TaskCreate(BaseModel):
    task_name: str = Field(..., min_length=1, max_length=120)
    source_type: Literal["upload", "nas"] = "upload"
    platform: Literal["douyin", "bilibili", "general"] = "general"
    original_video_path: Optional[str] = None
    nas_file_path: Optional[str] = None
    max_clip_duration: int = Field(default=2, ge=1, le=60)
    candidate_clip_count: int = Field(default=5, ge=1, le=50)
    ai_preference: Optional[str] = None


class TaskSummary(BaseModel):
    id: str
    task_name: str
    platform: str
    status: TaskStatus
    progress: int = Field(ge=0, le=100)
    max_clip_duration: int
    candidate_clip_count: int
    created_at: str
    updated_at: str


class TaskStatusUpdate(BaseModel):
    status: TaskStatus
    error_message: Optional[str] = None


class TaskAIPreferenceUpdate(BaseModel):
    ai_preference: Optional[str] = Field(default=None, max_length=1000)


class AIPromptPresetUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    prompt_text: str = Field(default="", max_length=30000)


class TaskAIPromptPresetUpdate(BaseModel):
    ai_prompt_preset_id: str = Field(..., min_length=1, max_length=80)


class TaskCandidateClipCountUpdate(BaseModel):
    candidate_clip_count: int = Field(default=5, ge=1, le=50)


class SubtitleStyleUpdate(BaseModel):
    font_family: str = Field(default="Microsoft YaHei", min_length=1, max_length=120)
    font_size: int = Field(default=42, ge=20, le=88)
    position: Literal["bottom_center", "middle_lower", "top_center"] = "bottom_center"
    font_color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    stroke_color: str = Field(default="#111827", pattern=r"^#[0-9a-fA-F]{6}$")
    shadow_enabled: bool = True


class PublishJobCreate(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=80)
    output_clip_id: str = Field(..., min_length=1, max_length=80)
    video_source: Literal["original", "subtitled"] = "original"
    platforms: list[Literal["douyin", "bilibili"]] = Field(default_factory=list)
    title: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(default="", max_length=2000)
    tags: Optional[str] = Field(default="", max_length=500)

    @validator("platforms")
    def validate_platforms(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("至少选择一个发布平台")
        return value


class PublishJobStatusUpdate(BaseModel):
    status: Literal["draft", "ready", "publishing", "published", "failed", "cancelled"]
    error_message: Optional[str] = Field(default=None, max_length=1000)


class ClipCandidate(BaseModel):
    id: str
    task_id: str
    clip_key: str = ""
    title: str
    start_time: str
    end_time: str
    duration_seconds: int
    summary: str = ""
    highlight_reason: str = ""
    spread_value: str = ""
    suggested_editing: str = ""
    confidence_score: float = Field(default=0, ge=0, le=1)
    selected_by_default: bool = True
    enabled: bool = True
    reviewed: bool = False


class ClipCandidateUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=160)
    start_time: str = Field(..., min_length=1, max_length=16)
    end_time: str = Field(..., min_length=1, max_length=16)
    enabled: bool = True
    summary: Optional[str] = Field(default=None, max_length=1000)


class ClipCandidateBatchItem(ClipCandidateUpdate):
    id: str = Field(..., min_length=1)


class ClipCandidateBatchUpdate(BaseModel):
    clips: list[ClipCandidateBatchItem] = Field(default_factory=list)


class AIClipItem(BaseModel):
    clip_id: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=160)
    start_time: str = Field(..., min_length=1, max_length=16)
    end_time: str = Field(..., min_length=1, max_length=16)
    duration_seconds: int = Field(..., ge=1)
    summary: str = Field(..., min_length=1, max_length=1000)
    highlight_reason: str = Field(..., min_length=1, max_length=1000)
    spread_value: str = Field(..., min_length=1, max_length=40)
    suggested_editing: str = Field(..., min_length=1, max_length=1000)
    confidence_score: float = Field(..., ge=0, le=1)
    selected_by_default: bool = True

    @validator("start_time", "end_time")
    def validate_time_text(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) not in {2, 3}:
            raise ValueError("时间必须使用 MM:SS 或 HH:MM:SS 格式")
        if not all(part.isdigit() for part in parts):
            raise ValueError("时间只能包含数字和冒号")
        return value


class AIClipAnalysisResult(BaseModel):
    task_id: str | int = ""
    analysis_summary: str = ""
    clips: list[AIClipItem] = Field(default_factory=list)
