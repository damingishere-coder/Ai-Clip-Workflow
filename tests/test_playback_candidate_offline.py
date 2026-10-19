from copy import deepcopy
import json

import pytest

from scripts.compare_playback_candidate import (
    DEFAULT_BUNDLE, compare, digest, load_bundle, rank_items, render, score_values,
    weighted, write_new, review_material,
)


def example():
    rules, prompt_sha = load_bundle(DEFAULT_BUNDLE)
    items = []
    judgments = []
    for key, old, new in [("run/a", 80, 70), ("run/b", 70, 80)]:
        source = {"title": "<script>bad()</script>", "start_time": "00:00:00", "end_time": "00:01:00",
                  "transcript": [{"start": "00:00:00", "end": "00:00:03", "text": "实际问题"}]}
        items.append({"key": key, "task_id": "task", "run_id": "run", "clip_key": key,
                      "input": source, "input_sha256": digest(source),
                      "original_scores": dict.fromkeys(rules["weights"], old),
                      "original_total": old, "original_recommended": False,
                      "profile_version_id": None, "publications": []})
        judgments.append({"key": key, "input_sha256": digest(source),
                          "scores": dict.fromkeys(rules["weights"], new),
                          "rationale": "有连续问答", "opening_evidence": "实际问题", "uncertainty": "原音未核验"})
    packet = {"rules_sha256": digest(rules), "prompt_sha256": prompt_sha, "items": items}
    envelope = {"packet": packet, "sha256": digest(packet)}
    review = {"packet_sha256": envelope["sha256"], "prompt_sha256": prompt_sha,
              "mode": "astra_text_review_not_blind", "reviewer": "Astra", "items": judgments}
    return envelope, review, rules, prompt_sha


def test_review_handoff_has_no_outcomes_or_old_scores():
    envelope, _, _, _ = example()
    material = review_material(envelope)
    assert material['packet_sha256'] == envelope['sha256']
    assert set(material['items'][0]) == {'key', 'input_sha256', 'input'}
    text = json.dumps(material)
    for forbidden in ('publications', 'original_scores', 'original_total', 'original_recommended'):
        assert forbidden not in text


def test_platform_metrics_cannot_inject_html():
    args = example()
    args[0]['packet']['items'][0]['publications'] = [{
        'published_at': '2026-09-27', 'captured_at': '2026-09-27',
        'play_count': '<img src=x>', 'like_count': '<b>oops</b>', 'comment_count': 3,
    }]
    args[0]['sha256'] = digest(args[0]['packet'])
    args[1]['packet_sha256'] = args[0]['sha256']
    output = render(compare(*args))
    assert '<img src=x>' not in output
    assert '&lt;img src=x&gt;' in output
    assert '<b>oops</b>' not in output


def test_semantic_review_is_distinct_from_weight_only_and_inputs_stay_frozen():
    args = example()
    before = deepcopy(args)
    result = compare(*args)
    a, b = result["items"]
    assert (a["baseline_text_rank"], a["reweighted_only_rank"], a["text_review_rank"]) == (1, 1, 2)
    assert b["text_review_rank"] == 1
    assert args == before
    assert result["status"] == "offline_only_not_activated"


@pytest.mark.parametrize("bad", [None, True, float("nan"), float("inf"), -1, 101])
def test_invalid_dimensions_fail_closed(bad):
    scores = dict.fromkeys(load_bundle(DEFAULT_BUNDLE)[0]["weights"], 80)
    scores["hook"] = bad
    with pytest.raises(ValueError):
        score_values(scores)


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown", "evidence", "prompt", "weights", "rationale"])
def test_missing_or_tampered_review_is_not_silently_accepted(change):
    packet, reviews, rules, sha = example()
    if change == "missing":
        reviews["items"].pop()
    elif change == "duplicate":
        reviews["items"].append(deepcopy(reviews["items"][0]))
    elif change == "unknown":
        reviews["items"][0]["key"] = "unknown"
    elif change == "evidence":
        packet["packet"]["items"][0]["input"]["title"] = "changed"
    elif change == "prompt":
        reviews["prompt_sha256"] = "changed"
    elif change == "weights":
        rules["weights"]["hook"] = 0.1
    else:
        reviews["items"][0]["rationale"] = ""
    with pytest.raises(ValueError):
        compare(packet, reviews, rules, sha)


def test_outcome_metrics_do_not_change_score_or_rank():
    packet, reviews, rules, sha = example()
    first = compare(packet, reviews, rules, sha)
    packet["packet"]["items"][0]["publications"] = [{"play_count": 10**9, "like_count": 99, "comment_count": 42}]
    packet["sha256"] = digest(packet["packet"])
    reviews["packet_sha256"] = packet["sha256"]
    second = compare(packet, reviews, rules, sha)
    assert [(x["text_review"], x["text_review_rank"]) for x in first["items"]] == [
        (x["text_review"], x["text_review_rank"]) for x in second["items"]]


def test_competition_ties_are_not_broken_into_fake_recommendations():
    rows = [{"key": key, "score": score} for key, score in [("a", 90), ("b", 90), ("c", 80)]]
    rank_items(rows, "score")
    assert [r["score_rank"] for r in rows] == [1, 1, 3]


def test_known_weight_contribution_and_escaped_source():
    rules, _ = load_bundle(DEFAULT_BUNDLE)
    assert weighted(dict(zip(rules["weights"], (80, 70, 60, 90, 50, 100), strict=True)), rules["weights"]) == 74
    output = render(compare(*example()))
    assert "<script>bad()" not in output
    assert "&lt;script&gt;bad()" in output
    assert "不代表未发布或播放为零" in output


def test_existing_outputs_are_not_overwritten(tmp_path):
    path = tmp_path / "evidence.json"
    write_new(path, json.dumps({"original": True}))
    with pytest.raises(FileExistsError):
        write_new(path, "replacement")
    assert json.loads(path.read_text())["original"] is True
