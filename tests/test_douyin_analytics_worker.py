from __future__ import annotations

import io
import json
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.error import HTTPError

from fastapi.testclient import TestClient
from openpyxl import Workbook
import pytest

from app.core.config import settings
from app.services import content_review_service
from app.services.publishers.base import PublishError, PublishNeedsReview
from app.services.publishers.worker_client import PublishWorkerClient
from scripts import publish_host_worker as worker_module


def _official_xlsx(row_count: int = 1) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(content_review_service.DOUYIN_ITEM_EXPORT_HEADERS)
    for index in range(row_count):
        worksheet.append(
            [
                f"测试作品 {index:03d}",
                f"2026-08-{28 - (index % 20):02d} 10:00",
                "视频",
                "审核通过",
                100 + index,
                0.4,
                0.5,
                0.3,
                0.2,
                12.5,
                10,
                2,
                3,
                4,
                5,
                1,
            ]
        )
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class FakeBodyLocator:
    def __init__(self, text: str) -> None:
        self.text = text

    def inner_text(self, timeout=0):
        return self.text


class FakeButton:
    def __init__(self, page: "FakePage", *, visible: bool = True) -> None:
        self.page = page
        self.visible = visible

    @property
    def first(self):
        return self

    def wait_for(self, **_kwargs):
        if not self.visible:
            raise RuntimeError("button missing")

    def click(self):
        self.page.click_count += 1
        if self.page.response_handler:
            for status in self.page.response_statuses:
                self.page.response_handler(SimpleNamespace(status=status))


class FakeDownload:
    def __init__(self, path, *, failure: str | None = None, filename: str = "作品列表导出.xlsx"):
        self._path = path
        self._failure = failure
        self.suggested_filename = filename

    def failure(self):
        return self._failure

    def path(self):
        return str(self._path) if self._path is not None else None


class FakePage:
    def __init__(
        self,
        download: FakeDownload,
        *,
        text: str = "作品管理",
        button_visible: bool = True,
        response_statuses: list[int] | None = None,
        expect_error: Exception | None = None,
    ) -> None:
        self.url = worker_module.DOUYIN_CONTENT_MANAGE_URL
        self.download = download
        self.text = text
        self.button_visible = button_visible
        self.response_statuses = response_statuses or []
        self.expect_error = expect_error
        self.response_handler = None
        self.click_count = 0
        self.expect_count = 0

    def locator(self, selector):
        assert selector == "body"
        return FakeBodyLocator(self.text)

    def on(self, event, handler):
        assert event == "response"
        self.response_handler = handler

    def wait_for_timeout(self, _milliseconds):
        return None

    def get_by_role(self, _role, **_kwargs):
        return FakeButton(self, visible=self.button_visible)

    def get_by_text(self, _text, **_kwargs):
        return FakeButton(self, visible=self.button_visible)

    @contextmanager
    def expect_download(self, **_kwargs):
        self.expect_count += 1
        info = SimpleNamespace(value=self.download)
        yield info
        if self.expect_error:
            raise self.expect_error


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


def test_export_clicks_once_parses_all_rows_and_removes_temporary_file(tmp_path):
    download_path = tmp_path / "temporary.xlsx"
    download_path.write_bytes(_official_xlsx(107))
    page = FakePage(FakeDownload(download_path))

    result = worker_module._export_douyin_item_report(FakeRuntime(page))

    assert page.click_count == 1
    assert page.expect_count == 1
    assert result["row_count"] == 107
    assert len(result["items"]) == 107
    assert result["source_filename"] == "作品列表导出.xlsx"
    assert set(result["items"][0]) == set(content_review_service.DOUYIN_ITEM_EXPORT_FIELDS)
    assert not download_path.exists()


