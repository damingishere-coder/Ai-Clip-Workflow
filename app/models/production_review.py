from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ProductionReviewConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    delivery_mode: Literal["original", "subtitled"]
    confirmed: bool = False
