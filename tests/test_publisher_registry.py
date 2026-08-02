from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.publishers.base import PublishValidationError
from app.services.publishers.bilibili import BilibiliPublisher
from app.services.publishers.douyin import DouyinPublisher
from app.services.publishers.local_browser import LocalBrowserPublisher
from app.services.publishers.manual_export import ManualExportPublisher
from app.services.publishers.registry import (
    get_platform_publisher_class,
    get_publisher,
    registered_modes,
    registered_platforms,
)


def test_registry_has_only_supported_real_platforms():
    assert registered_platforms() == ("bilibili", "douyin")
    assert {"local_browser", "manual_export", "opencli_publish"}.issubset(registered_modes())


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("douyin", DouyinPublisher), ("bilibili", BilibiliPublisher)],
)
def test_platform_registry_returns_platform_specific_publisher(platform, expected):
    assert get_platform_publisher_class(platform) is expected


@pytest.mark.parametrize("platform", ["douyin", "bilibili"])
def test_local_browser_mode_uses_one_orchestrator(platform):
    publisher = get_publisher(platform, "local_browser")
    assert isinstance(publisher, LocalBrowserPublisher)
    assert publisher.platform == platform


def test_manual_export_is_explicit_mode():
    assert isinstance(get_publisher("douyin", "manual_export"), ManualExportPublisher)


def test_unknown_platform_never_falls_back_to_manual_export():
    with pytest.raises(PublishValidationError) as caught:
        get_publisher("xiaohongshu", "local_browser")
    assert caught.value.error_code == "unregistered_platform"


def test_opencli_compatibility_must_be_enabled_explicitly():
    original = settings.publish_enable_opencli_fallback
    object.__setattr__(settings, "publish_enable_opencli_fallback", False)
    try:
        with pytest.raises(PublishValidationError) as caught:
            get_publisher("douyin", "opencli_publish")
        assert caught.value.error_code == "opencli_fallback_disabled"
    finally:
        object.__setattr__(settings, "publish_enable_opencli_fallback", original)