@pytest.mark.parametrize(
    ("page_options", "challenge", "content", "download_failure", "error_code", "max_clicks"),
    [
        ({"text": "请先登录创作者中心"}, False, _official_xlsx(), None, "LOGIN_REQUIRED", 0),
        ({}, True, _official_xlsx(), None, "VERIFICATION_REQUIRED", 0),
        ({"response_statuses": [429]}, False, _official_xlsx(), None, "RATE_LIMITED", 1),
        ({"button_visible": False}, False, _official_xlsx(), None, "PAGE_CHANGED", 0),
        ({"expect_error": TimeoutError("timed out")}, False, _official_xlsx(), None, "DOWNLOAD_FAILED", 1),
        ({}, False, b"broken workbook", None, "INVALID_EXPORT", 1),
        ({}, False, _official_xlsx(), "download failed", "DOWNLOAD_FAILED", 1),
    ],
)
def test_export_stops_once_with_fixed_error_codes(
    tmp_path,
    page_options,
    challenge,
    content,
    download_failure,
    error_code,
    max_clicks,
):
    download_path = tmp_path / f"{error_code}.xlsx"
    download_path.write_bytes(content)
    page = FakePage(
        FakeDownload(download_path, failure=download_failure),
        **page_options,
    )
    with pytest.raises(worker_module.AnalyticsSyncError) as caught:
        worker_module._export_douyin_item_report(
            FakeRuntime(page, challenge=challenge)
        )
    assert caught.value.error_code == error_code
    assert page.click_count <= max_clicks
    assert page.expect_count <= 1
    if page.click_count == 1 and "expect_error" not in page_options:
        assert not download_path.exists()


def test_worker_route_uses_account_lock_and_fixed_unavailable_code(tmp_path):
    original_state_dir = settings.publish_worker_state_dir
    object.__setattr__(settings, "publish_worker_state_dir", tmp_path)
    lock = worker_module._account_lock("douyin", "analytics-account")
    assert lock.acquire(blocking=False)
    try:
        response = TestClient(worker_module.create_worker_app(token="test-token")).post(
            "/v1/analytics/douyin/export-sync",
            headers={"Authorization": "Bearer test-token"},
            json={"account_id": "analytics-account"},
        )
    finally:
        lock.release()
        object.__setattr__(settings, "publish_worker_state_dir", original_state_dir)
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "WORKER_UNAVAILABLE"


def test_worker_route_maps_invalid_export_without_opening_real_browser(monkeypatch, tmp_path):
    original_state_dir = settings.publish_worker_state_dir
    object.__setattr__(settings, "publish_worker_state_dir", tmp_path)

    def invalid_export(_runtime):
        raise worker_module.AnalyticsSyncError("broken workbook", "INVALID_EXPORT")

    monkeypatch.setattr(worker_module, "_export_douyin_item_report", invalid_export)
    try:
        response = TestClient(worker_module.create_worker_app(token="test-token")).post(
            "/v1/analytics/douyin/export-sync",
            headers={"Authorization": "Bearer test-token"},
            json={"account_id": "analytics-account"},
        )
    finally:
        object.__setattr__(settings, "publish_worker_state_dir", original_state_dir)
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error_code": "INVALID_EXPORT",
        "message": "broken workbook",
    }


def test_worker_client_preserves_fixed_export_error_code(monkeypatch):
    body = io.BytesIO(
        json.dumps(
            {"detail": {"error_code": "DOWNLOAD_FAILED", "message": "download failed"}}
        ).encode("utf-8")
    )

    def fail_urlopen(request, timeout):
        raise HTTPError(request.full_url, 502, "Bad Gateway", {}, body)

    monkeypatch.setattr("app.services.publishers.worker_client.urlopen", fail_urlopen)
    with pytest.raises(PublishError) as caught:
        PublishWorkerClient("http://127.0.0.1:8765", "token", 2).analytics_export_sync(
            "analytics-account"
        )
    assert caught.value.error_code == "DOWNLOAD_FAILED"
    assert caught.value.message == "download failed"
