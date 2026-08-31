from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import Request, getproxies, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.transcription_defaults import (  # noqa: E402
    CPU_FALLBACK_TRANSCRIPTION_MODEL,
    CPU_FALLBACK_TRANSCRIPTION_MODEL_BIN_SHA256,
    CPU_FALLBACK_TRANSCRIPTION_MODEL_BIN_SIZE,
    CPU_FALLBACK_TRANSCRIPTION_MODEL_REPOSITORY,
    CPU_FALLBACK_TRANSCRIPTION_MODEL_REVISION,
    PRIMARY_TRANSCRIPTION_MODEL,
    PRIMARY_TRANSCRIPTION_MODEL_BIN_SHA256,
    PRIMARY_TRANSCRIPTION_MODEL_BIN_SIZE,
    PRIMARY_TRANSCRIPTION_MODEL_REPOSITORY,
    PRIMARY_TRANSCRIPTION_MODEL_REVISION,
)
from app.services.local_transcription_runtime import (  # noqa: E402
    MODEL_VOCABULARY_FILES,
    OPTIONAL_MODEL_FILES,
    REQUIRED_MODEL_FILES,
    configure_windows_cuda_dll_directories,
    model_cache_directory,
    model_files_ready,
)


MODEL_SPECS = (
    (
        PRIMARY_TRANSCRIPTION_MODEL,
        PRIMARY_TRANSCRIPTION_MODEL_REPOSITORY,
        PRIMARY_TRANSCRIPTION_MODEL_REVISION,
        PRIMARY_TRANSCRIPTION_MODEL_BIN_SIZE,
        PRIMARY_TRANSCRIPTION_MODEL_BIN_SHA256,
        "GPU 主模型",
    ),
    (
        CPU_FALLBACK_TRANSCRIPTION_MODEL,
        CPU_FALLBACK_TRANSCRIPTION_MODEL_REPOSITORY,
        CPU_FALLBACK_TRANSCRIPTION_MODEL_REVISION,
        CPU_FALLBACK_TRANSCRIPTION_MODEL_BIN_SIZE,
        CPU_FALLBACK_TRANSCRIPTION_MODEL_BIN_SHA256,
        "CPU 兜底模型",
    ),
)
_MODEL_DOWNLOAD_ATTEMPTS = 20
_CURL_DOWNLOAD_ATTEMPTS = 3
_MODEL_BIN_PART_SIZE = 64 * 1024 * 1024
_MODEL_BIN_DOWNLOAD_WORKERS = 8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化 NiuMa Studio 完全离线转写模型。")
    parser.add_argument("--cache-dir", default=str(settings.transcription_model_cache_dir))
    parser.add_argument("--audio", default="", help="可选：真实音频路径，用于 GPU 推理冒烟。")
    parser.add_argument("--seconds", type=int, default=20, help="真实音频冒烟截取秒数。")
    return parser.parse_args()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_model_bin_part(url: str, target: Path, start: int, end: int) -> int:
    if shutil.which("curl"):
        try:
            return _download_model_bin_part_with_curl(url, target, start, end)
        except Exception as exc:
            print(
                f"官方模型分片 curl 连续失败，切换 Python Range 续传：{start}-{end}；{exc}",
                flush=True,
            )
    return _download_model_bin_part_with_urllib(url, target, start, end)


def _append_download_tail(target: Path, tail: Path, expected_size: int) -> None:
    if not tail.exists():
        return
    current_size = target.stat().st_size if target.exists() else 0
    if current_size + tail.stat().st_size > expected_size:
        tail.unlink()
        raise RuntimeError(f"官方模型分片响应超过预期大小：{target.name}")
    with target.open("ab" if current_size else "wb") as output, tail.open("rb") as source:
        shutil.copyfileobj(source, output, length=1024 * 1024)
    tail.unlink()


