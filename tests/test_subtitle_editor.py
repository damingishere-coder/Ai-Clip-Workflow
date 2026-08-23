from __future__ import annotations

from array import array
import hashlib
import json
from uuid import uuid4

import pysubs2
import pytest
from fastapi.testclient import TestClient

from app.db.database import get_connection, init_db
from app.main import app
from app.services.subtitle_data_service import (
    apply_revision_operations,
    create_manual_revision,
    ensure_clip_track,
    ensure_source_track,
    evaluate_subtitle_quality,
    export_subtitle_text,
    get_revision,
    get_track,
    get_waveform_peaks,
    import_subtitle_text,
    inherit_cues_for_clip,
    serialize_revision_to_ass,
)
from app.services.video_cut_service import CutResult
from app.services.video_cut_workflow_service import _insert_output_clip_record
from app.services.subtitle_workflow_service import _write_ass_file


PREFIX = "test-subtitle-editor-"


@pytest.fixture(autouse=True)
def subtitle_editor_database():
    init_db()
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM subtitle_cues WHERE revision_id IN (SELECT id FROM subtitle_revisions WHERE track_id IN (SELECT id FROM subtitle_tracks WHERE task_id LIKE ?))",
            (f"{PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM subtitle_revisions WHERE track_id IN (SELECT id FROM subtitle_tracks WHERE task_id LIKE ?)",
            (f"{PREFIX}%",),
        )
        connection.execute("DELETE FROM subtitle_tracks WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM subtitle_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM cut_runs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM transcription_chunks WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM transcription_runs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _create_task(*, segments: list[dict] | None = None, with_clip: bool = True) -> tuple[str, str | None]:
    task_id = f"{PREFIX}{uuid4().hex[:10]}"
    now = "2026-08-23T12:00:00+00:00"
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO tasks (id, task_name, platform, status, created_at, updated_at) VALUES (?, ?, 'general', 'completed', ?, ?)",
            (task_id, "字幕编辑器测试", now, now),
        )
        if segments is not None:
            _insert_transcription(connection, task_id, segments, now)
        output_id = None
        if with_clip:
            output_id = f"out-{uuid4().hex[:10]}"
            connection.execute(
                """
                INSERT INTO output_clip (
                    id, task_id, output_file_path, output_file_name, status, is_active,
                    source_start_ms, source_end_ms, source_duration_ms,
                    source_fingerprint, snapshot_source, created_at, updated_at
                ) VALUES (?, ?, ?, 'clip.mp4', 'completed', 1, 1000, 5000, 4000,
                          'source-v1', 'cut_commit', ?, ?)
                """,
                (output_id, task_id, "C:/missing/clip.mp4", now, now),
            )
        connection.commit()
    return task_id, output_id


def _insert_transcription(connection, task_id: str, segments: list[dict], now: str) -> None:
    run_id = f"run-{uuid4().hex[:10]}"
    raw = json.dumps(segments, ensure_ascii=False, separators=(",", ":"))
    checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    connection.execute(
        """
        INSERT INTO transcription_runs (
            id, task_id, source_fingerprint, provider, model, device, compute_type,
            chunk_seconds, overlap_seconds, status, total_chunks, completed_chunks,
            is_active, created_at, updated_at, completed_at
        ) VALUES (?, ?, 'source-v1', 'local', 'small', 'cpu', 'int8',
                  120, 5, 'completed', 1, 1, 1, ?, ?, ?)
        """,
        (run_id, task_id, now, now, now),
    )
    connection.execute(
        """
        INSERT INTO transcription_chunks (
            id, run_id, task_id, chunk_index, start_ms, end_ms, status,
            attempt_count, result_json, result_checksum, created_at, updated_at
        ) VALUES (?, ?, ?, 1, 0, 120000, 'completed', 1, ?, ?, ?, ?)
        """,
        (f"chunk-{uuid4().hex[:10]}", run_id, task_id, raw, checksum, now, now),
    )


