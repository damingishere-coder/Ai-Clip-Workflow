"""Explicit local folder selection and immutable preview contracts."""
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MaterialScan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directory: str = Field(min_length=1, max_length=2048)


class MaterialRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scan_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_keys: list[str] = Field(min_length=1, max_length=200)
    request_key: UUID
    confirmed: bool = False

    @model_validator(mode="after")
    def validate_selection(self):
        if len(set(self.source_keys)) != len(self.source_keys):
            raise ValueError("同一素材不能重复选择")
        if any(len(key) != 64 or any(c not in "0123456789abcdef" for c in key) for key in self.source_keys):
            raise ValueError("素材编号无效")
        return self
