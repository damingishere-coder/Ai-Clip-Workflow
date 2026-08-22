"""发布领域常量：目标平台、执行方式与状态。"""

TARGET_PLATFORMS = {
    "douyin": "抖音",
    "bilibili": "B站",
}

# 前台和自动同步当前只创建抖音任务；B 站后端能力与历史数据继续保留。
AUTO_PUBLISH_PLATFORMS = ("douyin",)

PUBLISH_MODES = {
    "opencli_publish": "opencli 兼容发送（需显式开启）",
    "manual_export": "本地发布包导出",
    "api_publish": "平台 API 发布",
    "local_browser": "Windows Chrome 真实发布",
}

PUBLISH_STATUSES = {
    "DRAFT",
    "WAITING",
    "SCHEDULED",
    "PUBLISHING",
    "PUBLISHED",
    "EXPORTED",
    "FAILED",
    "CANCELLED",
    "NEED_REVIEW",
}

TERMINAL_PUBLISH_STATUSES = {"PUBLISHED", "EXPORTED", "CANCELLED"}
ACTIVE_PUBLISH_STATUSES = PUBLISH_STATUSES - TERMINAL_PUBLISH_STATUSES


def validate_target_platform(platform: str) -> str:
    value = (platform or "").strip().lower()
    if value not in TARGET_PLATFORMS:
        raise ValueError("目标平台只能是 douyin 或 bilibili")
    return value


def validate_publish_mode(publish_mode: str) -> str:
    value = (publish_mode or "").strip().lower()
    if value not in PUBLISH_MODES:
        raise ValueError("不支持的发布执行方式")
    return value
