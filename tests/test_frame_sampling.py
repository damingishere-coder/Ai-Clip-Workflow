import hashlib
from pathlib import Path
import shutil
import subprocess

import pytest

from app.services import frame_sampling_service as sampling
from app.services import storage_service


@pytest.fixture
def sample_storage(tmp_path, monkeypatch):
    replacements = {"storage_root": tmp_path, "tasks_dir": tmp_path / "tasks", "allowed_media_roots": ""}
    originals = {name: getattr(storage_service.settings, name) for name in replacements}
    for name, value in replacements.items():
        object.__setattr__(storage_service.settings, name, value)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"isolated-video")
    monkeypatch.setattr(sampling, "ensure_ffmpeg_available", lambda: shutil.which("ffmpeg") or "ffmpeg")
    try:
        yield source
    finally:
        for name, value in originals.items():
            object.__setattr__(storage_service.settings, name, value)


def make_sampler(source, **kwargs):
    return sampling.CandidateFrameSampler(task_id="frames", task_dir_name="frames", source_path=str(source), source_duration_seconds=4, **kwargs)


@pytest.mark.parametrize("start,end,total", [(-1, 2, 4), (2, 2, 4), (1, 5, 4), (float("nan"), 3, 4), (0, float("inf"), 4)])
def test_reject_invalid_candidate_boundaries(start, end, total):
    with pytest.raises(ValueError, match="时间范围"):
        sampling.plan_candidate_frames(start, end, total)


def test_moment_priority_and_honest_fallback():
    plan = sampling.plan_candidate_frames(100, 160, 200, key_moment_seconds=130, hook_seconds=90, reaction_seconds=float("nan"))
    assert len(plan.points) == 6
    assert {p.timestamp_seconds for p in plan.points} >= {100, 130, 132, 159.9}
    assert plan.ignored_moments == ("hook", "reaction")
    assert any(p.source == "interval_fallback" for p in plan.points)
    assert not any(p.role in ("hook", "reaction") for p in plan.points)
    assert list(plan.points) == sorted(plan.points, key=lambda p: p.timestamp_seconds)


@pytest.mark.parametrize("limit,expected", [(1, {130}), (2, {130, 132}), (3, {105, 130, 132})])
def test_small_budget_keeps_priority_before_chronological_sort(limit, expected):
    plan = sampling.plan_candidate_frames(100, 160, 200, key_moment_seconds=130, hook_seconds=105, reaction_seconds=135, frame_limit=limit)
    assert {p.timestamp_seconds for p in plan.points} == expected


@pytest.mark.parametrize("limit", [0, 9, True, 1.5])
def test_frame_budget_rejects_unbounded_requests(limit):
    with pytest.raises(ValueError):
        sampling.plan_candidate_frames(0, 4, 4, frame_limit=limit)


def test_timeout_terminates_owned_process(monkeypatch):
    class Process:
        def communicate(self, timeout):
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
    process = Process()
    terminated = []
    monkeypatch.setattr(sampling, "popen_process_group", lambda *a, **kw: process)
    monkeypatch.setattr(sampling, "terminate_process_tree", terminated.append)
    with pytest.raises(sampling.SamplingFailure, match="frame_timeout"):
        sampling._run(["ffmpeg"], .1)
    assert terminated == [process]


def test_failure_cleanup_candidate_budget_and_manifest(sample_storage, monkeypatch):
    sampler = make_sampler(sample_storage, max_candidates=1)
    def fail(command, timeout):
        Path(command[-1]).write_bytes(b"partial JPEG")
        raise sampling.SamplingFailure("frame_timeout")
    monkeypatch.setattr(sampling, "_run", fail)
    plan = sampling.plan_candidate_frames(0, 4, 4)
    result = sampler.sample(plan, candidate_source_id="candidate-1")
    assert result["status"] == "unavailable"
    assert len(result["skipped"]) == 6
    assert {x["reason"] for x in result["skipped"]} == {"frame_timeout"}
    assert not list(sampler.directory.glob("*.jpg"))
    assert (sampler.directory / result["manifest_file_name"]).is_file()
    result = sampler.sample(plan, candidate_source_id="candidate-2")
    assert result["skipped"] == [{"reason": "candidate_budget_exhausted"}]
    assert sample_storage.read_bytes() == b"isolated-video"


