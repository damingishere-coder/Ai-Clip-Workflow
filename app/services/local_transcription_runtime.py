"""本地 faster-whisper 模型缓存和 Windows CUDA 运行时检查。"""

from __future__ import annotations

import ctypes
import importlib.util
import os
from pathlib import Path
import sys
from typing import Any

from app.core.config import settings
from app.core.transcription_defaults import (
    CPU_FALLBACK_TRANSCRIPTION_MODEL,
    CPU_FALLBACK_TRANSCRIPTION_MODEL_REVISION,
    PRIMARY_TRANSCRIPTION_MODEL,
    PRIMARY_TRANSCRIPTION_MODEL_REVISION,
)


REQUIRED_MODEL_FILES = (
    "config.json",
    "model.bin",
    "tokenizer.json",
)

OPTIONAL_MODEL_FILES = (
    "preprocessor_config.json",
)

MODEL_VOCABULARY_FILES = (
    "vocabulary.json",
    "vocabulary.txt",
)

_DLL_DIRECTORY_HANDLES: list[Any] = []
_DLL_DIRECTORY_PATHS: set[str] = set()
_DLL_LIBRARY_HANDLES: list[Any] = []
_DLL_LIBRARY_PATHS: set[str] = set()


class TranscriptionOfflinePolicyError(RuntimeError):
    """完全离线模式禁止调用远程转写。"""


def ensure_transcription_provider_allowed(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    if settings.transcription_offline_only and normalized != "local":
        raise TranscriptionOfflinePolicyError(
            "已启用完全离线转写，禁止调用火山引擎或其他远程转写服务。"
        )
    return normalized


def model_revision_for(model_name: str) -> str:
    normalized = (model_name or "").strip()
    if normalized == CPU_FALLBACK_TRANSCRIPTION_MODEL:
        return CPU_FALLBACK_TRANSCRIPTION_MODEL_REVISION
    if normalized == PRIMARY_TRANSCRIPTION_MODEL:
        return PRIMARY_TRANSCRIPTION_MODEL_REVISION
    if normalized == settings.transcription_model:
        return settings.transcription_model_revision
    return settings.transcription_model_revision


def model_identity(model_name: str, revision: str | None = None) -> str:
    resolved_revision = (revision or model_revision_for(model_name)).strip()
    suffix = resolved_revision[:8] if resolved_revision else "unversioned"
    return f"{model_name}@{suffix}"


def model_cache_directory(model_name: str, revision: str | None = None) -> Path:
    resolved_revision = (revision or model_revision_for(model_name)).strip()
    safe_name = (model_name or "model").replace("/", "--").replace("\\", "--")
    suffix = resolved_revision[:8] if resolved_revision else "unversioned"
    return Path(settings.transcription_model_cache_dir) / f"{safe_name}-{suffix}"


def model_files_ready(model_path: Path) -> bool:
    return bool(
        model_path.is_dir()
        and all((model_path / name).is_file() for name in REQUIRED_MODEL_FILES)
        and any((model_path / name).is_file() for name in MODEL_VOCABULARY_FILES)
    )


def _package_directory(package_name: str) -> Path | None:
    spec = importlib.util.find_spec(package_name)
    if spec is None:
        return None
    if spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations)))
    if spec.origin:
        return Path(spec.origin).parent
    return None


