from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ContentMetricImportCommitRequest(BaseModel):
    confirm: bool = True


class ContentIntelligenceReportRequest(BaseModel):
    account_id: str = Field(default="", max_length=120)
    days: int = Field(default=30, ge=1, le=180)
    request_key: UUID


class DouyinAnalyticsExportSyncRequest(BaseModel):
    account_id: str = Field(default="", max_length=120)


class ContentItemMatchUpdate(BaseModel):
    publish_job_id: str = Field(..., min_length=1, max_length=120)


class ContentExperimentCreateRequest(BaseModel):
    account_id: str = Field(default="", max_length=120)
    recommendation_id: str = Field(..., min_length=1, max_length=80)


class ContentExperimentDecisionRequest(BaseModel):
    decision: Literal["keep", "revert", "inconclusive", "cancel"]


class ContentExperimentAssignmentRequest(BaseModel):
    experiment_id: str = Field(default="", max_length=120)
