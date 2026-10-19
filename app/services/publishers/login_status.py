"""登录检测结果归一化；未确认不等于登录失效。"""

from typing import Any


def normalize_login_status(result: dict[str, Any]) -> str:
    status = str(result.get("login_status") or result.get("status") or "").lower()
    if status in {"normal", "invalid", "login_required", "busy", "login_pending", "check_failed"}:
        return status
    return "check_failed"


def login_status_message(status: str) -> str:
    return {
        "normal": "登录状态正常",
        "invalid": "账号需要重新登录",
        "login_required": "账号需要重新登录",
        "busy": "账号正在操作，请稍后检查",
        "login_pending": "等待登录完成，请稍后检查",
    }.get(status, "暂时无法确认登录状态，请重新检查状态")