def _download_model_bin_part_with_curl(url: str, target: Path, start: int, end: int) -> int:
    expected_size = end - start + 1
    tail = target.with_suffix(".download")
    _append_download_tail(target, tail, expected_size)
    proxy = getproxies().get("https") or getproxies().get("http") or ""
    child_environment = os.environ.copy()
    if proxy:
        child_environment["HTTPS_PROXY"] = proxy
    for attempt in range(1, _CURL_DOWNLOAD_ATTEMPTS + 1):
        current_size = target.stat().st_size if target.exists() else 0
        if current_size == expected_size:
            return expected_size
        if current_size > expected_size:
            target.unlink()
            current_size = 0

        request_start = start + current_size
        command = [
            "curl",
            "--location",
            "--fail",
            "--silent",
            "--show-error",
            "--connect-timeout",
            "20",
            "--max-time",
            "60",
            "--range",
            f"{request_start}-{end}",
            "--output",
            str(tail),
        ]
        command.append(url)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=child_environment,
        )
        _append_download_tail(target, tail, expected_size)
        updated_size = target.stat().st_size if target.exists() else 0
        if updated_size == expected_size:
            return expected_size
        if attempt >= _CURL_DOWNLOAD_ATTEMPTS:
            raise RuntimeError(
                f"官方模型分片 curl 下载失败：{start}-{end}；"
                f"exit={result.returncode}，error={result.stderr.strip()[:300]}"
            )
        time.sleep(min(20, attempt * 3))
    raise RuntimeError(f"官方模型分片 curl 下载失败：{start}-{end}")


def _download_model_bin_part_with_urllib(url: str, target: Path, start: int, end: int) -> int:
    expected_size = end - start + 1
    for attempt in range(1, _MODEL_DOWNLOAD_ATTEMPTS + 1):
        current_size = target.stat().st_size if target.exists() else 0
        if current_size == expected_size:
            return expected_size
        if current_size > expected_size:
            target.unlink()
            current_size = 0

        request_start = start + current_size
        request = Request(
            url,
            headers={
                "Range": f"bytes={request_start}-{end}",
                "User-Agent": "NiuMa-Studio-offline-transcription-setup/1.0",
            },
        )
        try:
            with urlopen(request, timeout=120) as response:
                if getattr(response, "status", 0) != 206:
                    raise RuntimeError(f"官方模型分片接口未返回 206：{getattr(response, 'status', 0)}")
                with target.open("ab" if current_size else "wb") as output:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
        except Exception:
            if attempt >= _MODEL_DOWNLOAD_ATTEMPTS:
                raise
            time.sleep(min(20, attempt * 3))
            continue

        if target.stat().st_size == expected_size:
            return expected_size
    raise RuntimeError(f"官方模型分片下载失败：{start}-{end}")


