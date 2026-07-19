"""FastAPI 调度进程访问 Windows 发布 Worker 的小型 HTTP 客户端。"""

from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import settings
from app.services.publishers.base import PublishError, PublishResult, PublishWorkerUnavailable


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
            if exc.code in {401, 403}:
                raise PublishError(str(detail), "publish_worker_unauthorized") from exc
            if exc.code in {409, 422}:
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
            raise PublishWorkerUnavailable(
                "Windows 发布 Worker 未启动或当前不可达。请在项目目录运行 "
                r".\scripts\start_niuma_studio.ps1；脚本会同时启动 Worker、Docker 和发送中心。",
                request_may_have_been_received=timed_out,
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
        return self._request("GET", "/health")

    def check_account(self, platform: str, account_id: str) -> dict[str, Any]:
        return self._request("POST", "/v1/accounts/check", {"platform": platform, "account_id": account_id})

    def start_login(self, platform: str, account_id: str) -> dict[str, Any]:
        return self._request("POST", "/v1/accounts/login", {"platform": platform, "account_id": account_id})

    def open_creator_center(self, platform: str, account_id: str) -> dict[str, Any]:
        return self._request("POST", "/v1/accounts/open-center", {"platform": platform, "account_id": account_id})

    def publish(self, payload: dict[str, Any]) -> PublishResult:
        response = self._request("POST", "/v1/publish", payload)
        return PublishResult.from_dict(response)

    def execution(self, execution_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/executions/{execution_id}")