def _segments(count: int = 4) -> list[dict]:
    return [
        {
            "start_seconds": index * 1.5 + 0.123,
            "end_seconds": index * 1.5 + 1.345,
            "text": f"第{index + 1}条中文字幕",
            "confidence": 0.91,
            "words": [
                {
                    "start_ms": round((index * 1.5 + 0.123) * 1000),
                    "end_ms": round((index * 1.5 + 0.5) * 1000),
                    "text": "第",
                    "confidence": 0.9,
                }
            ],
        }
        for index in range(count)
    ]


def test_schema_migration_is_idempotent_and_contains_revision_tables():
    init_db()
    init_db()
    with get_connection() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        output_columns = {row[1] for row in connection.execute("PRAGMA table_info(output_clip)")}
    assert {"subtitle_tracks", "subtitle_revisions", "subtitle_cues"} <= names
    assert {"source_start_ms", "source_end_ms", "source_duration_ms", "source_fingerprint"} <= output_columns


def test_source_track_uses_structured_checkpoint_with_millisecond_precision():
    task_id, _ = _create_task(segments=_segments(), with_clip=False)
    track = ensure_source_track(task_id)
    revision = get_revision(track["active_revision_id"], include_cues=True)
    assert revision["cue_count"] == 4
    assert revision["cues"][0]["start_ms"] == 123
    assert revision["cues"][0]["end_ms"] == 1345
    assert revision["cues"][0]["confidence"] == pytest.approx(0.91)


def test_more_than_120_cues_are_not_truncated():
    task_id, _ = _create_task(segments=_segments(150), with_clip=False)
    track = ensure_source_track(task_id)
    revision = get_revision(track["active_revision_id"], include_cues=True)
    assert revision["cue_count"] == 150
    assert len(revision["cues"]) == 150
    assert revision["cues"][-1]["text"] == "第150条中文字幕"


def test_source_to_clip_boundary_conversion_is_exact():
    source = [
        {"id": "a", "start_ms": 0, "end_ms": 1500, "text": "开头"},
        {"id": "b", "start_ms": 1500, "end_ms": 3000, "text": "中间"},
        {"id": "c", "start_ms": 4500, "end_ms": 6000, "text": "结尾"},
        {"id": "d", "start_ms": 7000, "end_ms": 8000, "text": "范围外"},
    ]
    inherited = inherit_cues_for_clip(source, 1000, 5000)
    assert [(cue["start_ms"], cue["end_ms"], cue["text"]) for cue in inherited] == [
        (0, 500, "开头"),
        (500, 2000, "中间"),
        (3500, 4000, "结尾"),
    ]
    assert [cue["source_cue_id"] for cue in inherited] == ["a", "b", "c"]


def test_clip_track_inherits_snapshot_and_manual_revision_is_not_overwritten():
    task_id, output_id = _create_task(segments=_segments(), with_clip=True)
    clip_track = ensure_clip_track(task_id, output_id)
    active = get_revision(clip_track["active_revision_id"], include_cues=True)
    edited = [{**cue, "text": f"人工：{cue['text']}"} for cue in active["cues"]]
    manual = create_manual_revision(
        clip_track["id"],
        base_revision_id=active["id"],
        cues=edited,
        note="人工精修",
    )

    with get_connection() as connection:
        chunk = connection.execute(
            "SELECT * FROM transcription_chunks WHERE task_id = ?", (task_id,)
        ).fetchone()
        changed = _segments()
        changed[1]["text"] = "原片字幕已经变化"
        raw = json.dumps(changed, ensure_ascii=False, separators=(",", ":"))
        connection.execute(
            "UPDATE transcription_chunks SET result_json = ?, result_checksum = ? WHERE id = ?",
            (raw, hashlib.sha256(raw.encode("utf-8")).hexdigest(), chunk["id"]),
        )
        connection.commit()

    ensure_source_track(task_id, force=True)
    protected = get_track(clip_track["id"])
    assert protected["active_revision_id"] == manual["id"]
    assert protected["sync_status"] == "pending_sync"
    assert protected["has_manual_edits"] is True


