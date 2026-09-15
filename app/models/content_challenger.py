"""Explicit human-authored alternatives; no automatic prompt editing."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ChallengerDraftCreate(BaseModel):
    report_id: str = Field(min_length=1, max_length=120)
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_id: Literal["variety_comedy", "interview_story", "knowledge_opinion", "long_live_talk", "general"]
    champion_preset_id: str = Field(min_length=1, max_length=80)
    baseline_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    name: str = Field(min_length=1, max_length=80)
    hypothesis: str = Field(min_length=1, max_length=1000)
    prompt_text: str = Field(min_length=1, max_length=30000)
    request_key: UUID


class ChallengerExperimentCreate(BaseModel):
    report_id: str = Field(min_length=1, max_length=120)
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    challenger_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cohort_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm: bool = False


class PolicyDecision(BaseModel):
    action: Literal["activate", "rollback"]
    preview_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm: bool = False
    request_key: UUID
