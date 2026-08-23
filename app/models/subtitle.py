from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class SubtitleCueInput(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=4000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    speaker: str = Field(default="", max_length=80)
    source_cue_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("字幕结束时间必须晚于开始时间")
        return self


class SubtitleRevisionCreate(BaseModel):
    base_revision_id: str | None = Field(default=None, max_length=64)
    cues: list[SubtitleCueInput] = Field(default_factory=list, max_length=20000)
    note: str = Field(default="", max_length=500)


class SubtitleOperation(BaseModel):
    type: Literal["update", "split", "merge", "add", "delete", "shift", "replace"]
    cue_id: str | None = Field(default=None, max_length=64)
    cue_ids: list[str] = Field(default_factory=list, max_length=20000)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    split_ms: int | None = Field(default=None, gt=0)
    text: str | None = Field(default=None, max_length=4000)
    second_text: str | None = Field(default=None, max_length=4000)
    speaker: str | None = Field(default=None, max_length=80)
    confidence: float | None = Field(default=None, ge=0, le=1)
    delta_ms: int | None = Field(default=None, ge=-86_400_000, le=86_400_000)
    search: str | None = Field(default=None, max_length=500)
    replacement: str | None = Field(default=None, max_length=500)
    cue: SubtitleCueInput | None = None


class SubtitleOperationsRequest(BaseModel):
    base_revision_id: str = Field(min_length=1, max_length=64)
    operations: list[SubtitleOperation] = Field(min_length=1, max_length=1000)
    note: str = Field(default="", max_length=500)


class SubtitleApproveRequest(BaseModel):
    revision_id: str = Field(min_length=1, max_length=64)


class SubtitleSyncRequest(BaseModel):
    force: bool = False


class SubtitleAIRevisionRequest(BaseModel):
    revision_id: str = Field(min_length=1, max_length=64)
    cue_ids: list[str] = Field(default_factory=list, max_length=500)
    instructions: str = Field(default="", max_length=2000)


class SubtitleStyleExtendedUpdate(BaseModel):
    font_family: str = Field(default="Microsoft YaHei", min_length=1, max_length=120)
    font_size: int = Field(default=42, ge=12, le=160)
    position: Literal["bottom_center", "middle_lower", "top_center"] = "bottom_center"
    font_color: str = Field(default="#ffffff", pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke_color: str = Field(default="#111827", pattern=r"^#[0-9A-Fa-f]{6}$")
    shadow_enabled: bool = True
    outline_width: float = Field(default=3, ge=0, le=20)
    shadow_depth: float = Field(default=1, ge=0, le=20)
    safe_area_percent: float = Field(default=5, ge=0, le=25)
    speaker_styles: dict[str, dict[str, Any]] = Field(default_factory=dict)