def test_source_manual_revision_syncs_unedited_clip_but_only_flags_edited_clip():
    task_id, output_id = _create_task(segments=_segments(), with_clip=True)
    source_track = ensure_source_track(task_id)
    clip_track = ensure_clip_track(task_id, output_id)
    source_revision = get_revision(source_track["active_revision_id"], include_cues=True)
    source_cues = [{**cue, "text": f"原片修改：{cue['text']}"} for cue in source_revision["cues"]]
    new_source = create_manual_revision(
        source_track["id"],
        base_revision_id=source_revision["id"],
        cues=source_cues,
    )
    followed = get_track(clip_track["id"])
    assert followed["source_revision_id"] == new_source["id"]
    assert followed["sync_status"] == "up_to_date"

    clip_revision = get_revision(followed["active_revision_id"], include_cues=True)
    manual_clip = create_manual_revision(
        clip_track["id"],
        base_revision_id=clip_revision["id"],
        cues=[{**cue, "text": f"切片精修：{cue['text']}"} for cue in clip_revision["cues"]],
    )
    unchanged_source = ensure_clip_track(task_id, output_id)
    assert unchanged_source["active_revision_id"] == manual_clip["id"]
    assert unchanged_source["sync_status"] == "manual"
    source_after = get_revision(new_source["id"], include_cues=True)
    create_manual_revision(
        source_track["id"],
        base_revision_id=new_source["id"],
        cues=[{**cue, "text": f"再次修改：{cue['text']}"} for cue in source_after["cues"]],
    )
    protected = get_track(clip_track["id"])
    assert protected["active_revision_id"] == manual_clip["id"]
    assert protected["sync_status"] == "pending_sync"


def test_multiple_operations_keep_cue_ids_until_new_revision_is_committed():
    task_id, _ = _create_task(segments=_segments(), with_clip=False)
    track = ensure_source_track(task_id)
    revision = get_revision(track["active_revision_id"], include_cues=True)
    cue_id = revision["cues"][0]["id"]
    updated = apply_revision_operations(
        track["id"],
        base_revision_id=revision["id"],
        operations=[
            {"type": "update", "cue_id": cue_id, "text": "连续操作"},
            {"type": "shift", "cue_ids": [cue_id], "delta_ms": 250},
        ],
    )
    assert updated["cues"][0]["text"] == "连续操作"
    assert updated["cues"][0]["start_ms"] == 373


@pytest.mark.parametrize("format_name", ["srt", "vtt", "ass"])
def test_pysubs2_round_trip_preserves_chinese_and_milliseconds(format_name: str):
    task_id, _ = _create_task(segments=_segments(), with_clip=False)
    track = ensure_source_track(task_id)
    content, _media_type, _filename = export_subtitle_text(track["id"], format_name=format_name)
    parsed = pysubs2.SSAFile.from_string(content, format_=format_name)
    # ASS 规范使用厘秒；SRT/VTT 保留 1ms，ASS 最多产生 5ms 的量化误差。
    tolerance_ms = 5 if format_name == "ass" else 0
    assert abs(parsed.events[0].start - 123) <= tolerance_ms
    assert abs(parsed.events[0].end - 1345) <= tolerance_ms
    assert "中文字幕" in parsed.events[0].plaintext

    imported = import_subtitle_text(track["id"], content=content, format_name=format_name)
    assert abs(imported["cues"][0]["start_ms"] - 123) <= tolerance_ms
    assert abs(imported["cues"][0]["end_ms"] - 1345) <= tolerance_ms


