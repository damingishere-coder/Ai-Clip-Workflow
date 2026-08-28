"""发布前置条件计算；页面、排期和立即发送共用同一结果。"""

from __future__ import annotations

from typing import Any

from app.db.database import get_connection
from app.services.publish_copy_rules import validate_douyin_publish_copy
from app.services.publishers.base import PublishError, PublishValidationError


SAFE_PREFLIGHT_REPAIR_CODES = {
    "legacy_schedule_requires_confirmation",
    "opencli_fallback_disabled",
}


class SendReadinessBlocked(ValueError):
    """发送条件不足，并携带可供页面直接展示的结构化原因。"""

    def __init__(self, readiness: dict[str, Any]) -> None:
        super().__init__(str(readiness.get("message") or "发布条件尚未满足"))
        self.readiness = readiness


class PublishPlatformIsolationBlocked(ValueError):
    """所选任务跨越平台或试图修改任务所属平台。"""


def list_account_snapshots() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, platform, account_name, auth_type, login_status,
                   login_message, login_checked_at, last_login_at
            FROM publish_accounts
            ORDER BY created_at, id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _issue(code: str, message: str, action: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "action": action, **details}


def _content_issues(job: dict[str, Any], platform: str, publish_mode: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    caption = str(job.get("caption") or job.get("description") or "").strip()
    hashtags = str(job.get("hashtags") or job.get("tags") or "").strip()
    checks = [
        ("title", str(job.get("title") or "").strip(), "标题"),
        ("caption", caption, "正文/简介"),
        ("video", str(job.get("video_path") or job.get("video_file_path") or "").strip(), "视频文件"),
    ]
    if publish_mode == "local_browser":
        checks.extend(
            [
                ("hashtags", hashtags, "话题/标签"),
                ("cover", str(job.get("cover_file_path") or "").strip(), "封面"),
            ]
        )
    missing = [label for _, value, label in checks if not value]
    if platform == "bilibili" and publish_mode == "local_browser":
        if not str(job.get("bilibili_tid") or "").strip():
            missing.append("B站分区")
        if str(job.get("bilibili_copyright") or "original") == "repost" and not str(
            job.get("bilibili_source") or ""
        ).strip():
            missing.append("转载来源")
    if missing:
        issues.append(
            _issue(
                "content_incomplete",
                f"请先补充：{'、'.join(missing)}",
                "complete_content",
                missing_fields=missing,
            )
        )
    if platform == "douyin" and publish_mode == "local_browser":
        title = str(job.get("title") or "").strip()
        if title and caption and hashtags:
            try:
                validate_douyin_publish_copy(title, caption, hashtags)
            except ValueError as exc:
                issues.append(
                    _issue(
                        "content_invalid",
                        str(exc),
                        "complete_content",
                        invalid_fields=["标题", "正文/简介", "话题/标签"],
                    )
                )
    return issues


def _subtitle_delivery_issues(job: dict[str, Any]) -> list[dict[str, Any]]:
    provider_payload = job.get("provider_payload") if isinstance(job.get("provider_payload"), dict) else {}
    delivery_mode = str(
        job.get("subtitle_delivery_mode")
        or provider_payload.get("subtitle_delivery_mode")
        or ""
    )
    video_source = str(job.get("video_source") or "original")
    if delivery_mode == "original" and video_source != "original":
        return [
            _issue(
                "subtitle_delivery_mismatch",
                "该任务已明确选择原片继续，当前视频源不一致",
                "complete_content",
            )
        ]
    if delivery_mode == "subtitled" and video_source != "subtitled":
        return [
            _issue(
                "subtitle_review_required",
                "该任务要求使用审核后的字幕成片，当前仍是原片",
                "complete_content",
            )
        ]
    if video_source != "subtitled":
        return []
    revision_id = str(job.get("subtitle_revision_id") or provider_payload.get("subtitle_revision_id") or "")
    revision_status = str(
        job.get("subtitle_revision_status")
        or provider_payload.get("subtitle_revision_status")
        or ""
    )
    validation_status = str(
        job.get("subtitle_validation_status")
        or provider_payload.get("subtitle_validation_status")
        or ""
    )
    if not revision_id or revision_status != "approved" or validation_status != "verified":
        return [
            _issue(
                "subtitle_not_verified",
                "带字幕成片缺少已审核 revision 或 FFprobe 验证证据",
                "complete_content",
            )
        ]
    return []


def build_send_readiness(
    job: dict[str, Any],
    *,
    accounts: list[dict[str, Any]] | None = None,
    resolve_legacy: bool = False,
    validate_files: bool = False,
    worker_available: bool | None = None,
    worker_message: str = "",
) -> dict[str, Any]:
    """返回发送就绪状态；本函数不修改任务和账号。"""

    account_rows = accounts if accounts is not None else list_account_snapshots()
    status = str(job.get("status") or "").upper()
    platform = str(job.get("platform") or "").strip().lower()
    original_mode = str(job.get("publish_mode") or "").strip().lower()
    resolved_mode = "local_browser" if original_mode == "opencli_publish" else original_mode
    needs_legacy_conversion = original_mode == "opencli_publish"
    issues: list[dict[str, Any]] = []

    if platform not in {"douyin", "bilibili"}:
        issues.append(_issue("unsupported_platform", "当前平台不支持真实投稿", "complete_content"))

    if resolved_mode not in {"local_browser", "manual_export"}:
        issues.append(_issue("unsupported_publish_mode", "当前发布方式不受支持", "complete_content"))

    if needs_legacy_conversion and not resolve_legacy:
        issues.append(
            _issue(
                "legacy_publish_mode",
                "旧版发送方式将在发送前转换为 Windows Chrome",
                "convert_and_send",
            )
        )

    resolved_account: dict[str, Any] | None = None
    auto_selected = False
    if resolved_mode == "local_browser" and platform in {"douyin", "bilibili"}:
        account_id = str(job.get("account_id") or "").strip()
        if account_id:
            resolved_account = next((item for item in account_rows if str(item.get("id")) == account_id), None)
            if not resolved_account:
                issues.append(_issue("account_not_found", "原发布账号已不存在，请重新选择", "select_account"))
        else:
            matching = [item for item in account_rows if str(item.get("platform") or "") == platform]
            if not matching:
                issues.append(
                    _issue(
                        "account_missing",
                        f"还没有可用的{'抖音' if platform == 'douyin' else 'B站'}账号",
                        "create_account",
                        platform=platform,
                    )
                )
            elif len(matching) > 1:
                issues.append(
                    _issue(
                        "account_selection_required",
                        "检测到多个同平台账号，请先选择本次使用的账号",
                        "select_account",
                        platform=platform,
                    )
                )
            else:
                resolved_account = matching[0]
                auto_selected = True

        if resolved_account:
            if str(resolved_account.get("platform") or "") != platform:
                issues.append(_issue("account_platform_mismatch", "账号与目标平台不一致", "select_account"))
            elif str(resolved_account.get("login_status") or "login_required") != "normal":
                issues.append(
                    _issue(
                        "account_login_required",
                        f"账号“{resolved_account.get('account_name') or '未命名账号'}”尚未登录",
                        "login_account",
                        account_id=str(resolved_account.get("id") or ""),
                        account_name=str(resolved_account.get("account_name") or ""),
                        login_message=str(resolved_account.get("login_message") or ""),
                    )
                )

    issues.extend(_subtitle_delivery_issues(job))
    issues.extend(_content_issues(job, platform, resolved_mode))

    if resolved_mode == "local_browser" and worker_available is False:
        issues.append(
            _issue(
                "publish_worker_unavailable",
                worker_message or "Windows 发布 Worker 未连接",
                "start_worker",
            )
        )

    resolved_account_id = str(resolved_account.get("id") or "") if resolved_account else ""
    dispatch_issues = [item for item in issues if item["code"] != "legacy_publish_mode"]
    can_auto_resolve = needs_legacy_conversion and not dispatch_issues

    if validate_files and not dispatch_issues:
        candidate = {
            **job,
            "publish_mode": resolved_mode,
            "account_id": resolved_account_id or job.get("account_id") or "",
        }
        try:
            if resolved_mode == "local_browser":
                from app.services.publishers.local_browser import LocalBrowserPublisher

                LocalBrowserPublisher(platform=platform).validate(candidate)
            elif resolved_mode == "manual_export":
                from app.services.publishers.manual_export import ManualExportPublisher

                ManualExportPublisher().validate(candidate)
        except PublishError as exc:
            issue = _issue(exc.error_code or "content_invalid", exc.message, "complete_content")
            issues.append(issue)
            dispatch_issues.append(issue)
            can_auto_resolve = False

    repairable = (
        status == "NEED_REVIEW"
        and str(job.get("error_code") or "") in SAFE_PREFLIGHT_REPAIR_CODES
        and not str(job.get("platform_url") or "").strip()
        and not str(job.get("remote_video_id") or "").strip()
    )

    action_priority = {
        "start_worker": 1,
        "create_account": 2,
        "select_account": 3,
        "login_account": 4,
        "complete_content": 5,
        "convert_and_send": 6,
    }
    primary = min(issues, key=lambda item: action_priority.get(str(item.get("action")), 99)) if issues else None
    ready = not issues
    dispatch_ready = not dispatch_issues
    message = "发送条件已满足"
    if primary:
        message = str(primary.get("message") or "发布条件尚未满足")

    return {
        "ready": ready,
        "dispatch_ready": dispatch_ready,
        "message": message,
        "action": str(primary.get("action") if primary else ("export" if resolved_mode == "manual_export" else "send")),
        "issues": issues,
        "requires_worker": resolved_mode == "local_browser",
        "original_publish_mode": original_mode,
        "resolved_publish_mode": resolved_mode,
        "resolved_account_id": resolved_account_id,
        "resolved_account_name": str(resolved_account.get("account_name") or "") if resolved_account else "",
        "auto_selected_account": auto_selected,
        "needs_legacy_conversion": needs_legacy_conversion,
        "can_auto_resolve": can_auto_resolve,
        "repairable": repairable,
    }


def worker_blocked_readiness(message: str) -> dict[str, Any]:
    issue = _issue("publish_worker_unavailable", message, "start_worker")
    return {
        "ready": False,
        "dispatch_ready": False,
        "message": message,
        "action": "start_worker",
        "issues": [issue],
        "requires_worker": True,
        "original_publish_mode": "local_browser",
        "resolved_publish_mode": "local_browser",
        "resolved_account_id": "",
        "resolved_account_name": "",
        "auto_selected_account": False,
        "needs_legacy_conversion": False,
        "can_auto_resolve": False,
        "repairable": False,
    }


def require_worker_available(worker_client: Any) -> None:
    try:
        health = worker_client.health()
        if str(health.get("status") or "") != "ok":
            raise PublishValidationError("Windows 发布 Worker 健康检查异常", "publish_worker_unavailable")
    except PublishError as exc:
        raise SendReadinessBlocked(worker_blocked_readiness(exc.message)) from exc