def test_source_change_is_recorded_without_decoding(sample_storage, monkeypatch):
    sampler = make_sampler(sample_storage)
    sample_storage.write_bytes(b"changed-video")
    monkeypatch.setattr(sampling, "_run", lambda *args: pytest.fail("must not decode changed source"))
    result = sampler.sample(sampling.plan_candidate_frames(0, 4, 4), candidate_source_id="a")
    assert result["status"] == "unavailable"
    assert {x["reason"] for x in result["skipped"]} == {"source_changed"}


def test_mid_candidate_source_change_discards_earlier_frames(sample_storage, monkeypatch):
    sampler = make_sampler(sample_storage)
    decoded = []
    def decode(command, timeout):
        if command[-1] == "pipe:1":
            return subprocess.CompletedProcess(command, 0, bytes([100]) * 256, b"")
        decoded.append(command)
        if len(decoded) == 2:
            sample_storage.write_bytes(b"changed-during-frame-reading")
        Path(command[-1]).write_bytes(b"\xff\xd8" + bytes([len(decoded)]) + b"\xff\xd9")
        return subprocess.CompletedProcess(command, 0, b"", b"n: 0 pts: 0 pts_time:0")
    monkeypatch.setattr(sampling, "_run", decode)
    result = sampler.sample(sampling.plan_candidate_frames(0, 4, 4), candidate_source_id="changed")
    assert result["status"] == "unavailable"
    assert not result["frames"]
    assert len(decoded) == 2
    assert not list(sampler.directory.glob("*.jpg"))


def test_manually_built_plan_cannot_escape_bounds(sample_storage):
    sampler = make_sampler(sample_storage)
    plan = sampling.SamplingPlan(1, 3, (sampling.FramePoint(3.5, "interval", "interval_fallback"),))
    with pytest.raises(ValueError, match="超出候选"):
        sampler.sample(plan, candidate_source_id="a")