def _download_verified_model_bin(
    target_dir: Path,
    repository: str,
    revision: str,
    expected_size: int,
    expected_sha256: str,
    label: str,
) -> Path:
    target = target_dir / "model.bin"
    if target.is_file():
        if target.stat().st_size != expected_size:
            raise RuntimeError(f"{label} 已有 model.bin 大小异常，请人工移走后重新初始化：{target}")
        if _sha256_file(target) != expected_sha256:
            raise RuntimeError(f"{label} 已有 model.bin SHA-256 不一致，请人工检查：{target}")
        print(f"[{label}] model.bin 已存在且 SHA-256 校验通过")
        return target

    part_dir = target_dir / ".model-bin-parts"
    part_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/{repository}/resolve/{revision}/model.bin?download=true"
    ranges: list[tuple[int, int, Path]] = []
    for index, start in enumerate(range(0, expected_size, _MODEL_BIN_PART_SIZE)):
        end = min(expected_size - 1, start + _MODEL_BIN_PART_SIZE - 1)
        ranges.append((start, end, part_dir / f"part-{index:04d}.bin"))

    completed_bytes = sum(
        end - start + 1
        for start, end, part in ranges
        if part.is_file() and part.stat().st_size == end - start + 1
    )
    print(
        f"[{label}] 从 Hugging Face 官方固定 revision 下载 model.bin："
        f"{completed_bytes}/{expected_size} 字节已缓存",
        flush=True,
    )
    failures: list[Exception] = []
    with ThreadPoolExecutor(max_workers=min(_MODEL_BIN_DOWNLOAD_WORKERS, len(ranges))) as executor:
        future_to_range = {
            executor.submit(_download_model_bin_part, url, part, start, end): (start, end)
            for start, end, part in ranges
        }
        finished_parts = 0
        for future in as_completed(future_to_range):
            try:
                future.result()
            except Exception as exc:
                failures.append(exc)
                print(f"[{label}] 一个分片仍未完成，其他分片继续：{exc}", flush=True)
            else:
                finished_parts += 1
                print(
                    f"[{label}] 官方 model.bin 分片完成：{finished_parts}/{len(ranges)}",
                    flush=True,
                )
    if failures:
        raise RuntimeError(
            f"{label} 仍有 {len(failures)} 个分片未完成；已保留全部分片，下次运行只补缺失字节。"
        ) from failures[0]

    temporary = target.with_suffix(".bin.tmp")
    digest = hashlib.sha256()
    total_size = 0
    with temporary.open("wb") as output:
        for _start, _end, part in ranges:
            with part.open("rb") as source:
                while True:
                    block = source.read(4 * 1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    digest.update(block)
                    total_size += len(block)
    actual_sha256 = digest.hexdigest()
    if total_size != expected_size or actual_sha256 != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"{label} model.bin 完整性校验失败：size={total_size}/{expected_size}，"
            f"sha256={actual_sha256}/{expected_sha256}"
        )
    temporary.replace(target)
    for _start, _end, part in ranges:
        part.unlink(missing_ok=True)
    try:
        part_dir.rmdir()
    except OSError:
        pass
    print(f"[{label}] model.bin 官方 SHA-256 校验通过：{actual_sha256}")
    return target


def download_model(
    model_name: str,
    repository: str,
    revision: str,
    model_bin_size: int,
    model_bin_sha256: str,
    label: str,
) -> Path:
    from huggingface_hub import snapshot_download

    target = model_cache_directory(model_name, revision)
    target.mkdir(parents=True, exist_ok=True)
    print(f"[{label}] 固定版本：{repository}@{revision}")
    print(f"[{label}] 本地目录：{target}")
    for attempt in range(1, _MODEL_DOWNLOAD_ATTEMPTS + 1):
        try:
            snapshot_download(
                repo_id=repository,
                revision=revision,
                local_dir=target,
                allow_patterns=[
                    name
                    for name in (*REQUIRED_MODEL_FILES, *MODEL_VOCABULARY_FILES, *OPTIONAL_MODEL_FILES)
                    if name != "model.bin"
                ],
                max_workers=2,
            )
            break
        except Exception as exc:
            if attempt >= _MODEL_DOWNLOAD_ATTEMPTS:
                raise RuntimeError(
                    f"{label} 固定版本下载连续 {_MODEL_DOWNLOAD_ATTEMPTS} 次失败，"
                    "已保留本地缓存，可稍后重新运行脚本继续。"
                ) from exc
            delay_seconds = min(30, attempt * 5)
            print(
                f"[{label}] 第 {attempt}/{_MODEL_DOWNLOAD_ATTEMPTS} 次下载中断：{exc}。"
                f"保留缓存，{delay_seconds} 秒后重试。",
                flush=True,
            )
            time.sleep(delay_seconds)
    _download_verified_model_bin(
        target,
        repository,
        revision,
        model_bin_size,
        model_bin_sha256,
        label,
    )
    if not model_files_ready(target):
        missing = [name for name in REQUIRED_MODEL_FILES if not (target / name).is_file()]
        if not any((target / name).is_file() for name in MODEL_VOCABULARY_FILES):
            missing.append("vocabulary.json 或 vocabulary.txt")
        raise RuntimeError(f"{label} 缓存不完整，缺少：{', '.join(missing)}")
    return target


