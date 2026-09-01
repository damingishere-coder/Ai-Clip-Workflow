"""FastAPI 调度进程访问 Windows 发布 Worker 的小型 HTTP 客户端。"""

from __future__ import annotations

import json
import re
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import settings
from app.services.publishers.base import (
    PublishError,
    PublishResult,
    PublishValidationError,
    PublishWorkerUnavailable,
)


_WORKER_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def validate_worker_identifier(value: str, field_name: str, *, max_length: int) -> str:
    """验证会进入 URL、journal 或 Windows 目录名的稳定标识。"""
    text = str(value or "")
    if not text or len(text) > max_length:
        raise PublishValidationError(f"{field_name} 长度不合法", "invalid_worker_identifier")
    if (
        not _WORKER_IDENTIFIER.fullmatch(text)
        or ".." in text
        or text.endswith((".", " "))
        or text.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
    ):
        raise PublishValidationError(
            f"{field_name} 含有不安全字符或 Windows 保留名称",
            "invalid_worker_identifier",
        )
    return text


class PublishWorkerClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: int | None = None) -> None:
        self.base_url = str(base_url or settings.publish_worker_url).rstrip("/")
        self.token = str(token if token is not None else settings.publish_worker_token)
        self.timeout = max(2, int(timeout or settings.publish_worker_timeout_seconds))

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.base_url:
            raise PublishWorkerUnavailable("未配置 PUBLISH_WORKER_URL")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - URL is local configuration
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw).get("detail") or raw
            except json.JSONDecodeError:
                detail = raw or str(exc)
            detail_code = ""
            if isinstance(detail, dict):
                detail_code = str(detail.get("error_code") or "")
                detail = str(detail.get("message") or detail_code or "Worker 拒绝了请求")
            if exc.code in {401, 403}:
                raise PublishError(str(detail), "publish_worker_unauthorized") from exc
            if detail_code in {
                "LOGIN_REQUIRED",
                "VERIFICATION_REQUIRED",
                "RATE_LIMITED",
                "PAGE_CHANGED",
                "WORKER_UNAVAILABLE",
            }:
                raise PublishError(str(detail), detail_code) from exc
            if exc.code in {409, 422, 429}:
                raise PublishError(str(detail), "publish_worker_rejected") from exc
            raise PublishWorkerUnavailable(
                f"Windows 发布 Worker 返回 HTTP {exc.code}：{detail}",
                request_may_have_been_received=True,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise PublishWorkerUnavailable(
                f"等待 Windows 发布 Worker 返回结果超时：{exc}",
                request_may_have_been_received=True,
            ) from exc
        except (URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            timed_out = isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower()
            definitely_not_connected = isinstance(reason, (ConnectionRefusedError, socket.gaierror))
            publish_result_uncertain = (
                method.upper() == "POST"
                and path == "/v1/publish"
                and not definitely_not_connected
            )
            raise PublishWorkerUnavailable(
                "发送服务正在随 Docker 中的牛马片场项目自动启动。"
                "如果刚刚运行项目，请稍候并重新检测；持续未连接时，请在 Docker Desktop 中停止后重新运行本项目。",
                request_may_have_been_received=timed_out or publish_result_uncertain,
            ) from exc
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PublishWorkerUnavailable("Windows 发布 Worker 返回了无效数据") from exc
        if not isinstance(parsed, dict):
            raise PublishWorkerUnavailable("Windows 发布 Worker 返回格式不正确")
        return parsed

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/health")

    def check_account(self, platform: str, account_id: str) -> dict[str, Any]:
        account_id = validate_worker_identifier(account_id, "account_id", max_length=120)
        return self._request("POST", "/v1/accounts/check", {"platform": platform, "account_id": account_id})

    def start_login(self, platform: str, account_id: str) -> dict[str, Any]:
        account_id = validate_worker_identifier(account_id, "account_id", max_length=120)
        return self._request("POST", "/v1/accounts/login", {"platform": platform, "account_id": account_id})

    def open_creator_center(self, platform: str, account_id: str) -> dict[str, Any]:
        account_id = validate_worker_identifier(account_id, "account_id", max_length=120)
        return self._request("POST", "/v1/accounts/open-center", {"platform": platform, "account_id": account_id})

    def analytics_sync(self, account_id: str, limit: int = 50) -> dict[str, Any]:
        account_id = validate_worker_identifier(account_id, "account_id", max_length=120)
        safe_limit = max(1, min(50, int(limit)))
        return self._request(
            "POST",
            "/v1/analytics/douyin/sync",
            {"account_id": account_id, "limit": safe_limit},
        )

    def publish(self, payload: dict[str, Any]) -> PublishResult:
        safe_payload = dict(payload)
        safe_payload["job_id"] = validate_worker_identifier(
            str(safe_payload.get("job_id") or ""), "job_id", max_length=160
        )
        safe_payload["execution_id"] = validate_worker_identifier(
            str(safe_payload.get("execution_id") or ""), "execution_id", max_length=160
        )
        safe_payload["account_id"] = validate_worker_identifier(
            str(safe_payload.get("account_id") or ""), "account_id", max_length=120
        )
        response = self._request("POST", "/v1/publish", safe_payload)
        return PublishResult.from_dict(response)

    def execution(self, execution_id: str) -> dict[str, Any]:
        execution_id = validate_worker_identifier(execution_id, "execution_id", max_length=160)
        return self._request("GET", f"/v1/executions/{execution_id}")
