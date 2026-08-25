from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import media_preflight_service, subtitle_data_service, task_service, video_cut_service
from app.services.video_cut_service import CutPlan


def test_task_probe_timeout_returns_bounded_metadata(monkeypatch, tmp_path: Path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(task_service.shutil, "which", lambda _name: "ffprobe")
    monkeypatch.setattr(
        task_service.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffprobe", 1)),
    )
    result = task_service._probe_video(source)
    assert result["duration"] == "尚未读取"
    assert result["video_size"] != "读取失败"


def test_cut_timeout_cleans_partial_output(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "clip.mp4"
    plan = CutPlan("clip-1", "title", "0", "10", output, 10)

    def timeout(command, **_kwargs):
        Path(command[-1]).write_bytes(b"partial")
        raise subprocess.TimeoutExpired(command[0], 1)

    monkeypatch.setattr(video_cut_service.subprocess, "run", timeout)
    result = video_cut_service.cut_single_clip("ffmpeg", source, plan)
    assert result.status == "failed"
    assert result.output_file_path == ""
    assert "已清理" in str(result.error_message)
    assert not output.exists()


def test_cut_nonzero_exit_cleans_partial_output(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "clip.mp4"
    plan = CutPlan("clip-1", "title", "0", "10", output, 10)

    def fail(command, **_kwargs):
        Path(command[-1]).write_bytes(b"partial")
        return SimpleNamespace(returncode=1, stderr="decode failed")

    monkeypatch.setattr(video_cut_service.subprocess, "run", fail)
    result = video_cut_service.cut_single_clip("ffmpeg", source, plan)
    assert result.status == "failed"
    assert not output.exists()


def test_cut_atomic_replace_failure_cleans_partial_output(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "clip.mp4"
    plan = CutPlan("clip-1", "title", "0", "10", output, 10)

    def succeed(command, **_kwargs):
        Path(command[-1]).write_bytes(b"complete")
        return SimpleNamespace(returncode=0, stderr="")

    original_replace = Path.replace

    def fail_part_replace(path: Path, target):
        if path.name.endswith(".part.mp4"):
            raise OSError("rename denied")
        return original_replace(path, target)

    monkeypatch.setattr(video_cut_service.subprocess, "run", succeed)
    monkeypatch.setattr(Path, "replace", fail_part_replace)
    result = video_cut_service.cut_single_clip("ffmpeg", source, plan)
    assert result.status == "failed"
    assert "切换失败" in str(result.error_message)
    assert not output.exists()
    assert not (tmp_path / "clip.part.mp4").exists()


def test_subtitle_dimension_probe_fails_closed_on_timeout(monkeypatch, tmp_path: Path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"video")
    monkeypatch.setattr(subtitle_data_service.shutil, "which", lambda _name: "ffprobe")
    monkeypatch.setattr(
        subtitle_data_service.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffprobe", 1)),
    )
    with pytest.raises(RuntimeError, match="读取视频尺寸超过"):
        subtitle_data_service._probe_media_dimensions(media)


def test_media_decode_sample_wraps_timeout(monkeypatch, tmp_path: Path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"video")
    monkeypatch.setattr(media_preflight_service.shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        media_preflight_service.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffmpeg", 1)),
    )
    with pytest.raises(ValueError, match="解码抽样超过"):
        media_preflight_service._run_decode_sample(media, 0)
