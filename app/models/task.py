from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, validator


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    PREPARING_SOURCE = "PREPARING_SOURCE"
    TRANSCRIBING = "TRANSCRIBING"
    AI_ANALYZING = "AI_ANALYZING"
    CLIP_SELECTING = "CLIP_SELECTING"
    VIDEO_CUTTING = "VIDEO_CUTTING"
    SUBTITLE_DRAFTING = "SUBTITLE_DRAFTING"
    PENDING_SUBTITLE_REVIEW = "PENDING_SUBTITLE_REVIEW"
    METADATA_GENERATING = "METADATA_GENERATING"
    SCHEDULE_CREATING = "SCHEDULE_CREATING"
    PUBLISH_JOB_CREATING = "PUBLISH_JOB_CREATING"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED_PREPARING_SOURCE = "FAILED_PREPARING_SOURCE"
    FAILED_TRANSCRIBING = "FAILED_TRANSCRIBING"
    FAILED_AI_ANALYZING = "FAILED_AI_ANALYZING"
    FAILED_CLIP_SELECTING = "FAILED_CLIP_SELECTING"
    FAILED_VIDEO_CUTTING = "FAILED_VIDEO_CUTTING"
    FAILED_SUBTITLE_DRAFTING = "FAILED_SUBTITLE_DRAFTING"
    FAILED_METADATA_GENERATING = "FAILED_METADATA_GENERATING"
    FAILED_SCHEDULE_CREATING = "FAILED_SCHEDULE_CREATING"
    FAILED_PUBLISH_JOB_CREATING = "FAILED_PUBLISH_JOB_CREATING"
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
    max_clip_duration: int = Field(default=10, ge=1, le=60)
    candidate_clip_count: int = Field(default=12, ge=1, le=50)
    selection_profile: Literal["general", "variety_comedy", "long_live_talk"]
    final_clip_target: int = Field(default=5, ge=1, le=12)
    highlight_density_per_hour: int = Field(default=4, ge=1, le=10)
    highlight_total_limit: int = Field(default=30, ge=1, le=50)
    ai_preference: Optional[str] = None
    auto_mode: bool = False
    auto_clip_count: str = Field(default="auto", max_length=10)
    auto_min_clip_seconds: int = Field(default=15, ge=1, le=3600)
    auto_max_clip_seconds: int = Field(default=300, ge=1, le=7200)
    auto_schedule_mode: Literal["default", "immediate", "interval", "daily_window"] = "default"
    auto_schedule_start_at: Optional[str] = Field(default="", max_length=80)
    auto_schedule_interval_hours: int = Field(default=3, ge=1, le=168)
    auto_schedule_daily_start_time: str = Field(default="07:00", max_length=5)
    auto_schedule_daily_end_time: str = Field(default="00:00", max_length=5)
    auto_metadata_use_ai: bool = False

    @validator("auto_clip_count")
    def validate_auto_clip_count(cls, value: str) -> str:
        text = str(value or "auto").strip().lower()
        if text == "auto":
            return text
        if not text.isdigit():
            raise ValueError("自动切片数量必须是 auto 或数字")
        number = int(text)
        if number not in {5, 10, 15} and not 1 <= number <= 50:
            raise ValueError("自动切片数量必须是 auto、5、10、15 或 1-50 的数字")
        return text


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
    candidate_clip_count: int = Field(default=12, ge=1, le=50)


class TaskSelectionSettingsUpdate(BaseModel):
    selection_profile: Literal["general", "variety_comedy", "long_live_talk"]
    final_clip_target: int = Field(default=5, ge=1, le=12)
    highlight_density_per_hour: int = Field(default=4, ge=1, le=10)
    highlight_total_limit: int = Field(default=30, ge=1, le=50)


class ClipFeedbackCreate(BaseModel):
    decision: Literal["keep", "reject"]
    reason_code: Literal[
        "worth_publishing",
        "not_funny",
        "fragmented",
        "missing_setup",
        "duplicate",
        "dragging",
        "other",
    ]
    note: Optional[str] = Field(default="", max_length=500)


