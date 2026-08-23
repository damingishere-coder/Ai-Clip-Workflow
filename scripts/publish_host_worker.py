"""牛马片场 Windows 浏览器发布 Worker。

该进程必须运行在安装了 Google Chrome 的 Windows 主机上。FastAPI 调度器通过带
Bearer Token 的本地 HTTP 接口调用它，Docker 容器自身不接触宿主浏览器。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

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
)
from app.services.publishers.browser_runtime import BrowserRuntime  # noqa: E402
from app.services.publishers.registry import get_platform_publisher  # noqa: E402


class AccountRequest(BaseModel):
    platform: str = Field(pattern="^(douyin|bilibili)$")
    account_id: str = Field(min_length=1, max_length=120)


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


class OpenCliRunRequest(BaseModel):
    command: list[str]
    timeout: int = Field(default=600, ge=1, le=1800)


class ExecutionJournal:
    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self.root = Path(settings.publish_worker_state_dir) / "executions"
        self.path = self.root / f"{execution_id}.json"
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"execution_id": self.execution_id, "phase": "unknown"}
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"execution_id": self.execution_id, "phase": "unknown"}
        return parsed if isinstance(parsed, dict) else {"execution_id": self.execution_id, "phase": "unknown"}

    def update(self, phase: str, details: dict[str, Any] | None = None) -> None:
        with self._lock:
            current = self.read()
            current.update({
                "execution_id": self.execution_id,
                "phase": phase,
                "updated_at": utc_now_iso(),
            })
            if details:
                current["details"] = details
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)


_ACCOUNT_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _account_lock(platform: str, account_id: str) -> threading.Lock:
    key = f"{platform}:{account_id}"
    with _LOCKS_GUARD:
        return _ACCOUNT_LOCKS.setdefault(key, threading.Lock())


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

    def login_background(payload: AccountRequest) -> None:
        lock = _account_lock(payload.platform, payload.account_id)
        if not lock.acquire(blocking=False):
            return
        try:
            runtime = BrowserRuntime(payload.platform, payload.account_id)
            publisher = get_platform_publisher(
                payload.platform, runtime=runtime, account_id=payload.account_id
            )
            publisher.open_login(payload.account_id)
        except Exception:
            # Worker 只负责宿主浏览器操作，不直接写 Docker 挂载的 SQLite。
            # 登录结果由 FastAPI 后续通过账号检测接口统一落库。
            return
        finally:
            lock.release()

    @worker.post("/v1/accounts/login", dependencies=[Depends(require_token)], status_code=202)
    def login_account(payload: AccountRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
        if _account_lock(payload.platform, payload.account_id).locked():
            raise HTTPException(status_code=409, detail="该账号已有浏览器窗口正在运行")
        background_tasks.add_task(login_background, payload)
        return {"status": "started", "message": "已打开独立 Chrome，请在窗口中完成平台登录"}

    @worker.post("/v1/accounts/open-center", dependencies=[Depends(require_token)], status_code=202)
    def open_center(payload: AccountRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
        if _account_lock(payload.platform, payload.account_id).locked():
            raise HTTPException(status_code=409, detail="该账号已有浏览器窗口正在运行")
        background_tasks.add_task(login_background, payload)
        return {"status": "started", "message": "已打开平台创作者中心"}

    @worker.post("/v1/publish", dependencies=[Depends(require_token)])
    def publish(payload: PublishRequest) -> dict[str, Any]:
        journal = ExecutionJournal(payload.execution_id)
        journal.update("received", {"job_id": payload.job_id, "platform": payload.platform})
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
            journal.update("confirmed_success" if result.outcome == PublishOutcome.PUBLISHED else result.outcome.value.lower(), result.as_dict())
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
            result = PublishResult(
                outcome=PublishOutcome.FAILED,
                message=exc.message,
                error_code=exc.error_code,
            )
            journal.update("failed", result.as_dict())
            return result.as_dict()
        except PublishError as exc:
            phase = str(journal.read().get("phase") or "unknown")
            current = journal.read()
            diagnostics = current.get("details") if isinstance(current.get("details"), dict) else {}
            uncertain = exc.needs_manual_review or phase in {
                "upload_started", "upload_completed", "submit_clicked", "unknown"
            }
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

    @worker.get("/v1/executions/{execution_id}", dependencies=[Depends(require_token)])
    def execution(execution_id: str) -> dict[str, Any]:
        return ExecutionJournal(execution_id).read()

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
