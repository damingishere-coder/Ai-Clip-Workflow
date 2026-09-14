from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ObservationDecision(BaseModel):
    observation_key: str = Field(min_length=1, max_length=200)
    observation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["keep", "reject"]
    reason_code: Literal["worth_publishing", "not_funny", "fragmented", "missing_setup",
                         "duplicate", "dragging", "low_value", "weak_evidence", "other"]
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def consistent_reason(self):
        if (self.decision == "keep") != (self.reason_code == "worth_publishing"):
            raise ValueError("接受请选择值得发，拒绝请选择具体拒绝原因")
        return self
