from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import re

import pytest

from app.db.database import get_connection, init_db
from app.services import job_service
from app.services.ai.ai_clip_analyzer import TranscriptRow
from app.services.ai.base import AIProviderError
from app.services.ai.long_live_talk_analyzer import (
    LongLiveAnalysisRequest,
    LongLiveWindow,
    _get_or_create_checkpoint,
    _mark_checkpoint_running,
    analyze_long_live_talk,
    build_long_live_windows,
    calculate_window_coverage,
    deduplicate_long_live_moments,
    list_long_live_window_checkpoints,
    select_temporally_balanced_highlights,
)
from app.services.pipeline_engine import PipelineEngine
from app.services.storage_service import get_artifact_paths


PREFIX = "test-long-selection-"


@pytest.fixture(autouse=True)
def cleanup_long_live_selection_rows():
    init_db()
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM workflow_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM ai_analysis_windows WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM ai_analysis_runs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _time_text(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, value = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{value:02d}"


def _rows_for_duration(duration_seconds: int, step_seconds: int = 30) -> list[TranscriptRow]:
    rows = []
    for start in range(0, duration_seconds, step_seconds):
        end = min(duration_seconds, start + step_seconds)
        rows.append(
            TranscriptRow(
                start_time=_time_text(start),
                end_time=_time_text(end),
                start_seconds=start,
                end_seconds=end,
                text=f"第 {start // step_seconds + 1} 句结构化直播转写",
            )
        )
    return rows


def _write_transcript(task_id: str, duration_seconds: int) -> None:
    path = get_artifact_paths(task_id)["transcript_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["| 开始 | 结束 | 文本 |", "| --- | --- | --- |"]
    lines.extend(
        f"| {row.start_time} | {row.end_time} | {row.text} |"
        for row in _rows_for_duration(duration_seconds)
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _create_task(task_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, source_type, platform, selection_profile,
                highlight_density_per_hour, highlight_total_limit,
                status, progress, is_deleted, created_at, updated_at
            ) VALUES (?, ?, ?, 'upload', 'general', 'long_live_talk', 4, 30,
                      'pending_processing', 0, 0, ?, ?)
            """,
            (task_id, task_id, task_id, now, now),
        )
        connection.commit()


def _moment(start: int, *, score: float = 80, title: str = "观点") -> dict:
    return {
        "title": title,
        "start_seconds": start,
        "end_seconds": start + 60,
        "key_seconds": start + 30,
        "summary": f"{title}完整内容",
        "highlight_reason": "有明确内容价值",
        "suggested_editing": "保留完整表达",
        "category": "quote_opinion",
        "topic_key": title,
        "score": score,
        "source_window_indexes": [],
    }


class FakeWindowProvider:
    def __init__(self, failed_indexes: set[int] | None = None):
        self.failed_indexes = failed_indexes or set()
        self.calls: Counter[int] = Counter()

    def generate_json(self, prompt: str, retry_instruction: str | None = None) -> str:
        del retry_instruction
        match = re.search(r"窗口 (\d+)/(\d+)，范围 (\d{2}:\d{2}:\d{2})-(\d{2}:\d{2}:\d{2})", prompt)
        assert match
        index = int(match.group(1))
        self.calls[index] += 1
        if index in self.failed_indexes:
            raise RuntimeError(f"模拟窗口 {index} 网络失败")
        start = sum(
            value * factor
            for value, factor in zip(map(int, match.group(3).split(":")), (3600, 60, 1), strict=True)
        )
        end = sum(
            value * factor
            for value, factor in zip(map(int, match.group(4).split(":")), (3600, 60, 1), strict=True)
        )
        clip_end = min(end, start + 60)
        return json.dumps(
            {
                "moments": [
                    {
                        "title": f"窗口 {index} 的观点",
                        "category": "quote_opinion",
                        "start_time": _time_text(start),
                        "end_time": _time_text(clip_end),
                        "key_time": _time_text((start + clip_end) // 2),
                        "topic_key": f"topic-{index}",
                        "summary": "完整观点",
                        "highlight_reason": "可独立传播",
                        "score": 80,
                    }
                ]
            },
            ensure_ascii=False,
        )


class SafeRetryWindowProvider(FakeWindowProvider):
    def generate_json(self, prompt: str, retry_instruction: str | None = None) -> str:
        match = re.search(r"窗口 (\d+)/(\d+)，范围", prompt)
        assert match
        index = int(match.group(1))
        if index == 1 and self.calls[index] < 2:
            self.calls[index] += 1
            raise AIProviderError(
                "模拟 429",
                category="rate_limited",
                http_status=429,
                safe_to_retry=True,
                retry_after_seconds=0,
            )
        return super().generate_json(prompt, retry_instruction)


def test_six_hour_structured_timeline_window_coverage_density_and_total_limit():
    rows = _rows_for_duration(6 * 3600)
    windows = build_long_live_windows(rows)
    coverage = calculate_window_coverage(
        [(window.start_seconds, window.end_seconds) for window in windows],
        0,
        6 * 3600,
    )
    assert 85 <= len(windows) <= 95
    assert coverage == pytest.approx(1.0)

    moments = []
    for hour in range(6):
        for position in range(7):
            moments.append(_moment(hour * 3600 + position * 420, score=100 - position, title=f"{hour}-{position}"))
    selected = select_temporally_balanced_highlights(moments, density_per_hour=4, total_limit=30)
    hour_counts = Counter(((item["start_seconds"] + item["end_seconds"]) // 2) // 3600 for item in selected)
    assert len(selected) == 24
    assert set(hour_counts) == set(range(6))
    assert max(hour_counts.values()) == 4


def test_cross_window_duplicate_uses_time_and_semantic_merge():
    first = _moment(100, score=82, title="创业失败后的转折")
    first["source_window_indexes"] = [1]
    second = _moment(125, score=91, title="创业失败以后如何翻身")
    second["source_window_indexes"] = [2]
    unique = deduplicate_long_live_moments([first, second, _moment(1000, title="独立知识点")])
    assert len(unique) == 2
    merged = unique[0]
    assert merged["score"] == 91
    assert merged["start_seconds"] == 100
    assert merged["end_seconds"] == 185
    assert merged["source_window_indexes"] == [1, 2]


def test_ambiguous_window_failure_is_not_auto_retried_and_next_run_reuses_success(tmp_path):
    task_id = f"{PREFIX}resume"
    _create_task(task_id)
    _write_transcript(task_id, 30 * 60)
    request = LongLiveAnalysisRequest(
        task_id=task_id,
        transcript_path=get_artifact_paths(task_id)["transcript_path"],
        provider_name="remote",
        model_name="test-model",
        density_per_hour=4,
        total_limit=30,
    )
    first_provider = FakeWindowProvider({3})
    first = analyze_long_live_talk(request, provider=first_provider, sleep_fn=lambda _seconds: None)
    assert first_provider.calls[3] == 1
    assert first.meta["failed_window_count"] == 1
    checkpoints = list_long_live_window_checkpoints(task_id)
    failed = [item for item in checkpoints if item["window_index"] == 3][0]
    assert failed["status"] == "failed"
    assert failed["attempt_count"] == 1
    assert "模拟窗口 3 网络失败" in failed["error_message"]

    second_provider = FakeWindowProvider()
    second = analyze_long_live_talk(request, provider=second_provider, sleep_fn=lambda _seconds: None)
    assert second_provider.calls == Counter({3: 1})
    assert second.meta["failed_window_count"] == 0
    assert second.meta["reused_window_count"] == second.meta["window_count"] - 1
    assert second.meta["coverage_ratio"] == pytest.approx(1.0)


def test_safe_rate_limit_window_is_retried_within_bound(tmp_path):
    task_id = f"{PREFIX}safe-retry"
    _create_task(task_id)
    _write_transcript(task_id, 10 * 60)
    request = LongLiveAnalysisRequest(
        task_id=task_id,
        transcript_path=get_artifact_paths(task_id)["transcript_path"],
        provider_name="remote",
        model_name="test-model",
        density_per_hour=4,
        total_limit=30,
    )
    provider = SafeRetryWindowProvider()
    result = analyze_long_live_talk(request, provider=provider, sleep_fn=lambda _seconds: None)
    assert provider.calls[1] == 3
    assert result.meta["failed_window_count"] == 0


def test_stale_worker_cannot_update_long_live_checkpoint():
    task_id = f"{PREFIX}stale-worker"
    _create_task(task_id)
    request = LongLiveAnalysisRequest(
        task_id=task_id,
        transcript_path=get_artifact_paths(task_id)["transcript_path"],
        provider_name="remote",
        model_name="test-model",
    )
    window = LongLiveWindow(1, 1, 0, 60, tuple(_rows_for_duration(60)), "text")
    checkpoint = _get_or_create_checkpoint(request, "fingerprint", window)
    job = job_service.create_job(task_id, job_service.JOB_TYPE_AUTO_PIPELINE)
    claimed = job_service.claim_job(job["id"], "old-worker")
    assert claimed
    with get_connection() as connection:
        connection.execute(
            "UPDATE workflow_jobs SET lease_expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", job["id"]),
        )
        connection.commit()
    with job_service.job_lease_context(job["id"], "old-worker", claimed["lease_token"]):
        with pytest.raises(job_service.JobLeaseLostError):
            _mark_checkpoint_running(checkpoint["id"])
    with get_connection() as connection:
        status = connection.execute(
            "SELECT status FROM ai_analysis_windows WHERE id = ?",
            (checkpoint["id"],),
        ).fetchone()["status"]
    assert status == "queued"


def test_pipeline_blocks_incomplete_long_live_before_candidate_selection():
    task_id = f"{PREFIX}gate"
    _create_task(task_id)
    path = get_artifact_paths(task_id)["analysis_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "clips": [],
                "analysis_meta": {
                    "selection_profile": "long_live_talk",
                    "analysis_incomplete": True,
                    "coverage_ratio": 0.72,
                    "coverage_percent": 72,
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="低于 90%"):
        PipelineEngine()._select_clips(task_id, {"config": {}})


def test_ai_analysis_windows_schema_is_idempotent():
    init_db()
    init_db()
    with get_connection() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(ai_analysis_windows)")}
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list(ai_analysis_windows)")}
    assert {"transcript_fingerprint", "window_index", "attempt_count", "result_checksum", "next_retry_at"} <= columns
    assert "idx_ai_analysis_windows_resume" in indexes
