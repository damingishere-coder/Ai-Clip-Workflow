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
from datetime import datetime, timezone
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


class AnalyticsSyncRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=120)
    limit: int = Field(default=50, ge=1, le=50)

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, value: str) -> str:
        return validate_worker_identifier(value, "account_id", max_length=120)


ANALYTICS_ERROR_STATUS = {
    "LOGIN_REQUIRED": 409,
    "VERIFICATION_REQUIRED": 409,
    "RATE_LIMITED": 429,
    "PAGE_CHANGED": 422,
    "WORKER_UNAVAILABLE": 503,
}
DOUYIN_CONTENT_MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"
DOUYIN_METRICS_TREND_URL = "https://creator.douyin.com/janus/douyin/creator/data/item_analysis/metrics_trend"


class AnalyticsSyncError(RuntimeError):
    def __init__(self, message: str, error_code: str) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


def _analytics_http_error(error_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=ANALYTICS_ERROR_STATUS[error_code],
        detail={"error_code": error_code, "message": message},
    )


def _first_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _nested_dict(mapping: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _as_nonnegative_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(0, int(float(str(value).replace(",", ""))))
    except (TypeError, ValueError):
        return None


def _as_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return None
    if str(value).strip().endswith("%") or (1 < number <= 100):
        number /= 100
    return number if number >= 0 else None


def _published_at(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str) and "-" in value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(timespec="seconds")
        except ValueError:
            return value[:40]
    try:
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def _normalize_douyin_item(candidate: dict[str, Any]) -> dict[str, Any] | None:
    stats = _nested_dict(candidate, ("statistics", "stats", "stat", "metrics"))
    video = _nested_dict(candidate, ("video", "video_info", "videoInfo"))
    aweme_id = _first_value(candidate, ("aweme_id", "awemeId", "item_id", "itemId"))
    if aweme_id in (None, "") and any(key in candidate for key in ("create_time", "publish_time", "desc")):
        aweme_id = candidate.get("id")
    aweme_id = str(aweme_id or "").strip()
    if not aweme_id:
        return None
    duration = _first_value(candidate, ("duration", "duration_seconds", "video_duration"))
    if duration in (None, ""):
        duration = _first_value(video, ("duration", "duration_seconds"))
    try:
        duration_value = float(duration) if duration not in (None, "") else None
        if duration_value is not None and duration_value > 3600:
            duration_value /= 1000
    except (TypeError, ValueError):
        duration_value = None
    merged = {**candidate, **stats}
    return {
        "aweme_id": aweme_id[:120],
        "title": str(_first_value(candidate, ("title", "desc", "caption", "name")) or "")[:240],
        "published_at": _published_at(
            _first_value(candidate, ("create_time", "publish_time", "published_at", "createTime"))
        ),
        "duration_seconds": duration_value,
        "play_count": _as_nonnegative_int(_first_value(merged, ("play_count", "playCount", "play"))),
        "like_count": _as_nonnegative_int(_first_value(merged, ("like_count", "digg_count", "likeCount"))),
        "comment_count": _as_nonnegative_int(_first_value(merged, ("comment_count", "commentCount"))),
        "share_count": _as_nonnegative_int(_first_value(merged, ("share_count", "shareCount"))),
        "collect_count": _as_nonnegative_int(_first_value(merged, ("collect_count", "collectCount"))),
        "five_second_completion_rate": _as_optional_float(
            _first_value(merged, ("five_second_completion_rate", "five_sec_play_rate", "play_5s_rate"))
        ),
        "two_second_bounce_rate": _as_optional_float(
            _first_value(merged, ("two_second_bounce_rate", "two_sec_jump_rate", "skip_2s_rate"))
        ),
        "cover_click_rate": _as_optional_float(
            _first_value(merged, ("cover_click_rate", "coverClickRate"))
        ),
        "average_watch_seconds": _as_optional_float(
            _first_value(merged, ("average_watch_seconds", "avg_play_duration", "average_play_time"))
        ),
    }


def _extract_douyin_work_items(payload: Any, limit: int = 50) -> list[dict[str, Any]]:
    """从作品列表 XHR 的常见嵌套结构中提取白名单字段，不保留原始响应。"""
    candidates: list[dict[str, Any]] = []

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 10 or len(candidates) >= 500:
            return
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    normalized = _normalize_douyin_item(item)
                    if normalized is not None:
                        candidates.append(normalized)
                    visit(item, depth + 1)
            return
        if isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested, depth + 1)

    visit(payload)
    unique: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        aweme_id = candidate["aweme_id"]
        if aweme_id not in unique or sum(value is not None for value in candidate.values()) > sum(
            value is not None for value in unique[aweme_id].values()
        ):
            unique[aweme_id] = candidate
    return sorted(
        unique.values(),
        key=lambda item: str(item.get("published_at") or ""),
        reverse=True,
    )[: max(1, min(50, int(limit)))]