class SubtitleStyleUpdate(BaseModel):
    font_family: str = Field(default="Microsoft YaHei", min_length=1, max_length=120)
    font_size: int = Field(default=42, ge=20, le=88)
    position: Literal["bottom_center", "middle_lower", "top_center"] = "bottom_center"
    font_color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    stroke_color: str = Field(default="#111827", pattern=r"^#[0-9a-fA-F]{6}$")
    shadow_enabled: bool = True
    outline_width: float = Field(default=3, ge=0, le=20)
    shadow_depth: float = Field(default=1, ge=0, le=20)
    safe_area_percent: float = Field(default=5, ge=0, le=25)
    speaker_styles: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PublishPlatformConfigUpdate(BaseModel):
    app_name: str = Field(default="", max_length=120)
    client_key: str = Field(default="", max_length=300)
    client_secret: str = Field(default="", max_length=500)
    redirect_uri: str = Field(default="", max_length=1000)
    scope: str = Field(default="", max_length=1000)
    api_base_url: str = Field(default="", max_length=1000)
    auth_url: str = Field(default="", max_length=1000)
    token_url: str = Field(default="", max_length=1000)
    refresh_url: str = Field(default="", max_length=1000)
    upload_url: str = Field(default="", max_length=1000)
    create_url: str = Field(default="", max_length=1000)
    extra_config: Optional[str] = Field(default="", max_length=4000)


class PublishAccountCreate(BaseModel):
    platform: Literal["douyin", "bilibili"]
    account_name: str = Field(..., min_length=1, max_length=120)
    account_uid: Optional[str] = Field(default="", max_length=200)
    open_id: Optional[str] = Field(default="", max_length=300)
    access_token: Optional[str] = Field(default="", max_length=2000)
    refresh_token: Optional[str] = Field(default="", max_length=2000)
    token_expires_at: Optional[str] = Field(default="", max_length=80)
    refresh_expires_at: Optional[str] = Field(default="", max_length=80)
    scopes: Optional[str] = Field(default="", max_length=1000)
    remark: Optional[str] = Field(default="", max_length=1000)


class PublishJobCreate(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=80)
    output_clip_id: str = Field(..., min_length=1, max_length=80)
    platform: Literal["douyin", "bilibili"]
    account_id: Optional[str] = Field(default="", max_length=80)
    publish_mode: Literal["manual_export", "local_browser", "api_publish", "opencli_publish"] = "local_browser"
    video_source: Literal["original", "subtitled"] = "original"
    title: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(default="", max_length=2000)
    tags: Optional[str] = Field(default="", max_length=500)
    visibility: Literal["public", "friends", "private"] = "public"
    cover_mode: Literal["auto", "time"] = "auto"
    cover_time_seconds: float = Field(default=0, ge=0)
    cover_file_path: Optional[str] = Field(default="", max_length=1000)
    allow_download: bool = True
    bilibili_tid: Optional[str] = Field(default="", max_length=80)
    bilibili_copyright: Literal["original", "repost"] = "original"
    bilibili_source: Optional[str] = Field(default="", max_length=300)
    scheduled_at: Optional[str] = Field(default="", max_length=80)


class PublishJobScheduleUpdate(BaseModel):
    scheduled_at: str = Field(..., min_length=1, max_length=80)


