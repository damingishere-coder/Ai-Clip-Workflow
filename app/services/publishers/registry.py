"""平台和执行模式注册表。未知项永不静默降级。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.config import settings
from app.services.publishers.base import BasePlatformPublisher, BasePublisher, PublishValidationError


PublisherFactory = Callable[..., BasePublisher]
PlatformFactory = Callable[..., BasePlatformPublisher]

_MODE_REGISTRY: dict[str, PublisherFactory] = {}
_PLATFORM_REGISTRY: dict[str, PlatformFactory] = {}
_BOOTSTRAPPED = False


def register_publisher(name: str, factory: PublisherFactory) -> None:
    key = str(name or "").strip().lower()
    if not key:
        raise ValueError("Publisher 注册名称不能为空")
    _MODE_REGISTRY[key] = factory


def register_platform_publisher(platform: str, factory: PlatformFactory) -> None:
    key = str(platform or "").strip().lower()
    if key not in {"douyin", "bilibili"}:
        raise ValueError(f"不支持的平台：{platform}")
    _PLATFORM_REGISTRY[key] = factory


def _bootstrap() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    from app.services.publishers.bilibili import BilibiliPublisher
    from app.services.publishers.api_compat import ApiCompatPublisher
    from app.services.publishers.douyin import DouyinPublisher
    from app.services.publishers.local_browser import LocalBrowserPublisher
    from app.services.publishers.manual_export import ManualExportPublisher
    from app.services.publishers.opencli_compat import OpenCliCompatPublisher

    register_platform_publisher("douyin", DouyinPublisher)
    register_platform_publisher("bilibili", BilibiliPublisher)
    register_publisher("local_browser", LocalBrowserPublisher)
    register_publisher("manual_export", ManualExportPublisher)
    register_publisher("opencli_publish", OpenCliCompatPublisher)
    register_publisher("api_publish", ApiCompatPublisher)
    _BOOTSTRAPPED = True


def get_platform_publisher_class(platform: str) -> PlatformFactory:
    _bootstrap()
    key = str(platform or "").strip().lower()
    factory = _PLATFORM_REGISTRY.get(key)
    if not factory:
        raise PublishValidationError(f"未注册的平台：{key or '(empty)'}", "unregistered_platform")
    return factory


def get_platform_publisher(platform: str, **dependencies: Any) -> BasePlatformPublisher:
    factory = get_platform_publisher_class(platform)
    return factory(**dependencies)


def get_publisher(platform: str, publish_mode: str, **dependencies: Any) -> BasePublisher:
    _bootstrap()
    platform_key = str(platform or "").strip().lower()
    mode_key = str(publish_mode or "").strip().lower()
    if platform_key not in _PLATFORM_REGISTRY:
        raise PublishValidationError(f"未注册的平台：{platform_key or '(empty)'}", "unregistered_platform")
    factory = _MODE_REGISTRY.get(mode_key)
    if not factory:
        raise PublishValidationError(f"未注册的发布方式：{mode_key or '(empty)'}", "unsupported_publish_mode")
    if mode_key == "opencli_publish" and not settings.publish_enable_opencli_fallback:
        raise PublishValidationError(
            "该任务使用旧 opencli 模式，但兼容开关未开启，请改为本地浏览器后重新排期",
            "opencli_fallback_disabled",
            needs_manual_review=True,
        )
    return factory(platform=platform_key, **dependencies)


def registered_platforms() -> tuple[str, ...]:
    _bootstrap()
    return tuple(sorted(_PLATFORM_REGISTRY))


def registered_modes() -> tuple[str, ...]:
    _bootstrap()
    return tuple(sorted(_MODE_REGISTRY))
