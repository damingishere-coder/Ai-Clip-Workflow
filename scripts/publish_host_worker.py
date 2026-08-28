"""牛马片场 Windows 浏览器发布 Worker。

该进程必须运行在安装了 Google Chrome 的 Windows 主机上。FastAPI 调度器通过带
Bearer Token 的本地 HTTP 接口调用它，Docker 容器自身不接触宿主浏览器。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, ValidationInfo, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.publish_time import utc_now_iso  # noqa: E402
from app.services.publishers.base import (  # noqa: E402
    PublishError,
    PublishNeedsReview,
    PublishOutcome,
    PublishResult,
    PublishValidationError,
    sanitize_provider_response,
)
from app.services.publishers.browser_runtime import BrowserRuntime  # noqa: E402
from app.services.publishers.registry import get_platform_publisher  # noqa: E402
from app.services.publishers.worker_client import validate_worker_identifier  # noqa: E402


logger = logging.getLogger(__name__)


class AccountRequest(BaseModel):
    platform: str = Field(pattern="^(douyin|bilibili)$")
    account_id: str = Field(min_length=1, max_length=120)

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, value: str) -> str:
        return validate_worker_identifier(value, "account_id", max_length=120)


class PublishRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=160)
    execution_id: str = Field(min_length=1, max_length=160)
    platform: str = Field(pattern="^(douyin|bilibili)$")
    account_id: str = Field(min_length=1, max_length=120)
    task_id: str = ""
    clip_id: str = ""
    scheduled_at: str = ""
    title: str
    caption: str
    hashtags: str = ""
    video_path: str
    cover_file_path: str = ""
    visibility: str = "public"
    allow_download: bool = True
    bilibili_tid: str = ""
    bilibili_copyright: str = "original"
    bilibili_source: str = ""
    publisher: str = "local_browser"

    @field_validator("job_id", "execution_id", "account_id")
    @classmethod
    def validate_identifiers(cls, value: str, info: ValidationInfo) -> str:
        max_length = 120 if info.field_name == "account_id" else 160
        return validate_worker_identifier(value, str(info.field_name), max_length=max_length)


class OpenCliRunRequest(BaseModel):
    command: list[str]
    timeout: int = Field(default=600, ge=1, le=1800)


class _LockLease(str):
    """兼容原 token 字符串，同时持有由操作系统管理的文件锁句柄。"""

    def __new__(cls, value: str, handle: Any):
        lease = super().__new__(cls, value)
        lease.handle = handle
        return lease


def _try_create_lock_file(path: Path) -> _LockLease | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}:{uuid4().hex}"
    try:
        path.touch(exist_ok=True)
        handle = path.open("r+b")
    except OSError as exc:
        raise PublishValidationError(
            f"无法打开 Worker 独占锁：{path.name}", "worker_lock_unavailable"
        ) from exc
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    try:
        handle.seek(0)
        handle.write(token.encode("utf-8"))
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    except OSError as exc:
        lease = _LockLease(token, handle)
        _release_lock_file(path, lease)
        raise PublishValidationError(
            f"无法写入 Worker 独占锁：{path.name}", "worker_lock_unavailable"
        ) from exc
    return _LockLease(token, handle)


def _lock_file_is_active(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        lease = _try_create_lock_file(path)
        if lease is None:
            return True
        _release_lock_file(path, lease)
        return False
    except PublishValidationError:
        logger.exception("检查 Worker 独占锁失败：%s", path.name)
        return True


def _release_lock_file(path: Path, token: _LockLease | str | None) -> None:
    if not token:
        return
    if not isinstance(token, _LockLease):
        logger.error("拒绝在缺少操作系统锁句柄时删除锁文件：%s", path.name)
        return
    handle = token.handle
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        logger.exception("释放 Worker 操作系统锁失败：%s", path.name)
    finally:
        handle.close()


class _ReentrantExecutionLock:
    """进程内可重入 + 跨进程 O_EXCL，崩溃残留时 fail closed。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    @property
    def acquired(self) -> bool:
        return bool(getattr(self._local, "acquired", False))

    def __enter__(self) -> "_ReentrantExecutionLock":
        self._thread_lock.acquire()
        depth = int(getattr(self._local, "depth", 0))
        if depth == 0:
            try:
                self._local.token = _try_create_lock_file(self.path)
                self._local.acquired = bool(self._local.token)
            except Exception:
                self._thread_lock.release()
                raise
        self._local.depth = depth + 1
        return self

    def __exit__(self, *_args: object) -> None:
        depth = int(getattr(self._local, "depth", 1)) - 1
        self._local.depth = depth
        if depth == 0:
            _release_lock_file(self.path, getattr(self._local, "token", None))
            self._local.token = None
            self._local.acquired = False
        self._thread_lock.release()