@pytest.mark.parametrize("dimensions", [(1080, 1920), (1920, 1080), (1080, 1080)])
def test_ass_resolution_follows_real_media_dimensions(monkeypatch, dimensions):
    task_id, output_id = _create_task(segments=_segments(), with_clip=True)
    track = ensure_clip_track(task_id, output_id)
    monkeypatch.setattr("app.services.subtitle_data_service._probe_media_dimensions", lambda _path: dimensions)
    ass = serialize_revision_to_ass(track["id"], track["active_revision_id"])
    document = pysubs2.SSAFile.from_string(ass, format_="ass")
    assert int(document.info["PlayResX"]) == dimensions[0]
    assert int(document.info["PlayResY"]) == dimensions[1]
    assert document.styles["Default"].marginv == round(dimensions[1] * 0.05)


def test_ass_applies_default_host_and_guest_speaker_styles(monkeypatch):
    task_id, output_id = _create_task(segments=_segments(), with_clip=True)
    track = ensure_clip_track(task_id, output_id)
    revision = get_revision(track["active_revision_id"], include_cues=True)
    cues = []
    for index, cue in enumerate(revision["cues"]):
        cues.append({**cue, "speaker": "主播" if index == 0 else "嘉宾"})
    manual = create_manual_revision(
        track["id"],
        base_revision_id=revision["id"],
        cues=cues,
    )
    monkeypatch.setattr(
        "app.services.subtitle_data_service._probe_media_dimensions",
        lambda _path: (1920, 1080),
    )
    document = pysubs2.SSAFile.from_string(
        serialize_revision_to_ass(track["id"], manual["id"]),
        format_="ass",
    )
    assert document.events[0].style != "Default"
    assert document.events[1].style != "Default"
    assert document.styles[document.events[0].style].primarycolor == pysubs2.Color(255, 255, 255)
    assert document.styles[document.events[1].style].primarycolor == pysubs2.Color(255, 214, 10)


def test_ass_render_uses_explicit_immutable_revision_not_latest_active(monkeypatch, tmp_path):
    task_id, output_id = _create_task(segments=_segments(), with_clip=True)
    track = ensure_clip_track(task_id, output_id)
    original = get_revision(track["active_revision_id"], include_cues=True)
    create_manual_revision(
        track["id"],
        base_revision_id=original["id"],
        cues=[{**cue, "text": "最新人工版本"} for cue in original["cues"]],
    )
    monkeypatch.setattr(
        "app.services.subtitle_workflow_service.get_artifact_paths",
        lambda _task_id: {"subtitled_dir": tmp_path},
    )
    path = _write_ass_file(
        task_id,
        {"id": output_id, "output_file_name": "fixed.mp4"},
        {},
        revision_id=original["id"],
    )
    document = pysubs2.load(str(path), encoding="utf-8")
    assert "最新人工版本" not in document.events[0].plaintext
    assert "中文字幕" in document.events[0].plaintext


def test_quality_rules_only_report_and_do_not_change_text():
    cues = [
        {"id": "a", "start_ms": 0, "end_ms": 500, "text": "这是一行非常非常非常非常非常长的中文字幕"},
        {"id": "b", "start_ms": 400, "end_ms": 9000, "text": "发生重叠\n第二行\n第三行"},
    ]
    original = json.loads(json.dumps(cues, ensure_ascii=False))
    quality = evaluate_subtitle_quality(cues)
    codes = {issue["code"] for issue in quality["issues"]}
    assert {"too_short", "line_too_long", "reading_speed", "overlap", "too_long", "too_many_lines"} <= codes
    assert quality["error_count"] == 1
    assert cues == original