class PublishBatchScheduleUpdate(BaseModel):
    job_ids: list[str] = Field(default_factory=list)
    platform: Optional[Literal["douyin", "bilibili"]] = None
    action: Literal["apply", "clear"] = "apply"
    start_at_local: Optional[str] = Field(default="", max_length=80)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)
    interval_minutes: int = Field(default=180, ge=1, le=10080)
    daily_start_time: str = Field(default="07:00", min_length=5, max_length=5)
    daily_end_time: str = Field(default="00:00", min_length=5, max_length=5)
    confirmed_schedule: list[dict[str, str]] = Field(default_factory=list)

    @validator("job_ids")
    def validate_schedule_job_ids(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
        if not normalized:
            raise ValueError("至少选择一条发布任务")
        return normalized


class PublishScheduleNextStartRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)
    platform: Literal["douyin", "bilibili"]
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)
    interval_minutes: int = Field(default=180, ge=1, le=10080)
    daily_start_time: str = Field(default="07:00", min_length=5, max_length=5)
    daily_end_time: str = Field(default="00:00", min_length=5, max_length=5)

    @validator("job_ids")
    def validate_next_start_job_ids(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
        if not normalized:
            raise ValueError("至少选择一条发布任务")
        return normalized


class PublishJobContentUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    caption: str = Field(..., min_length=1, max_length=2000)
    hashtags: Optional[str] = Field(default="", max_length=500)
    cover_text: Optional[str] = Field(default="", max_length=500)
    scheduled_at: Optional[str] = Field(default="", max_length=80)


class PublishBatchJobCreate(BaseModel):
    output_clip_ids: list[str] = Field(default_factory=list)
    platform: Literal["douyin", "bilibili"]
    account_id: Optional[str] = Field(default="", max_length=80)
    publish_mode: Literal["manual_export", "local_browser", "api_publish", "opencli_publish"] = "local_browser"
    video_source: Literal["original", "subtitled"] = "original"
    title_prefix: Optional[str] = Field(default="", max_length=80)
    description: Optional[str] = Field(default="", max_length=2000)
    tags: Optional[str] = Field(default="", max_length=500)

    @validator("output_clip_ids")
    def validate_output_clip_ids(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("至少选择一条切片")
        return value


class PublishCoverCreate(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=80)
    output_clip_id: str = Field(..., min_length=1, max_length=80)
    video_source: Literal["original", "subtitled"] = "original"
    title: str = Field(..., min_length=1, max_length=120)
    cover_time_seconds: float = Field(default=0, ge=0)


class PublishSendJobUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(default="", max_length=2000)
    tags: Optional[str] = Field(default="", max_length=500)
    visibility: Literal["public", "friends", "private"] = "public"
    cover_file_path: Optional[str] = Field(default="", max_length=1000)
    cover_time_seconds: float = Field(default=0, ge=0)
    allow_download: bool = True
    bilibili_tid: Optional[str] = Field(default="娱乐", max_length=80)
    bilibili_copyright: Literal["original", "repost"] = "original"
    bilibili_source: Optional[str] = Field(default="", max_length=300)


class PublishRetryRequest(BaseModel):
    scheduled_at: Optional[str] = Field(default="", max_length=80)
    visibility: Optional[Literal["public", "friends", "private"]] = None


class PublishHistoryRecordBatchUpdate(BaseModel):
    platform: Literal["douyin", "bilibili"]
    job_ids: list[str] = Field(default_factory=list)

    @validator("job_ids")
    def validate_history_job_ids(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
        if not normalized:
            raise ValueError("至少选择一条执行记录")
        if len(normalized) > 100:
            raise ValueError("每次最多处理 100 条执行记录")
        return normalized


class PublishMarkPublishedRequest(BaseModel):
    platform_url: str = Field(..., min_length=8, max_length=2000)


class PublishJobTargetUpdate(BaseModel):
    platform: Literal["douyin", "bilibili"]
    account_id: Optional[str] = Field(default="", max_length=80)
    publish_mode: Literal["manual_export", "local_browser"] = "local_browser"


class PublishBatchTargetUpdate(PublishJobTargetUpdate):
    job_ids: list[str] = Field(default_factory=list)

    @validator("job_ids")
    def validate_target_job_ids(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
        if not normalized:
            raise ValueError("至少选择一条发布任务")
        return normalized


class PublishCoverFrameBatchCreate(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=80)
    output_clip_id: str = Field(..., min_length=1, max_length=80)
    video_source: Literal["original", "subtitled"] = "original"
    title: str = Field(default="直播切片", min_length=1, max_length=120)
    frame_count: int = Field(default=4, ge=1, le=8)


class ClipCandidate(BaseModel):
    id: str
    task_id: str
    clip_key: str = ""
    title: str
    start_time: str
    end_time: str
    duration_seconds: int
    cover_time_seconds: Optional[float] = Field(default=None, ge=0)
    summary: str = ""
    highlight_reason: str = ""
    spread_value: str = ""
    suggested_editing: str = ""
    confidence_score: float = Field(default=0, ge=0, le=1)
    selected_by_default: bool = True
    enabled: bool = True
    reviewed: bool = False
    is_deleted: bool = False
    deleted_at: Optional[str] = None


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
    cover_time_seconds: float = Field(..., ge=0)
    summary: str = Field(..., min_length=1, max_length=1000)
    highlight_reason: str = Field(..., min_length=1, max_length=1000)
    spread_value: str = Field(..., min_length=1, max_length=40)
    suggested_editing: str = Field(..., min_length=1, max_length=1000)
    confidence_score: float = Field(..., ge=0, le=1)
    selected_by_default: bool = True
    quality_tier: str = Field(default="", max_length=8)
    quality_score: float = Field(default=0, ge=0, le=100)
    text_quality_score: float = Field(default=0, ge=0, le=100)
    humor_score: float = Field(default=0, ge=0, le=100)
    completeness_score: float = Field(default=0, ge=0, le=100)
    audio_reaction_score: float = Field(default=0, ge=0, le=100)
    topic_key: str = Field(default="", max_length=120)
    key_moment_time: str = Field(default="", max_length=16)
    quality_evidence: dict[str, Any] = Field(default_factory=dict)
    rejection_reason: str = Field(default="", max_length=1000)

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
