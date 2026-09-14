"""候选范围内的有界视觉采样；尚未接入生产 Analyzer，不涉及发布封面。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
import subprocess
import time
from uuid import uuid4

from app.core.config import settings
from app.services.managed_process_service import popen_process_group, terminate_process_tree
from app.services.storage_service import (
    StorageSafetyError,
    get_task_directory,
    resolve_video_file_path,
    validate_source_video_path,
)
from app.services.transcription_checkpoint_service import fingerprint_file_full
from app.services.video_cut_service import ensure_ffmpeg_available


SAMPLER_VERSION = "candidate-frames-v1"


@dataclass(frozen=True)
class FramePoint:
    timestamp_seconds: float
    role: str
    source: str


@dataclass(frozen=True)
class SamplingPlan:
    start_seconds: float
    end_seconds: float
    points: tuple[FramePoint, ...]
    ignored_moments: tuple[str, ...] = ()


def plan_candidate_frames(
    start_seconds: float,
    end_seconds: float,
    source_duration_seconds: float,
    *,
    key_moment_seconds: float | None = None,
    hook_seconds: float | None = None,
    reaction_seconds: float | None = None,
    frame_limit: int = 6,
) -> SamplingPlan:
    """所有输入均为原片绝对秒数；缺失的 hook/reaction 不能伪装成检测结果。"""
    values = (start_seconds, end_seconds, source_duration_seconds)
    if not all(math.isfinite(v) for v in values) or not 0 <= start_seconds < end_seconds <= source_duration_seconds:
        raise ValueError("候选必须位于有效原片时间范围内")
    if isinstance(frame_limit, bool) or not isinstance(frame_limit, int) or not 1 <= frame_limit <= 8:
        raise ValueError("单候选帧数必须为 1–8")
    duration = end_seconds - start_seconds
    last = end_seconds - min(0.1, duration / 10)
    points: list[FramePoint] = []
    ignored: list[str] = []

    def add(value: float, role: str, source: str) -> None:
        value = min(last, max(start_seconds, value))
        separation = min(0.25, duration / 20)
        if all(abs(value - p.timestamp_seconds) >= separation for p in points):
            points.append(FramePoint(value, role, source))

    # 有明确时间的证据优先于均匀补点；最终按时间排序，不按角色凑满数量。
    for role, moment in (("key_moment", key_moment_seconds), ("hook", hook_seconds), ("reaction", reaction_seconds)):
        if moment is None:
            continue
        if not math.isfinite(moment) or not start_seconds <= moment < end_seconds:
            ignored.append(role)
            continue
        add(moment, role, "candidate_timestamp")
        if role == "key_moment" and moment + 2 < last:
            add(moment + 2, "after_key_moment", "key_plus_2s")
    add(start_seconds, "clip_start", "candidate_boundary")
    add(last, "clip_end", "candidate_boundary")
    for fraction in (0.2, 0.5, 0.8, 0.35, 0.65, 0.1, 0.9):
        if len(points) >= frame_limit:
            break
        add(start_seconds + duration * fraction, "interval", "interval_fallback")
    return SamplingPlan(start_seconds, end_seconds, tuple(sorted(points[:frame_limit], key=lambda p: p.timestamp_seconds)), tuple(ignored))


class SamplingFailure(RuntimeError):
    pass


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    process = popen_process_group(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        terminate_process_tree(process)
        raise SamplingFailure("frame_timeout") from exc
    if process.returncode:
        raise SamplingFailure("frame_decode_failed")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


class CandidateFrameSampler:
    """一次分析共用预算/原片哈希；仅在任务 analysis/visual 下写入少量图片。"""

    def __init__(
        self,
        *,
        task_id: str,
        task_dir_name: str,
        source_path: str,
        source_duration_seconds: float,
        max_candidates: int = 20,
        max_bytes: int = 64 * 1024 * 1024,
        timeout_seconds: float = 600,
    ):
        if not math.isfinite(source_duration_seconds) or source_duration_seconds <= 0:
            raise ValueError("原片时长必须为正数")
        if not isinstance(max_candidates, int) or isinstance(max_candidates, bool) or not 1 <= max_candidates <= 20:
            raise ValueError("每轮最多验证 20 个候选")
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= 64 * 1024 * 1024:
            raise ValueError("图片预算必须为 1–64 MiB 范围内的字节数")
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 600:
            raise ValueError("采样整轮时限必须为 0–600 秒")
        valid, message = validate_source_video_path(source_path)
        if not valid:
            raise StorageSafetyError(message)
        self.source = resolve_video_file_path(source_path).resolve()
        self.source_duration = source_duration_seconds
        self.deadline = time.monotonic() + timeout_seconds
        self.source_stat = self._stat()
        # 一轮只做一次完整原片哈希；不能用旧的头尾摘要冒充内容 SHA-256。
        self.source_sha256 = fingerprint_file_full(self.source)
        self._unchanged()
        self._timeout()
        root = get_task_directory(task_id, task_dir_name).resolve()
        self.directory = (root / "analysis" / "visual" / uuid4().hex).resolve()
        if not self.directory.is_relative_to(root):
            raise StorageSafetyError("视觉缓存不能逃逸任务目录")
        self.ffmpeg = ensure_ffmpeg_available()
        self.directory.mkdir(parents=True, exist_ok=False)
        self.remaining_candidates = max_candidates
        self.remaining_bytes = max_bytes

    def _stat(self) -> tuple[int, int, int]:
        stat = self.source.stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def _unchanged(self) -> None:
        if self._stat() != self.source_stat:
            raise SamplingFailure("source_changed")

    def _timeout(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise SamplingFailure("sampling_time_budget_exhausted")
        return min(float(settings.ffmpeg_cover_timeout), remaining)

    def sample(self, plan: SamplingPlan, *, candidate_source_id: str) -> dict:
        # 即使调用方手动构造 dataclass，也不能绕过边界和帧数验证。
        plan_candidate_frames(plan.start_seconds, plan.end_seconds, self.source_duration, frame_limit=len(plan.points))
        if any(not math.isfinite(p.timestamp_seconds) or not plan.start_seconds <= p.timestamp_seconds < plan.end_seconds for p in plan.points):
            raise ValueError("抽帧时间超出候选范围")
        result = {
            "sampler_version": SAMPLER_VERSION,
            "source_sha256": self.source_sha256,
            "candidate_source_id": candidate_source_id,
            "plan": asdict(plan),
            "status": "unavailable",
            "frames": [],
            "skipped": [],
        }
        hashes: set[str] = set()
        if self.remaining_candidates <= 0:
            result["skipped"].append({"reason": "candidate_budget_exhausted"})
            return result
        self.remaining_candidates -= 1
        for point in plan.points:
            target = self.directory / f"{uuid4().hex}.jpg"
            kept = False
            try:
                self._unchanged()
                if self.remaining_bytes <= 0:
                    raise SamplingFailure("image_budget_exhausted")
                # 输入 seek + 有限输入时长；保留全画幅，不套用发布封面裁切。
                command = [self.ffmpeg, "-hide_banner", "-nostdin", "-ss", f"{point.timestamp_seconds:.6f}", "-t", f"{plan.end_seconds - point.timestamp_seconds:.6f}", "-i", str(self.source), "-an", "-frames:v", "1", "-vf", "showinfo,scale=w='min(960,iw)':h='min(960,ih)':force_original_aspect_ratio=decrease", "-q:v", "3", "-fs", str(min(self.remaining_bytes, 2 * 1024 * 1024)), "-y", str(target)]
                completed = _run(command, self._timeout())
                pts = re.search(rb"\bn:\s*0\s+pts:\s*-?\d+\s+pts_time:([-+\d.eE]+)", completed.stderr)
                if not pts:
                    raise SamplingFailure("frame_timestamp_unavailable")
                actual = point.timestamp_seconds + float(pts[1])
                if not math.isfinite(actual) or not plan.start_seconds <= actual < plan.end_seconds:
                    raise SamplingFailure("decoded_frame_outside_candidate")
                data = target.read_bytes()
                if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
                    raise SamplingFailure("invalid_frame_image")
                if len(data) > self.remaining_bytes:
                    raise SamplingFailure("image_budget_exhausted")
                gray = _run([self.ffmpeg, "-v", "error", "-nostdin", "-i", str(target), "-frames:v", "1", "-vf", "scale=16:16", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"], self._timeout()).stdout
                if len(gray) != 256:
                    raise SamplingFailure("invalid_frame_image")
                if sum(value <= 8 for value in gray) >= 254:
                    raise SamplingFailure("black_frame")
                digest = hashlib.sha256(data).hexdigest()
                if digest in hashes:
                    raise SamplingFailure("duplicate_frame")
                self._unchanged()
                hashes.add(digest)
                self.remaining_bytes -= len(data)
                result["frames"].append({**asdict(point), "actual_timestamp_seconds": actual, "file_name": target.name, "sha256": digest, "size_bytes": len(data)})
                kept = True
            except (SamplingFailure, OSError) as exc:
                reason = str(exc) if isinstance(exc, SamplingFailure) else "frame_io_error"
                result["skipped"].append({**asdict(point), "reason": reason})
                if reason == "source_changed":
                    # 不把同一次采样开始前后两个版本的原片混入有效证据。
                    for previous in result["frames"]:
                        (self.directory / previous["file_name"]).unlink(missing_ok=True)
                    result["frames"].clear()
            finally:
                if not kept:
                    target.unlink(missing_ok=True)
        result["status"] = ("partial" if result["skipped"] else "completed") if result["frames"] else "unavailable"
        # 本阶段 manifest 只记录采样；后续视觉 Provider/AI Run 账本另行接入。
        manifest = self.directory / f"{uuid4().hex}.json"
        manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["manifest_file_name"] = manifest.name
        return result