_EXECUTION_LOCKS: weakref.WeakValueDictionary[str, _ReentrantExecutionLock] = (
    weakref.WeakValueDictionary()
)
_EXECUTION_LOCKS_GUARD = threading.Lock()


def _execution_lock(execution_id: str) -> _ReentrantExecutionLock:
    state_root = Path(settings.publish_worker_state_dir).resolve()
    key = f"{state_root}:{execution_id}"
    with _EXECUTION_LOCKS_GUARD:
        return _EXECUTION_LOCKS.setdefault(
            key,
            _ReentrantExecutionLock(state_root / "locks" / "executions" / f"{execution_id}.lock"),
        )


class ExecutionJournal:
    def __init__(self, execution_id: str) -> None:
        self.execution_id = validate_worker_identifier(
            execution_id, "execution_id", max_length=160
        )
        self.root = Path(settings.publish_worker_state_dir) / "executions"
        self.path = self.root / f"{self.execution_id}.json"
        self.lock = _execution_lock(self.execution_id)
        self.root.mkdir(parents=True, exist_ok=True)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"execution_id": self.execution_id, "phase": "unknown"}
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"execution_id": self.execution_id, "phase": "corrupt", "corrupt": True}
        if not isinstance(parsed, dict):
            return {"execution_id": self.execution_id, "phase": "corrupt", "corrupt": True}
        if parsed.get("execution_id") != self.execution_id or not isinstance(parsed.get("phase"), str):
            return {"execution_id": self.execution_id, "phase": "corrupt", "corrupt": True}
        identity = parsed.get("identity")
        if identity is not None:
            expected_keys = {"job_id", "platform", "account_id"}
            if (
                not isinstance(identity, dict)
                or set(identity) != expected_keys
                or not all(isinstance(identity.get(key), str) and identity.get(key) for key in expected_keys)
            ):
                return {"execution_id": self.execution_id, "phase": "corrupt", "corrupt": True}
        return parsed

    def update(
        self,
        phase: str,
        details: dict[str, Any] | None = None,
        *,
        identity: dict[str, str] | None = None,
    ) -> None:
        with self.lock:
            if not self.lock.acquired:
                raise PublishValidationError(
                    "相同 execution_id 正在另一 Worker 进程中执行",
                    "execution_in_progress",
                )
            current = self.read()
            if current.get("corrupt"):
                raise PublishValidationError(
                    "执行日志已损坏，为避免重复投稿已停止执行",
                    "execution_journal_corrupt",
                )
            current.update({
                "execution_id": self.execution_id,
                "phase": phase,
                "updated_at": utc_now_iso(),
            })
            if phase == "upload_started" or current.get("upload_started") is True:
                current["upload_started"] = True
            if identity:
                current["identity"] = dict(identity)
            if details:
                current["details"] = sanitize_provider_response(details)
            temporary = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)