def write_manifest(cache_dir: Path, model_paths: dict[str, Path]) -> Path:
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": [
            {
                "name": model_name,
                "repository": repository,
                "revision": revision,
                "model_bin_size": model_bin_size,
                "model_bin_sha256": model_bin_sha256,
                "path": str(model_paths[model_name]),
            }
            for model_name, repository, revision, model_bin_size, model_bin_sha256, _label in MODEL_SPECS
        ],
    }
    target = cache_dir / "offline-transcription-manifest.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def extract_smoke_audio(source: Path, target: Path, seconds: int) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-t",
        str(max(1, seconds)),
        "-ar",
        "16000",
        "-ac",
        "1",
        str(target),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "FFmpeg 冒烟音频截取失败")


def verify_gpu_model(primary_path: Path, audio_path: Path | None, seconds: int) -> None:
    dll_status = configure_windows_cuda_dll_directories()
    if not dll_status["cublas_ready"] or not dll_status["cudnn_ready"]:
        raise RuntimeError(
            "Windows GPU 运行库未就绪："
            f"cuBLAS={dll_status['cublas_ready']}，cuDNN={dll_status['cudnn_ready']}"
        )

    from faster_whisper import WhisperModel

    model = WhisperModel(str(primary_path), device="cuda", compute_type="float16")
    print("GPU 主模型加载成功：large-v3 / cuda / float16")
    if audio_path is None:
        print("未提供 --audio，本次只验证模型加载，未执行真实语音推理。")
        return

    if not audio_path.is_file():
        raise RuntimeError(f"真实冒烟音频不存在：{audio_path}")
    with TemporaryDirectory(prefix="niuma_local_asr_smoke_") as temp_dir:
        clip_path = Path(temp_dir) / "smoke.wav"
        extract_smoke_audio(audio_path, clip_path, seconds)
        segments, _info = model.transcribe(
            str(clip_path),
            language="zh",
            vad_filter=True,
            beam_size=5,
            word_timestamps=True,
        )
        results = list(segments)
    if not results:
        raise RuntimeError("真实 GPU 冒烟完成，但没有识别到语音")
    print(f"真实 GPU 冒烟成功：识别到 {len(results)} 句")
    for segment in results[:5]:
        print(f"{segment.start:.1f}-{segment.end:.1f}: {segment.text.strip()}")


def verify_cpu_fallback_model(fallback_path: Path) -> None:
    from faster_whisper import WhisperModel

    WhisperModel(str(fallback_path), device="cpu", compute_type="int8")
    print("CPU 兜底模型加载成功：medium / cpu / int8")


def main() -> None:
    args = parse_args()
    cache_dir = Path(args.cache_dir).expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    object.__setattr__(settings, "transcription_model_cache_dir", cache_dir)

    model_paths: dict[str, Path] = {}
    for model_name, repository, revision, model_bin_size, model_bin_sha256, label in MODEL_SPECS:
        model_paths[model_name] = download_model(
            model_name,
            repository,
            revision,
            model_bin_size,
            model_bin_sha256,
            label,
        )

    manifest_path = write_manifest(cache_dir, model_paths)
    verify_gpu_model(
        model_paths[PRIMARY_TRANSCRIPTION_MODEL],
        Path(args.audio).expanduser().resolve() if args.audio else None,
        args.seconds,
    )
    verify_cpu_fallback_model(model_paths[CPU_FALLBACK_TRANSCRIPTION_MODEL])
    print(f"离线模型清单：{manifest_path}")
    print("初始化完成。运行时可断网加载，不会上传音频。")


if __name__ == "__main__":
    main()