def test_unsafe_source_and_cache_symlink_are_rejected(sample_storage):
    outside = sample_storage.parent.parent / "outside-source.mp4"
    outside.write_bytes(b"outside")
    with pytest.raises(storage_service.StorageSafetyError):
        make_sampler(outside)
    task_root = storage_service.get_task_directory("frames", "frames")
    task_root.mkdir(parents=True)
    external_cache = sample_storage.parent.parent / "external-cache"
    external_cache.mkdir(exist_ok=True)
    try:
        (task_root / "analysis").symlink_to(external_cache, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前系统不允许创建测试符号链接：{exc}")
    with pytest.raises(storage_service.StorageSafetyError, match="逃逸"):
        make_sampler(sample_storage)
    assert not list(external_cache.iterdir())


def generate_video(source, *, black=False, timestamp_mode="normal"):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg 未安装，不能执行真实媒体集成测试")
    rate = 8 if timestamp_mode == "vfr" else 2
    inputs = ["-f", "lavfi", "-i", "color=c=black:s=160x120:r=2:d=4"] if black else ["-f", "lavfi", "-i", f"testsrc2=size=320x240:rate={rate}:duration=4"]
    extra = ["-output_ts_offset", "5"] if timestamp_mode == "offset" else []
    if timestamp_mode == "vfr":
        extra = ["-vf", "select='eq(mod(n,5),0)+eq(mod(n,7),0)'", "-fps_mode", "vfr"]
    subprocess.run([ffmpeg, "-v", "error", *inputs, *extra, "-c:v", "libx264", "-y", str(source)], capture_output=True, check=True, timeout=15)


@pytest.mark.parametrize("timestamp_mode", ["normal", "offset", "vfr"])
def test_real_sampling_uses_decoded_pts_and_preserves_original(sample_storage, timestamp_mode):
    generate_video(sample_storage, timestamp_mode=timestamp_mode)
    source_hash = hashlib.sha256(sample_storage.read_bytes()).hexdigest()
    sampler = make_sampler(sample_storage)
    result = sampler.sample(sampling.plan_candidate_frames(.7, 3.1, 4, key_moment_seconds=2), candidate_source_id="real")
    assert result["status"] in ("completed", "partial")
    assert 1 <= len(result["frames"]) <= 6
    assert all(.7 <= frame["actual_timestamp_seconds"] < 3.1 for frame in result["frames"])
    assert any(abs(frame["timestamp_seconds"] - frame["actual_timestamp_seconds"]) > .01 for frame in result["frames"])
    assert result["source_sha256"] == source_hash == hashlib.sha256(sample_storage.read_bytes()).hexdigest()
    for frame in result["frames"]:
        assert hashlib.sha256((sampler.directory/frame["file_name"]).read_bytes()).hexdigest() == frame["sha256"]


def test_real_black_frames_and_byte_budget_leave_no_jpegs(sample_storage):
    generate_video(sample_storage, black=True)
    sampler = make_sampler(sample_storage)
    result = sampler.sample(sampling.plan_candidate_frames(0, 4, 4), candidate_source_id="black")
    assert result["status"] == "unavailable"
    assert {x["reason"] for x in result["skipped"]} <= {"black_frame", "frame_decode_failed", "frame_io_error"}
    assert any(x["reason"] == "black_frame" for x in result["skipped"])
    assert not list(sampler.directory.glob("*.jpg"))
    limited = make_sampler(sample_storage, max_bytes=1)
    result = limited.sample(sampling.plan_candidate_frames(0, 4, 4), candidate_source_id="limited")
    assert not result["frames"]
    assert any(x["reason"] == "image_budget_exhausted" for x in result["skipped"])
    assert not list(limited.directory.glob("*.jpg"))


def test_elapsed_budget_never_starts_more_decoders(sample_storage, monkeypatch):
    sampler = make_sampler(sample_storage, timeout_seconds=1)
    monkeypatch.setattr(sampling.time, "monotonic", lambda: sampler.deadline + 1)
    monkeypatch.setattr(sampling, "_run", lambda *args: pytest.fail("budget must be checked before spawn"))
    result = sampler.sample(sampling.plan_candidate_frames(0, 4, 4), candidate_source_id="elapsed")
    assert result["status"] == "unavailable"
    assert {x["reason"] for x in result["skipped"]} == {"sampling_time_budget_exhausted"}


def test_decoded_frame_beyond_boundary_is_discarded(sample_storage, monkeypatch):
    sampler = make_sampler(sample_storage)
    def outside(command, timeout):
        Path(command[-1]).write_bytes(b"\xff\xd8fixture\xff\xd9")
        return subprocess.CompletedProcess(command, 0, b"", b"n: 0 pts: 100 pts_time:5")
    monkeypatch.setattr(sampling, "_run", outside)
    result = sampler.sample(sampling.plan_candidate_frames(0, 4, 4), candidate_source_id="outside")
    assert result["status"] == "unavailable"
    assert {x["reason"] for x in result["skipped"]} == {"decoded_frame_outside_candidate"}
    assert not list(sampler.directory.glob("*.jpg"))


def test_real_static_frames_are_deduplicated(sample_storage):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg 未安装")
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=160x120:r=25:d=4", "-c:v", "libx264", "-y", str(sample_storage)], capture_output=True, check=True, timeout=15)
    sampler = make_sampler(sample_storage)
    result = sampler.sample(sampling.plan_candidate_frames(0, 3, 4), candidate_source_id="static")
    assert len(result["frames"]) == 1
    assert {x["reason"] for x in result["skipped"]} == {"duplicate_frame"}
    assert len(list(sampler.directory.glob("*.jpg"))) == 1