def _prior_job_execution_requires_review(
    job_id: str,
    current_execution_id: str,
) -> tuple[str, str] | None:
    """查找同一发布任务已进入不可安全重试阶段的旧 execution。"""

    safe_retry_phases = {
        "unknown",
        "received",
        "browser_opening",
        "browser_opened",
        "rejected",
        "failed",
    }
    root = Path(settings.publish_worker_state_dir) / "executions"
    if not root.exists():
        return None
    for path in root.glob("*.json"):
        if path.stem == current_execution_id:
            continue
        try:
            state = ExecutionJournal(path.stem).read()
        except PublishValidationError:
            continue
        identity = state.get("identity")
        if not isinstance(identity, dict) or identity.get("job_id") != job_id:
            continue
        phase = str(state.get("phase") or "unknown")
        if state.get("upload_started") is True or phase not in safe_retry_phases:
            return path.stem, phase
    return None


class _AccountOperationLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._thread_lock = threading.Lock()
        self._token: str | None = None

    def acquire(self, blocking: bool = True) -> bool:
        if not self._thread_lock.acquire(blocking=blocking):
            return False
        try:
            self._token = _try_create_lock_file(self.path)
        except Exception:
            self._thread_lock.release()
            raise
        if not self._token:
            self._thread_lock.release()
            return False
        return True

    def release(self) -> None:
        _release_lock_file(self.path, self._token)
        self._token = None
        self._thread_lock.release()


_ACCOUNT_LOCKS: weakref.WeakValueDictionary[str, _AccountOperationLock] = (
    weakref.WeakValueDictionary()
)
_JOB_LOCKS: weakref.WeakValueDictionary[str, _AccountOperationLock] = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()


def _account_lock(platform: str, account_id: str) -> _AccountOperationLock:
    state_root = Path(settings.publish_worker_state_dir).resolve()
    key = f"{state_root}:{platform}:{account_id}"
    with _LOCKS_GUARD:
        return _ACCOUNT_LOCKS.setdefault(
            key,
            _AccountOperationLock(
                state_root / "locks" / "accounts" / f"{platform}--{account_id}.lock"
            ),
        )


def _job_lock(job_id: str) -> _AccountOperationLock:
    safe_job_id = validate_worker_identifier(job_id, "job_id", max_length=160)
    state_root = Path(settings.publish_worker_state_dir).resolve()
    key = f"{state_root}:{safe_job_id}"
    with _LOCKS_GUARD:
        return _JOB_LOCKS.setdefault(
            key,
            _AccountOperationLock(state_root / "locks" / "jobs" / f"{safe_job_id}.lock"),
        )


def _allowed_roots() -> list[Path]:
    roots = [
        Path(settings.publish_host_project_root),
        Path(settings.tasks_dir),
        Path(settings.data_dir),
    ]
    configured = str(settings.publish_worker_allowed_roots or "")
    roots.extend(Path(item.strip()).expanduser() for item in configured.split(os.pathsep) if item.strip())
    return [root.resolve() for root in roots if str(root)]


def _resolve_media_path(raw_value: str, *, required: bool) -> str:
    text = str(raw_value or "").strip()
    if not text:
        if required:
            raise PublishValidationError("媒体文件路径不能为空", "missing_media_path")
        return ""
    path = Path(text).expanduser()
    normalized = text.replace("\\", "/")
    if not path.exists() and normalized.startswith("/workspace/tasks/"):
        relative = normalized[len("/workspace/tasks/"):]
        path = Path(settings.tasks_dir) / Path(relative)
    elif not path.exists() and normalized.startswith("/app/"):
        relative = normalized[len("/app/"):]
        path = Path(settings.publish_host_project_root) / Path(relative)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PublishValidationError(f"媒体文件不存在：{text}", "media_not_found") from exc
    if not resolved.is_file():
        raise PublishValidationError(f"媒体路径不是文件：{text}", "invalid_media_path")
    allowed = any(resolved == root or root in resolved.parents for root in _allowed_roots())
    if not allowed:
        raise PublishValidationError("媒体文件不在 Worker 允许目录内", "media_path_not_allowed")
    return str(resolved)


