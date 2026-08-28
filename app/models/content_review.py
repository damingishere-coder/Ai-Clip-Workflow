from pydantic import BaseModel, Field


class ContentMetricImportCommitRequest(BaseModel):
    confirm: bool = True


class DouyinAnalyticsSyncPreviewRequest(BaseModel):
    account_id: str = Field(default="", max_length=120)
    limit: int = Field(default=50, ge=1, le=50)


class ContentItemMatchUpdate(BaseModel):
    publish_job_id: str = Field(..., min_length=1, max_length=120)
