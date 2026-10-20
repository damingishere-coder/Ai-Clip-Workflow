import pytest

from app.services.ai import variety_comedy_analyzer as analyzer
from app.services.content_profile_definitions import playback_comedy_profile


def rows_at(*bounds):
    return [analyzer.TranscriptRow(
        start_time=analyzer._seconds_to_time(start), end_time=analyzer._seconds_to_time(end),
        start_seconds=start, end_seconds=end, text="原文",
    ) for start, end in bounds]


@pytest.mark.parametrize("start,end,next_end", [(667, 712, 713), (1387, 1432, 1434)])
def test_exact_next_utterance_start_remains_a_natural_45_second_end(start, end, next_end):
    # E1896 / E1897: the story ends a second before the next utterance starts.
    rows = rows_at((start, start + 2), (start + 20, start + 22), (end - 3, end - 1), (end, next_end))
    assert analyzer.normalize_clip_bounds(start, end, start + 20, rows, policy=playback_comedy_profile()) == (start, end)


def test_no_new_boundary_is_invented_inside_a_silence_gap():
    rows = rows_at((0, 2), (20, 22), (42, 44), (48, 50))
    assert analyzer.normalize_clip_bounds(0, 45, 20, rows, policy=playback_comedy_profile()) is None


def test_next_utterance_start_cannot_split_overlapping_speech():
    rows = rows_at((0, 2), (20, 22), (42, 44), (43, 47), (45, 49))
    assert analyzer.normalize_clip_bounds(0, 45, 20, rows, policy=playback_comedy_profile()) is None


@pytest.mark.parametrize("end", [44, 151])
def test_exact_utterance_boundary_does_not_relax_frozen_duration_limits(end):
    rows = rows_at((0, 2), (20, 22), (end - 3, end - 1), (end, end + 2))
    assert analyzer.normalize_clip_bounds(0, end, 20, rows, policy=playback_comedy_profile()) is None


def test_expansion_validation_accepts_the_preserved_natural_end():
    rows = rows_at((0, 2), (20, 22), (42, 44), (45, 47))
    payload = {"clips": [{"source_id": "w001_m01", "title": "原文事件", "start_time": "00:00:00",
        "end_time": "00:00:45", "key_moment_time": "00:00:20", "summary": "问题和完整回答",
        "highlight_reason": "有完整回应", "arc_structure": "问题与回应", "topic_key": "原文事件",
        "suggested_editing": "保留原视频", "humor_score": 70, "interaction_reaction_score": 70,
        "completeness_score": 70, "hook_score": 70, "novelty_score": 70, "title_score": 70}]}
    analyzer._validate_expansion_payload(payload, {"w001_m01": rows}, policy=playback_comedy_profile())
