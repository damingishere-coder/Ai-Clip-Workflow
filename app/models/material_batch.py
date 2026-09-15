from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.task import TaskCreate


class BatchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selection_profile: Literal["general", "variety_comedy", "long_live_talk", "interview_story", "knowledge_opinion"]
    ai_prompt_preset_id: str | None = Field(default=None, min_length=1, max_length=80)
    ai_provider: Literal["codex", "remote", "local"] | None = None
    candidate_clip_count: int = Field(default=12, ge=1, le=50)
    final_clip_target: int = Field(default=5, ge=1, le=12)
    max_clip_duration: int = Field(default=10, ge=1, le=60)
    highlight_density_per_hour: int = Field(default=4, ge=1, le=10)
    highlight_total_limit: int = Field(default=30, ge=1, le=50)
    visual_enabled: bool = False
    subtitle_strategy: Literal["original", "review"] = "original"
    auto_production: bool = False

    def task_payload(self, name: str) -> TaskCreate:
        return TaskCreate(task_name=name[:120], auto_mode=self.auto_production, **self.model_dump(exclude={"auto_production"}))


class MaterialBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material_ids: list[str] = Field(min_length=1, max_length=200)
    settings: BatchSettings
    request_key: UUID
    confirmed: bool = False
    create_new_production: bool = False

    @model_validator(mode="after")
    def validate_ids(self):
        if len(set(self.material_ids)) != len(self.material_ids):
            raise ValueError("素材选择重复")
        if any(len(i) != 32 or any(c not in "0123456789abcdef" for c in i) for i in self.material_ids):
            raise ValueError("素材编号无效")
        return self
