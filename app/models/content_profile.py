"""Immutable content policy data; importing this module performs no runtime work.

Profiles describe policy, not executable code. Analyzer dispatch and persistence
are deliberately outside this domain module. All nested collections are tuples
so a frozen task policy cannot be mutated through a nested list/dictionary.
"""

import hashlib
import json
import math
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PolicyModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class DurationPolicy(PolicyModel):
    strategy: Literal["task_limit", "sentence_bounds", "window_bounds"]
    min_seconds: int = Field(ge=1)
    max_seconds: int = Field(ge=1)
    recommended_min_seconds: int | None = Field(default=None, ge=1)
    recommended_max_seconds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def ordered_bounds(self) -> Self:
        if self.min_seconds > self.max_seconds:
            raise ValueError("时长下限不能大于上限")
        lo, hi = self.recommended_min_seconds, self.recommended_max_seconds
        if (lo is None) != (hi is None):
            raise ValueError("推荐时长必须同时提供上下限")
        if lo is not None and not self.min_seconds <= lo <= hi <= self.max_seconds:
            raise ValueError("推荐时长必须位于硬边界内")
        return self


class WindowPolicy(PolicyModel):
    provider: Literal["local", "non_local", "all"]
    seconds: int = Field(ge=1)
    overlap_seconds: int = Field(default=0, ge=0)
    char_budget: int = Field(ge=1)
    recall_limit: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def advancing_window(self) -> Self:
        if self.overlap_seconds >= self.seconds:
            raise ValueError("窗口重叠必须小于窗口长度")
        return self


class RecallPolicy(PolicyModel):
    strategy: Literal["general_chunks", "comedy_windows", "long_live_windows", "content_windows"]
    windows: tuple[WindowPolicy, ...] = Field(min_length=1)
    preliminary_limit: int | None = Field(default=None, ge=1)


class ExpansionPolicy(PolicyModel):
    strategy: Literal["none", "comedy_context", "content_context"]
    before_seconds: int = Field(default=0, ge=0)
    after_seconds: int = Field(default=0, ge=0)
    remote_batch_size: int = Field(default=1, ge=1)
    local_batch_size: int = Field(default=1, ge=1)


class ScoreDimension(PolicyModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    weight: float = Field(ge=0, le=1)


class HardGate(PolicyModel):
    dimension: str
    minimum: float = Field(ge=0, le=100)


class ScoringPolicy(PolicyModel):
    strategy: Literal["confidence", "comedy_weighted", "long_live_score", "content_weighted"]
    dimensions: tuple[ScoreDimension, ...] = Field(min_length=1)
    hard_gates: tuple[HardGate, ...] = ()
    a_threshold: float | None = Field(default=None, ge=0, le=100)
    b_threshold: float | None = Field(default=None, ge=0, le=100)
    audio_weight: float = Field(default=0, ge=0, le=1)
    visual_weight: float = Field(default=0, ge=0, le=1)
    signal_strategy: Literal["none", "bonus_only"] = "none"

    @model_validator(mode="after")
    def coherent_scoring(self) -> Self:
        ids = [dimension.id for dimension in self.dimensions]
        if len(set(ids)) != len(ids):
            raise ValueError("评分维度不能重复")
        if not math.isclose(sum(d.weight for d in self.dimensions), 1, abs_tol=1e-9):
            raise ValueError("文字评分权重之和必须为 1")
        if any(gate.dimension not in ids for gate in self.hard_gates):
            raise ValueError("hard gate 必须引用已定义的评分维度")
        if (self.a_threshold is None) != (self.b_threshold is None):
            raise ValueError("等级阈值必须成对提供")
        if self.a_threshold is not None and self.a_threshold < self.b_threshold:
            raise ValueError("A 级阈值不能低于 B 级")
        if self.audio_weight + self.visual_weight > 1:
            raise ValueError("辅助信号权重之和不能超过 1")
        if self.signal_strategy == "none" and (self.audio_weight or self.visual_weight):
            raise ValueError("辅助信号加权需要明确的组合策略")
        return self


class DedupePolicy(PolicyModel):
    strategy: Literal["overlap", "comedy_topic", "long_live_semantic", "content_topic"]
    recall_distance_seconds: int = Field(default=0, ge=0)
    topic_distance_seconds: int = Field(default=0, ge=0)
    topic_gap_seconds: int = Field(default=0, ge=0)
    expansion_overlap: float = Field(default=0.5, gt=0, le=1)
    final_overlap: float = Field(default=0.5, gt=0, le=1)
    semantic_threshold: float | None = Field(default=None, ge=0, le=1)


class SelectionPolicy(PolicyModel):
    strategy: Literal["default_selected", "a_only", "hourly_balanced"]
    candidate_pool_default: int = Field(ge=1, le=50)
    candidate_pool_max: int = Field(ge=1, le=50)
    final_target_default: int = Field(ge=1, le=50)
    final_target_max: int = Field(ge=1, le=50)
    density_per_hour: int | None = Field(default=None, ge=1, le=10)
    allow_fewer: bool = True

    @model_validator(mode="after")
    def valid_targets(self) -> Self:
        if self.candidate_pool_default > self.candidate_pool_max or self.final_target_default > self.final_target_max:
            raise ValueError("默认数量不能超过对应上限")
        return self


class ContentProfile(PolicyModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1)
    recommended_scenes: tuple[str, ...] = Field(min_length=1)
    rules_version: str = Field(min_length=1)
    analyzer_key: Literal["general", "variety_comedy", "long_live_talk", "content"]
    duration: DurationPolicy
    recall: RecallPolicy
    expansion: ExpansionPolicy
    scoring: ScoringPolicy
    dedupe: DedupePolicy
    selection: SelectionPolicy
    title_strategy: str = Field(min_length=1)
    prompt_preset_id: str = Field(min_length=1)
    prompt_template_key: str = Field(min_length=1)
    selection_rules: tuple[str, ...] = Field(min_length=1)

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
