"""Report-only Codex contract; no executable rule patches."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

REPORT_FORMAT = "manual_report_v1"
ReportText = Annotated[str, Field(min_length=1, max_length=8000)]


class ReviewSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: ReportText
    finding: ReportText
    action: ReportText
    expected_effect: ReportText
    insufficient: bool
    evidence_ids: list[str]
    primary_metric: Literal[
        "play_count",
        "five_second_completion_rate",
        "two_second_bounce_rate",
        "completion_rate",
        "watch_ratio",
    ]
    hypothesis: ReportText
    validation_needed: ReportText


class WeeklyReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: ReportText
    suggestions: Annotated[list[ReviewSuggestion], Field(min_length=3, max_length=3)]


REPORT_SCHEMA = WeeklyReviewReport.model_json_schema()