def _metric_values(payload: Any) -> dict[str, Any]:
    items = _extract_douyin_work_items(payload, limit=1)
    if items:
        return {key: value for key, value in items[0].items() if key.endswith("_count") and value is not None}
    found: dict[str, Any] = {}

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 10 or not isinstance(value, (dict, list)):
            return
        if isinstance(value, list):
            for nested in value:
                visit(nested, depth + 1)
            return
        aliases = {
            "play_count": ("play_count", "playCount"),
            "like_count": ("like_count", "digg_count", "likeCount"),
            "comment_count": ("comment_count", "commentCount"),
            "share_count": ("share_count", "shareCount"),
        }
        for field, keys in aliases.items():
            parsed = _as_nonnegative_int(_first_value(value, keys))
            if parsed is not None:
                found[field] = parsed
        for nested in value.values():
            visit(nested, depth + 1)

    visit(payload)
    return found


def _page_requires_login(page: Any) -> bool:
    url = str(getattr(page, "url", "") or "").lower()
    if any(marker in url for marker in ("passport", "/login", "login.douyin")):
        return True
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        text = ""
    return any(marker in text for marker in ("登录后使用", "请先登录", "登录创作者中心", "扫码登录"))


def _metric_start_timestamp(value: Any, fallback: int) -> int:
    if not value:
        return fallback
    try:
        return max(0, int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()))
    except (TypeError, ValueError, OSError):
        return fallback