def create_worker_app(token: str | None = None) -> FastAPI:
    worker_token = str(token if token is not None else settings.publish_worker_token)
    worker = FastAPI(title="NiuMa Studio Publish Worker", version="2.1.0")

    def require_token(authorization: str = Header(default="")) -> None:
        if not worker_token:
            raise HTTPException(status_code=503, detail="Worker 未配置 PUBLISH_WORKER_TOKEN")
        if authorization != f"Bearer {worker_token}":
            raise HTTPException(status_code=401, detail="Worker Token 无效")

    def health_payload() -> dict[str, Any]:
        from scripts.opencli_host_bridge import _opencli_executable

        opencli_executable = _opencli_executable() if settings.publish_enable_opencli_fallback else None
        return {
            "status": "ok",
            "worker": "windows_chrome",
            "browser_channel": settings.publish_browser_channel,
            "timezone": settings.app_timezone,
            "token_configured": bool(worker_token),
            "opencli_available": bool(opencli_executable),
            "opencli_executable": opencli_executable or "",
            "message": "Windows 发布 Worker 已启动",
        }

    @worker.get("/health")
    def health() -> dict[str, Any]:
        """供 Windows 本机启动脚本使用的公开健康检查。"""
        return health_payload()

    @worker.get("/v1/health", dependencies=[Depends(require_token)])
    def protected_health() -> dict[str, Any]:
        """供 Docker 调度器使用，同时验证 Worker Token。"""
        return health_payload()

    @worker.post("/run", dependencies=[Depends(require_token)])
    def run_opencli_compat(payload: OpenCliRunRequest) -> dict[str, Any]:
        if not settings.publish_enable_opencli_fallback:
            raise HTTPException(status_code=403, detail="opencli 兼容模式未开启")
        if not payload.command or Path(payload.command[0]).name.lower() not in {
            "opencli", "opencli.cmd", "opencli.exe", "opencli.ps1"
        }:
            raise HTTPException(status_code=400, detail="兼容接口只允许执行 opencli 命令")
        from scripts.opencli_host_bridge import _normalize_command

        command = _normalize_command(payload.command)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=payload.timeout,
            )
            return {
                "status": "ok", "returncode": result.returncode,
                "stdout": result.stdout or "", "stderr": result.stderr or "",
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "status": "timeout", "returncode": 124,
                "stdout": exc.stdout or "", "stderr": exc.stderr or "opencli 命令超时",
            }

    @worker.post("/v1/accounts/check", dependencies=[Depends(require_token)])
    def check_account(payload: AccountRequest) -> dict[str, Any]:
        lock = _account_lock(payload.platform, payload.account_id)
        if not lock.acquire(blocking=False):
            return {"login_status": "busy", "message": "该账号正在执行浏览器操作"}
        try:
            runtime = BrowserRuntime(payload.platform, payload.account_id)
            publisher = get_platform_publisher(
                payload.platform, runtime=runtime, account_id=payload.account_id
            )
            result = publisher.check_login(payload.account_id)
            return result
        except PublishError as exc:
            return {"login_status": "login_required", "message": exc.message, "error_code": exc.error_code}
        finally:
            lock.release()

    def login_background(payload: AccountRequest, lock: _AccountOperationLock) -> None:
        try:
            runtime = BrowserRuntime(payload.platform, payload.account_id)
            publisher = get_platform_publisher(
                payload.platform, runtime=runtime, account_id=payload.account_id
            )
            publisher.open_login(payload.account_id)
        except Exception:
            # Worker 只负责宿主浏览器操作，不直接写 Docker 挂载的 SQLite。
            # 登录结果由 FastAPI 后续通过账号检测接口统一落库。
            logger.exception(
                "打开平台登录窗口失败：platform=%s account_id=%s",
                payload.platform,
                payload.account_id,
            )
        finally:
            lock.release()

    @worker.post("/v1/accounts/login", dependencies=[Depends(require_token)], status_code=202)
    def login_account(payload: AccountRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
        lock = _account_lock(payload.platform, payload.account_id)
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="该账号已有浏览器窗口正在运行")
        background_tasks.add_task(login_background, payload, lock)
        return {"status": "started", "message": "已打开独立 Chrome，请在窗口中完成平台登录"}

    @worker.post("/v1/accounts/open-center", dependencies=[Depends(require_token)], status_code=202)
    def open_center(payload: AccountRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
        lock = _account_lock(payload.platform, payload.account_id)
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="该账号已有浏览器窗口正在运行")
        background_tasks.add_task(login_background, payload, lock)
        return {"status": "started", "message": "已打开平台创作者中心"}

    def publish_with_job_lock(payload: PublishRequest) -> dict[str, Any]:
        journal = ExecutionJournal(payload.execution_id)
        identity = {
            "job_id": payload.job_id,
            "platform": payload.platform,
            "account_id": payload.account_id,
        }
        terminal_phases = {"confirmed_success", "exported", "failed", "manual_review"}
        terminal_outcomes = {
            "confirmed_success": PublishOutcome.PUBLISHED.value,
            "exported": PublishOutcome.EXPORTED.value,
            "failed": PublishOutcome.FAILED.value,
            "manual_review": PublishOutcome.NEED_REVIEW.value,
        }
        safe_resume_phases = {"unknown", "received", "browser_opening", "browser_opened", "rejected"}

        def saved_terminal_result(phase: str, saved: Any) -> dict[str, Any] | None:
            required_fields = {
                "outcome", "message", "remote_video_id", "platform_url", "published_at",
                "provider_response", "error_code", "needs_manual_review",
            }
            if (
                phase not in terminal_outcomes
                or not isinstance(saved, dict)
                or not required_fields.issubset(saved)
                or saved.get("outcome") != terminal_outcomes[phase]
                or not isinstance(saved.get("provider_response"), dict)
                or not isinstance(saved.get("needs_manual_review"), bool)
                or (phase == "confirmed_success" and not str(saved.get("published_at") or ""))
                or (phase == "manual_review" and saved.get("needs_manual_review") is not True)
                or (phase != "manual_review" and saved.get("needs_manual_review") is not False)
            ):
                return None
            return sanitize_provider_response(saved)

        with journal.lock:
            current = journal.read()
            phase = str(current.get("phase") or "unknown")
            existing_identity = current.get("identity")
            if current.get("corrupt"):
                return PublishResult(
                    outcome=PublishOutcome.NEED_REVIEW,
                    message="Worker 执行日志已损坏，为避免重复投稿已停止执行",
                    error_code="execution_journal_corrupt",
                    needs_manual_review=True,
                ).as_dict()
            if isinstance(existing_identity, dict) and existing_identity != identity:
                return PublishResult(
                    outcome=PublishOutcome.NEED_REVIEW,
                    message="execution_id 已属于另一发布任务，已拒绝重复执行",
                    error_code="execution_identity_conflict",
                    needs_manual_review=True,
                ).as_dict()
            if journal.path.exists() and not isinstance(existing_identity, dict):
                return PublishResult(
                    outcome=PublishOutcome.NEED_REVIEW,
                    message="旧版 Worker 日志缺少执行身份，为避免重复投稿已停止执行",
                    error_code="execution_identity_missing",
                    needs_manual_review=True,
                ).as_dict()
            if not journal.lock.acquired:
                saved = saved_terminal_result(phase, current.get("details"))
                if saved is not None:
                    return saved
                return PublishResult(
                    outcome=PublishOutcome.NEED_REVIEW,
                    message="相同 execution_id 正在另一 Worker 进程执行，已拒绝重复投稿",
                    error_code="execution_in_progress",
                    needs_manual_review=True,
                ).as_dict()
            if phase in terminal_phases:
                saved = saved_terminal_result(phase, current.get("details"))
                if saved is not None:
                    return saved
                return PublishResult(
                    outcome=PublishOutcome.NEED_REVIEW,
                    message="Worker 终态日志与结果不一致，为避免重复投稿已停止执行",
                    error_code="execution_terminal_result_inconsistent",
                    needs_manual_review=True,
                ).as_dict()
            prior_conflict = _prior_job_execution_requires_review(
                payload.job_id,
                payload.execution_id,
            )
            if prior_conflict is not None:
                prior_execution_id, prior_phase = prior_conflict
                result = PublishResult(
                    outcome=PublishOutcome.NEED_REVIEW,
                    message=(
                        "同一发布任务已有旧 execution 进入不可安全重试阶段，"
                        "已拒绝再次投稿并要求人工核对"
                    ),
                    error_code="job_execution_conflict",
                    needs_manual_review=True,
                    provider_response={
                        "prior_execution_id": prior_execution_id,
                        "prior_phase": prior_phase,
                    },
                )
                journal.update("manual_review", result.as_dict(), identity=identity)
                return result.as_dict()
            if phase not in safe_resume_phases:
                result = PublishResult(
                    outcome=PublishOutcome.NEED_REVIEW,
                    message=f"Worker 上次停在 {phase} 阶段，可能已经上传，已禁止自动重试",
                    error_code="execution_resume_unsafe",
                    needs_manual_review=True,
                )
                journal.update("manual_review", result.as_dict(), identity=identity)
                return result.as_dict()

            journal.update(
                "received",
                {"job_id": payload.job_id, "platform": payload.platform},
                identity=identity,
            )
            lock = _account_lock(payload.platform, payload.account_id)
            if not lock.acquire(blocking=False):
                result = PublishResult(
                    outcome=PublishOutcome.FAILED,
                    message="同一账号已有发布任务正在执行",
                    error_code="account_busy",
                )
                journal.update("rejected", result.as_dict())
                return result.as_dict()
            try:
                values = payload.model_dump()
                values["video_path"] = _resolve_media_path(values["video_path"], required=True)
                values["cover_file_path"] = _resolve_media_path(values["cover_file_path"], required=False)
                if Path(values["video_path"]).suffix.lower() not in {
                    ".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm", ".m4v",
                }:
                    raise PublishValidationError("Worker 视频文件类型不受支持", "unsupported_video_format")
                if values["cover_file_path"] and Path(values["cover_file_path"]).suffix.lower() not in {
                    ".jpg", ".jpeg", ".png", ".webp",
                }:
                    raise PublishValidationError("Worker 封面文件类型不受支持", "unsupported_cover_format")

                def update_phase(phase: str, details: dict[str, Any] | None = None) -> None:
                    journal.update(phase, details)

                runtime = BrowserRuntime(
                    payload.platform,
                    payload.account_id,
                    phase_callback=update_phase,
                )
                publisher = get_platform_publisher(
                    payload.platform,
                    runtime=runtime,
                    account_id=payload.account_id,
                )
                result = publisher.publish(values)
                if (
                    result.outcome == PublishOutcome.PUBLISHED
                    and (not result.published_at or result.needs_manual_review)
                ):
                    result = PublishResult(
                        outcome=PublishOutcome.NEED_REVIEW,
                        message="Publisher 成功结果缺少时间证据或仍要求人工复核",
                        error_code="publish_result_inconsistent",
                        needs_manual_review=True,
                        provider_response={"invalid_result": result.as_dict()},
                    )
                if (
                    result.outcome == PublishOutcome.FAILED
                    and journal.read().get("upload_started") is True
                ):
                    result = PublishResult(
                        outcome=PublishOutcome.NEED_REVIEW,
                        message=result.message or "上传开始后的失败结果需要人工确认",
                        error_code=result.error_code or "publish_result_uncertain",
                        needs_manual_review=True,
                        provider_response=result.provider_response,
                    )
                result_phase = {
                    PublishOutcome.PUBLISHED: "confirmed_success",
                    PublishOutcome.EXPORTED: "exported",
                    PublishOutcome.NEED_REVIEW: "manual_review",
                    PublishOutcome.FAILED: "failed",
                }[result.outcome]
                journal.update(result_phase, result.as_dict())
                return result.as_dict()
            except PublishNeedsReview as exc:
                current = journal.read()
                diagnostics = current.get("details") if isinstance(current.get("details"), dict) else {}
                result = PublishResult(
                    outcome=PublishOutcome.NEED_REVIEW,
                    message=exc.message,
                    error_code=exc.error_code,
                    needs_manual_review=True,
                    provider_response={"diagnostics": diagnostics},
                )
                journal.update("manual_review", result.as_dict())
                return result.as_dict()
            except PublishValidationError as exc:
                upload_started = journal.read().get("upload_started") is True
                result = PublishResult(
                    outcome=(
                        PublishOutcome.NEED_REVIEW if upload_started else PublishOutcome.FAILED
                    ),
                    message=exc.message,
                    error_code=exc.error_code,
                    needs_manual_review=upload_started,
                )
                journal.update("manual_review" if upload_started else "failed", result.as_dict())
                return result.as_dict()
            except PublishError as exc:
                phase = str(journal.read().get("phase") or "unknown")
                current = journal.read()
                diagnostics = current.get("details") if isinstance(current.get("details"), dict) else {}
                uncertain = (
                    exc.needs_manual_review
                    or current.get("upload_started") is True
                    or phase in {
                        "upload_started",
                        "upload_completed",
                        "submit_clicked",
                        "unknown",
                    }
                )
                result = PublishResult(
                    outcome=PublishOutcome.NEED_REVIEW if uncertain else PublishOutcome.FAILED,
                    message=exc.message,
                    error_code=exc.error_code,
                    needs_manual_review=uncertain,
                    provider_response={"diagnostics": diagnostics},
                )
                journal.update("manual_review" if uncertain else "failed", result.as_dict())
                return result.as_dict()
            except Exception as exc:
                result = PublishResult(
                    outcome=PublishOutcome.NEED_REVIEW,
                    message=f"Worker 出现未识别异常，请人工确认平台是否已投稿：{exc}",
                    error_code="worker_result_uncertain",
                    needs_manual_review=True,
                )
                journal.update("manual_review", result.as_dict())
                return result.as_dict()
            finally:
                lock.release()

    @worker.post("/v1/publish", dependencies=[Depends(require_token)])
    def publish(payload: PublishRequest) -> dict[str, Any]:
        lock = _job_lock(payload.job_id)
        if not lock.acquire(blocking=True):
            return PublishResult(
                outcome=PublishOutcome.NEED_REVIEW,
                message="同一发布任务已有 execution 正在执行，已拒绝并行投稿",
                error_code="job_execution_in_progress",
                needs_manual_review=True,
            ).as_dict()
        try:
            return publish_with_job_lock(payload)
        finally:
            lock.release()

    @worker.get("/v1/executions/{execution_id}", dependencies=[Depends(require_token)])
    def execution(execution_id: str) -> dict[str, Any]:
        try:
            safe_execution_id = validate_worker_identifier(
                execution_id, "execution_id", max_length=160
            )
        except PublishValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.message) from exc
        journal = ExecutionJournal(safe_execution_id)
        execution_state = journal.read()
        execution_state["in_progress"] = _lock_file_is_active(journal.lock.path)
        return execution_state

    return worker


def main() -> int:
    parser = argparse.ArgumentParser(description="NiuMa Studio Windows publish worker")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not settings.publish_worker_token:
        raise SystemExit("请先在 .env 中设置 PUBLISH_WORKER_TOKEN，再启动发布 Worker。")
    uvicorn.run(create_worker_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