def test_cut_commit_saves_immutable_source_bounds():
    task_id, _ = _create_task(segments=None, with_clip=False)
    candidate_id = f"candidate-{uuid4().hex[:8]}"
    run_id = f"cut-{uuid4().hex[:8]}"
    now = "2026-08-23T12:00:00+00:00"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO clip_candidates (
                id, task_id, title, start_time, end_time, duration_seconds,
                created_at, updated_at
            ) VALUES (?, ?, '候选', '00:00:01.250', '00:00:05.750', 5, ?, ?)
            """,
            (candidate_id, task_id, now, now),
        )
        connection.execute(
            "INSERT INTO cut_runs (id, task_id, run_number, status, is_active, created_at, updated_at) VALUES (?, ?, 1, 'processing', 0, ?, ?)",
            (run_id, task_id, now, now),
        )
        connection.commit()
    _insert_output_clip_record(
        task_id,
        run_id,
        CutResult(candidate_id, "C:/missing/output.mp4", "output.mp4", "completed"),
        source_fingerprint="fingerprint-v1",
    )
    with get_connection() as connection:
        connection.execute(
            "UPDATE clip_candidates SET start_time = '00:01:00', end_time = '00:02:00' WHERE id = ?",
            (candidate_id,),
        )
        row = connection.execute(
            "SELECT * FROM output_clip WHERE task_id = ? AND clip_candidate_id = ?",
            (task_id, candidate_id),
        ).fetchone()
    assert row["source_start_ms"] == 1250
    assert row["source_end_ms"] == 5750
    assert row["source_duration_ms"] == 4500
    assert row["source_fingerprint"] == "fingerprint-v1"
    assert row["snapshot_source"] == "cut_commit"


def test_cue_api_supports_time_range_and_pagination():
    task_id, _ = _create_task(segments=_segments(20), with_clip=False)
    track = ensure_source_track(task_id)
    response = TestClient(app).get(
        f"/api/subtitles/tracks/{track['id']}/cues",
        params={"start_ms": 3000, "end_ms": 9000, "offset": 1, "limit": 2},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 4
    assert len(payload["cues"]) == 2
    assert all(cue["end_ms"] > 3000 and cue["start_ms"] < 9000 for cue in payload["cues"])


def test_subtitle_page_loads_local_editor_and_vendored_wavesurfer():
    task_id, _ = _create_task(segments=_segments(), with_clip=True)
    response = TestClient(app).get(f"/subtitles/{task_id}")
    assert response.status_code == 200
    assert 'id="subtitle-editor"' in response.text
    assert "vendor/wavesurfer/wavesurfer.min.js" in response.text
    assert "vendor/wavesurfer/regions.min.js" in response.text
    assert "js/subtitle-editor.js" in response.text


def test_waveform_peaks_are_precomputed_at_low_sample_rate_and_cached(monkeypatch, tmp_path):
    media_path = tmp_path / "source.mp4"
    media_path.write_bytes(b"fake-video-source")
    task_id, _ = _create_task(segments=_segments(), with_clip=False)
    with get_connection() as connection:
        connection.execute(
            "UPDATE tasks SET original_video_path = ? WHERE id = ?",
            (str(media_path), task_id),
        )
        connection.commit()
    track = ensure_source_track(task_id)
    transcript_path = tmp_path / "artifacts" / "transcript.md"
    calls = []

    class Result:
        returncode = 0
        stderr = b""
        stdout = array("h", [0, 1000, -2000, 32000, -12000] * 400).tobytes()

    def fake_run(command, **_kwargs):
        calls.append(command)
        return Result()

    monkeypatch.setattr("app.services.subtitle_data_service.shutil.which", lambda _name: "ffmpeg")
    monkeypatch.setattr("app.services.subtitle_data_service.subprocess.run", fake_run)
    monkeypatch.setattr(
        "app.services.subtitle_data_service.get_artifact_paths",
        lambda _task_id: {"transcript_path": transcript_path},
    )
    first = get_waveform_peaks(track["id"], max_points=1000)
    second = get_waveform_peaks(track["id"], max_points=1000)
    assert calls and calls[0][calls[0].index("-ar") + 1] == "100"
    assert first["point_count"] <= 1000
    assert first["cached"] is False
    assert second["cached"] is True
    assert len(calls) == 1