def _sync_douyin_analytics(runtime: BrowserRuntime, limit: int) -> dict[str, Any]:
    captured_payloads: list[Any] = []
    response_state = {"rate_limited": False, "login_required": False}
    with runtime.page(DOUYIN_CONTENT_MANAGE_URL) as page:
        if _page_requires_login(page):
            raise AnalyticsSyncError("抖音创作者中心登录已失效，请先重新登录", "LOGIN_REQUIRED")
        try:
            runtime.detect_manual_challenge(page)
        except PublishNeedsReview as exc:
            raise AnalyticsSyncError(exc.message, "VERIFICATION_REQUIRED") from exc

        def capture_response(response: Any) -> None:
            try:
                url = str(response.url or "").lower()
                resource_type = str(response.request.resource_type or "").lower()
                if resource_type not in {"xhr", "fetch"}:
                    return
                if int(response.status) == 429:
                    response_state["rate_limited"] = True
                    return
                if int(response.status) in {401, 403}:
                    response_state["login_required"] = True
                    return
                if not any(marker in url for marker in ("aweme", "item", "content/manage", "video/list")):
                    return
                if int(response.status) == 200:
                    captured_payloads.append(response.json())
            except Exception:
                return

        page.on("response", capture_response)
        try:
            page.reload(wait_until="domcontentloaded", timeout=settings.publish_browser_navigation_timeout_ms)
            page.wait_for_timeout(3000)
        except Exception as exc:
            raise AnalyticsSyncError(f"抖音作品页加载失败：{exc}", "WORKER_UNAVAILABLE") from exc
        if response_state["rate_limited"]:
            raise AnalyticsSyncError("抖音返回 429 限流，已停止同步且不会自动重试", "RATE_LIMITED")
        if response_state["login_required"] or _page_requires_login(page):
            raise AnalyticsSyncError("抖音创作者中心登录已失效，请先重新登录", "LOGIN_REQUIRED")
        try:
            runtime.detect_manual_challenge(page)
        except PublishNeedsReview as exc:
            raise AnalyticsSyncError(exc.message, "VERIFICATION_REQUIRED") from exc

        items: list[dict[str, Any]] = []
        for payload in captured_payloads:
            items.extend(_extract_douyin_work_items(payload, limit=limit))
        deduplicated = {item["aweme_id"]: item for item in items}
        items = sorted(
            deduplicated.values(),
            key=lambda item: str(item.get("published_at") or ""),
            reverse=True,
        )[:limit]
        if not items:
            raise AnalyticsSyncError(
                "没有从作品列表请求中识别到作品数据，平台页面结构可能已变化",
                "PAGE_CHANGED",
            )

        now_timestamp = int(time.time())
        metric_input = [
            {
                "aweme_id": item["aweme_id"],
                "start_time": _metric_start_timestamp(
                    item.get("published_at"),
                    now_timestamp - 90 * 86400,
                ),
                "end_time": now_timestamp,
            }
            for item in items
        ]
        metric_result = page.evaluate(
            """
            async ({endpoint, works}) => {
              const results = [];
              for (const work of works) {
                const response = await fetch(endpoint, {
                  method: 'POST',
                  credentials: 'same-origin',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({
                    aweme_id: work.aweme_id,
                    start_time: work.start_time,
                    end_time: work.end_time,
                    metrics: ['play_count', 'like_count', 'comment_count', 'share_count']
                  })
                });
                if (!response.ok) return {error_status: response.status, results};
                let payload;
                try { payload = await response.json(); }
                catch (_error) { return {error_status: 520, results}; }
                results.push({aweme_id: work.aweme_id, payload});
              }
              return {results};
            }
            """,
            {"endpoint": DOUYIN_METRICS_TREND_URL, "works": metric_input},
        )
        if not isinstance(metric_result, dict):
            raise AnalyticsSyncError("作品指标接口返回格式已变化", "PAGE_CHANGED")
        error_status = int(metric_result.get("error_status") or 0)
        if error_status == 429:
            raise AnalyticsSyncError("抖音返回 429 限流，已停止同步且不会自动重试", "RATE_LIMITED")
        if error_status in {401, 403}:
            raise AnalyticsSyncError("抖音创作者中心登录已失效，请先重新登录", "LOGIN_REQUIRED")
        if error_status:
            raise AnalyticsSyncError(f"作品指标接口返回 HTTP {error_status}", "PAGE_CHANGED")
        by_id = {item["aweme_id"]: item for item in items}
        for result in metric_result.get("results") or []:
            if not isinstance(result, dict) or str(result.get("aweme_id") or "") not in by_id:
                continue
            by_id[str(result["aweme_id"])].update(_metric_values(result.get("payload")))
        return {
            "status": "ok",
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "items": list(by_id.values())[:limit],
        }


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

    @worker.post("/v1/analytics/douyin/sync", dependencies=[Depends(require_token)])
    def sync_douyin_analytics(payload: AnalyticsSyncRequest) -> dict[str, Any]:
        lock = _account_lock("douyin", payload.account_id)
        if not lock.acquire(blocking=False):
            raise _analytics_http_error(
                "WORKER_UNAVAILABLE",
                "该账号正在执行其他浏览器操作，请完成后再同步",
            )
        try:
            runtime = BrowserRuntime("douyin", payload.account_id)
            return _sync_douyin_analytics(runtime, payload.limit)
        except AnalyticsSyncError as exc:
            raise _analytics_http_error(exc.error_code, exc.message) from exc
        except PublishNeedsReview as exc:
            raise _analytics_http_error("VERIFICATION_REQUIRED", exc.message) from exc
        except PublishError as exc:
            error_code = (
                "LOGIN_REQUIRED"
                if "login" in str(exc.error_code or "").lower()
                else "WORKER_UNAVAILABLE"
            )
            raise _analytics_http_error(error_code, exc.message) from exc
        except Exception as exc:
            logger.exception("抖音作品指标同步失败：account_id=%s", payload.account_id)
            raise _analytics_http_error("WORKER_UNAVAILABLE", f"Windows Worker 同步失败：{exc}") from exc
        finally:
            lock.release()

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
