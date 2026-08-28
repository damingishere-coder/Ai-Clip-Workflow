from __future__ import annotations

import io
import json
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.error import HTTPError

from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.services.publishers.base import PublishError, PublishNeedsReview
from app.services.publishers.worker_client import PublishWorkerClient
from scripts import publish_host_worker as worker_module


class FakeLocator:
    def __init__(self, text: str) -> None:
        self.text = text

    def inner_text(self, timeout=0):
        return self.text


class FakeResponse:
    def __init__(self, *, status: int, url: str, payload=None) -> None:
        self.status = status
        self.url = url
        self._payload = payload
        self.request = SimpleNamespace(resource_type="xhr")

    def json(self):
        return self._payload


class FakePage:
    def __init__(self, responses: list[FakeResponse], *, text: str = "作品管理") -> None:
        self.url = worker_module.DOUYIN_CONTENT_MANAGE_URL
        self.responses = responses
        self.text = text
        self.handler = None
        self.reload_count = 0
        self.evaluate_count = 0

    def locator(self, selector):
        assert selector == "body"
        return FakeLocator(self.text)

    def on(self, event, handler):
        assert event == "response"
        self.handler = handler

    def reload(self, **_kwargs):
        self.reload_count += 1
        for response in self.responses:
            self.handler(response)

    def wait_for_timeout(self, _milliseconds):
        return None

    def evaluate(self, _script, _payload):
        self.evaluate_count += 1
        return {
            "results": [
                {
                    "aweme_id": "1001",
                    "payload": {
                        "data": {
                            "play_count": 1200,
                            "like_count": 80,
                            "comment_count": 12,
                            "share_count": 4,
                        }
                    },
                }
            ]
        }


class FakeRuntime:
    def __init__(self, page: FakePage, *, challenge: bool = False) -> None:
        self.fake_page = page
        self.challenge = challenge

    @contextmanager
    def page(self, _url):
        yield self.fake_page

    def detect_manual_challenge(self, _page):
        if self.challenge:
            raise PublishNeedsReview("平台要求验证码", "platform_verification_required")


def _list_response(status: int = 200, payload=None) -> FakeResponse:
    return FakeResponse(
        status=status,
        url="https://creator.douyin.com/api/aweme/item/list",
        payload=payload,
    )


def test_recursive_xhr_extraction_only_returns_whitelisted_item_fields():
    payload = {
        "data": {
            "item_list": [
                {
                    "aweme_id": "1001",
                    "desc": "测试作品",
                    "create_time": 1787875200,
                    "duration": 61000,
                    "statistics": {
                        "play_count": "1,200",
                        "digg_count": 80,
                        "comment_count": 12,
                        "share_count": 4,
                    },
                    "cookie": "must-not-leak",
                    "raw_private_field": "must-not-leak",
                }
            ]
        }
    }
    items = worker_module._extract_douyin_work_items(payload)
    assert len(items) == 1
    assert items[0]["aweme_id"] == "1001"
    assert items[0]["duration_seconds"] == 61
    assert items[0]["play_count"] == 1200
    assert "cookie" not in items[0]
    assert "raw_private_field" not in items[0]


def test_sync_uses_one_page_reload_and_same_origin_metrics_without_retry():
    page = FakePage(
        [
            _list_response(
                payload={
                    "data": {
                        "items": [
                            {
                                "aweme_id": "1001",
                                "title": "测试作品",
                                "create_time": 1787875200,
                                "duration": 60000,
                            }
                        ]
                    }
                }
            )
        ]
    )
    result = worker_module._sync_douyin_analytics(FakeRuntime(page), limit=50)
    assert page.reload_count == 1
    assert page.evaluate_count == 1
    assert result["items"][0]["play_count"] == 1200
    assert result["items"][0]["like_count"] == 80


@pytest.mark.parametrize(
    ("page", "challenge", "error_code"),
    [
        (FakePage([], text="请先登录创作者中心"), False, "LOGIN_REQUIRED"),
        (FakePage([]), True, "VERIFICATION_REQUIRED"),
        (FakePage([_list_response(status=429)]), False, "RATE_LIMITED"),
        (FakePage([]), False, "PAGE_CHANGED"),
    ],
)
def test_sync_stops_with_fixed_error_codes(page, challenge, error_code):
    with pytest.raises(worker_module.AnalyticsSyncError) as caught:
        worker_module._sync_douyin_analytics(
            FakeRuntime(page, challenge=challenge),
            limit=50,
        )
    assert caught.value.error_code == error_code
    assert page.reload_count <= 1


def test_worker_route_uses_account_lock_and_fixed_unavailable_code(tmp_path):
    original_state_dir = settings.publish_worker_state_dir
    object.__setattr__(settings, "publish_worker_state_dir", tmp_path)
    lock = worker_module._account_lock("douyin", "analytics-account")
    assert lock.acquire(blocking=False)
    try:
        response = TestClient(worker_module.create_worker_app(token="test-token")).post(
            "/v1/analytics/douyin/sync",
            headers={"Authorization": "Bearer test-token"},
            json={"account_id": "analytics-account", "limit": 50},
        )
    finally:
        lock.release()
        object.__setattr__(settings, "publish_worker_state_dir", original_state_dir)
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "WORKER_UNAVAILABLE"


def test_worker_route_maps_page_change_without_opening_real_browser(monkeypatch, tmp_path):
    original_state_dir = settings.publish_worker_state_dir
    object.__setattr__(settings, "publish_worker_state_dir", tmp_path)

    def page_changed(_runtime, _limit):
        raise worker_module.AnalyticsSyncError("structure changed", "PAGE_CHANGED")

    monkeypatch.setattr(worker_module, "_sync_douyin_analytics", page_changed)
    try:
        response = TestClient(worker_module.create_worker_app(token="test-token")).post(
            "/v1/analytics/douyin/sync",
            headers={"Authorization": "Bearer test-token"},
            json={"account_id": "analytics-account", "limit": 50},
        )
    finally:
        object.__setattr__(settings, "publish_worker_state_dir", original_state_dir)
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error_code": "PAGE_CHANGED",
        "message": "structure changed",
    }


def test_worker_client_preserves_fixed_analytics_error_code(monkeypatch):
    body = io.BytesIO(
        json.dumps(
            {"detail": {"error_code": "RATE_LIMITED", "message": "rate limited"}}
        ).encode("utf-8")
    )

    def fail_urlopen(request, timeout):
        raise HTTPError(request.full_url, 429, "Too Many Requests", {}, body)

    monkeypatch.setattr("app.services.publishers.worker_client.urlopen", fail_urlopen)
    with pytest.raises(PublishError) as caught:
        PublishWorkerClient("http://127.0.0.1:8765", "token", 2).analytics_sync(
            "analytics-account",
            limit=50,
        )
    assert caught.value.error_code == "RATE_LIMITED"
    assert caught.value.message == "rate limited"