def configure_windows_cuda_dll_directories() -> dict[str, Any]:
    """仅修改当前 Python 进程的 DLL 搜索路径，不改 Windows 全局 PATH。"""
    if os.name != "nt":
        return {
            "configured": True,
            "cublas_ready": True,
            "cudnn_ready": True,
            "directories": [],
            "errors": [],
        }

    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    cublas_dir = site_packages / "nvidia" / "cublas" / "bin"
    ctranslate2_dir = _package_directory("ctranslate2")
    candidates = [cublas_dir]
    if ctranslate2_dir is not None:
        candidates.append(ctranslate2_dir)

    errors: list[str] = []
    for directory in candidates:
        directory_text = str(directory)
        if not directory.is_dir() or directory_text in _DLL_DIRECTORY_PATHS:
            continue
        try:
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(directory_text))
            _DLL_DIRECTORY_PATHS.add(directory_text)
        except (FileNotFoundError, OSError) as exc:
            errors.append(f"{directory}: {exc}")

    library_paths = [
        cublas_dir / "cublasLt64_12.dll",
        cublas_dir / "cublas64_12.dll",
    ]
    if ctranslate2_dir is not None:
        library_paths.append(ctranslate2_dir / "cudnn64_9.dll")
    library_errors: dict[str, str] = {}
    for library_path in library_paths:
        library_text = str(library_path)
        if not library_path.is_file():
            library_errors[library_path.name] = "文件不存在"
            continue
        if library_text in _DLL_LIBRARY_PATHS:
            continue
        try:
            _DLL_LIBRARY_HANDLES.append(ctypes.WinDLL(library_text))
            _DLL_LIBRARY_PATHS.add(library_text)
        except OSError as exc:
            library_errors[library_path.name] = str(exc)
            errors.append(f"{library_path}: {exc}")

    return {
        "configured": not errors,
        "cublas_ready": all(
            path.is_file() and path.name not in library_errors
            for path in library_paths[:2]
        ),
        "cudnn_ready": bool(
            ctranslate2_dir
            and library_paths[-1].is_file()
            and library_paths[-1].name not in library_errors
        ),
        "directories": [str(path) for path in candidates if path.is_dir()],
        "libraries": [str(path) for path in library_paths if path.is_file()],
        "library_errors": library_errors,
        "errors": errors,
    }


def resolve_local_model_source(model_name: str, revision: str | None = None) -> str:
    model_path = model_cache_directory(model_name, revision)
    if model_files_ready(model_path):
        return str(model_path)
    raise RuntimeError(
        "本地转写模型尚未初始化或缓存不完整："
        f"{model_path}。任务运行时不会联网下载，请先运行 scripts/setup_local_transcription.ps1。"
    )


def get_local_transcription_runtime_status() -> dict[str, Any]:
    dll_status = configure_windows_cuda_dll_directories()
    model_path = model_cache_directory(
        settings.transcription_model,
        settings.transcription_model_revision,
    )
    model_ready = model_files_ready(model_path)
    cuda_device_count = 0
    cuda_error = ""
    try:
        import ctranslate2

        cuda_device_count = int(ctranslate2.get_cuda_device_count())
    except Exception as exc:
        cuda_error = str(exc)

    gpu_ready = bool(
        cuda_device_count > 0
        and dll_status["cublas_ready"]
        and dll_status["cudnn_ready"]
    )
    errors = list(dll_status["errors"])
    if cuda_error:
        errors.append(cuda_error)
    if not model_ready:
        errors.append("固定版本模型尚未完整缓存")

    return {
        "offline_only": bool(settings.transcription_offline_only),
        "local_files_only": bool(settings.transcription_local_files_only),
        "provider": settings.transcription_provider,
        "model": settings.transcription_model,
        "model_revision": settings.transcription_model_revision,
        "model_identity": model_identity(
            settings.transcription_model,
            settings.transcription_model_revision,
        ),
        "model_cache_dir": str(settings.transcription_model_cache_dir),
        "model_path": str(model_path),
        "model_ready": model_ready,
        "gpu_ready": gpu_ready,
        "cuda_device_count": cuda_device_count,
        "cublas_ready": bool(dll_status["cublas_ready"]),
        "cudnn_ready": bool(dll_status["cudnn_ready"]),
        "device": settings.transcription_device,
        "compute_type": settings.transcription_compute_type,
        "external_cost": "0 元",
        "external_cost_yuan": 0,
        "ready": bool(
            settings.transcription_provider == "local"
            and model_ready
            and (
                settings.transcription_device != "cuda"
                or gpu_ready
            )
        ),
        "errors": errors,
    }


__all__ = [
    "PRIMARY_TRANSCRIPTION_MODEL",
    "PRIMARY_TRANSCRIPTION_MODEL_REVISION",
    "MODEL_VOCABULARY_FILES",
    "OPTIONAL_MODEL_FILES",
    "REQUIRED_MODEL_FILES",
    "TranscriptionOfflinePolicyError",
    "configure_windows_cuda_dll_directories",
    "ensure_transcription_provider_allowed",
    "get_local_transcription_runtime_status",
    "model_cache_directory",
    "model_files_ready",
    "model_identity",
    "model_revision_for",
    "resolve_local_model_source",
]
