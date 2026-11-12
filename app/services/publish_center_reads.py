"""Bounded, read-only queries for the publish workbench."""

import calendar
from datetime import datetime, timedelta, timezone

from app.db.database import get_connection
from app.services import publish_service as service


def _filter(section="content", platform="douyin", task_id="", status="", q=""):
    if section not in {"content", "schedule"} or platform != "douyin":
        raise ValueError("不支持的发送中心区域或平台")
    conditions = ["p.platform = ?", "COALESCE(o.is_active, 1) = 1"]
    params = [platform]
    statuses = (
        "'DRAFT','WAITING','SCHEDULED'"
        if section == "content"
        else "'WAITING','SCHEDULED'"
    )
    conditions.append(f"p.status IN ({statuses})")
    if task_id:
        conditions.append("p.task_id = ?")
        params.append(task_id)
    if status == "missed":
        conditions.append("p.error_code = 'schedule_missed'")
    elif status:
        conditions.append("p.status = ?")
        params.append(status)
    if q:
        conditions.append(
            "(instr(lower(p.title),lower(?))>0 OR instr(lower(COALESCE(t.task_name,'')),lower(?))>0)"
        )
        params.extend([q, q])
    return " AND ".join(conditions), params


_FROM = " FROM publish_jobs p LEFT JOIN output_clip o ON o.id=p.output_clip_id LEFT JOIN tasks t ON t.id=p.task_id "


def ordering(section="content"):
    if section == "schedule":
        return "CASE WHEN p.error_code='schedule_missed' THEN 0 ELSE 1 END, CASE WHEN COALESCE(p.scheduled_at,'')='' THEN 1 ELSE 0 END, p.scheduled_at ASC, p.created_at DESC, p.id DESC"
    return "p.created_at DESC,p.id DESC"


def selection(**filters):
    where, params = _filter(**filters)
    with get_connection() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT p.id,p.account_id,p.platform,p.status"
                + _FROM
                + "WHERE "
                + where
                + " ORDER BY "
                + ordering(filters.get("section", "content")),
                params,
            )
        ]


def summary(**filters):
    where, params = _filter(**filters)
    with get_connection() as c:
        missed = c.execute(
            "SELECT COUNT(*)"
            + _FROM
            + "WHERE "
            + where
            + " AND p.error_code='schedule_missed'",
            params,
        ).fetchone()[0]
        counts = {
            r["status"]: r["n"]
            for r in c.execute(
                "SELECT p.status,COUNT(*) n"
                + _FROM
                + "WHERE "
                + where
                + " GROUP BY p.status",
                params,
            )
        }
        missing = c.execute(
            "SELECT COUNT(*)"
            + _FROM
            + "WHERE "
            + where
            + " AND COALESCE(p.cover_file_path,'')=''",
            params,
        ).fetchone()[0]
    task_filters = {**filters, "task_id": ""}
    task_where, task_params = _filter(**task_filters)
    with get_connection() as c:
        tasks = [
            dict(r)
            for r in c.execute(
                "SELECT p.task_id,t.task_name,COUNT(*) count"
                + _FROM
                + "WHERE "
                + task_where
                + " GROUP BY p.task_id ORDER BY t.created_at DESC,p.task_id",
                task_params,
            )
        ]
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "missing_cover_count": missing,
        "tasks": tasks,
        "missed_count": missed,
    }


def query(*, page=1, page_size=50, worker_state=None, **filters):
    where, params = _filter(**filters)
    with get_connection() as c:
        total = c.execute(
            "SELECT COUNT(*)" + _FROM + "WHERE " + where, params
        ).fetchone()[0]
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(max(1, page), total_pages)
        ids = [
            r[0]
            for r in c.execute(
                "SELECT p.id"
                + _FROM
                + "WHERE "
                + where
                + " ORDER BY "
                + ordering(filters.get("section", "content"))
                + " LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            )
        ]
    jobs = service.list_publish_jobs(
        limit=None, job_ids=ids, worker_state=worker_state or {"worker_available": None}
    )
    mapped = {j["id"]: j for j in jobs}
    jobs = [mapped[i] for i in ids]
    # Raw worker payloads belong to event/detail reads, never to a list response.
    for job in jobs:
        for key in ("provider_response", "publish_result"):
            job.pop(key, None)
    return {
        "jobs": jobs,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }


def render_rows(jobs, section, *, expanded=False):
    from app.routers.pages import templates
    from app.services.content_review_service import (
        list_active_content_experiments_for_publish,
    )

    context = {
        "accounts": service.list_accounts(),
        "content_experiments": list_active_content_experiments_for_publish()
        if expanded
        else [],
    }
    module = templates.get_template("_publish_rows.html").make_module(context)
    if section == "content" and not expanded:
        return templates.get_template("_publish_content_groups.html").render(
            **context, publish_task_groups=service._build_publish_task_groups(jobs)
        )
    macro = module.content_row if section == "content" else module.schedule_row
    return "".join(
        str(macro(job, expanded)) if section == "content" else str(macro(job))
        for job in jobs
    )


def month_jobs(month, platform="douyin"):
    try:
        year, number = map(int, month.split("-"))
        start = datetime(year, number, 1, tzinfo=timezone(timedelta(hours=8)))
        end = start + timedelta(days=calendar.monthrange(year, number)[1])
    except (ValueError, TypeError):
        raise ValueError("月份格式应为 YYYY-MM") from None
    with get_connection() as c:
        rows = c.execute(
            """SELECT p.id,p.title,p.scheduled_at,a.account_name
            FROM publish_jobs p LEFT JOIN publish_accounts a ON a.id=p.account_id
            LEFT JOIN output_clip o ON o.id=p.output_clip_id
            WHERE p.platform=? AND p.status='SCHEDULED' AND COALESCE(o.is_active,1)=1
            AND p.scheduled_at>=? AND p.scheduled_at<? ORDER BY p.scheduled_at,p.id""",
            (
                platform,
                start.astimezone(timezone.utc).isoformat(),
                end.astimezone(timezone.utc).isoformat(),
            ),
        ).fetchall()
    return [dict(r) for r in rows]


def initial_context(focus_task_id):
    from app.services.publish_scheduler import _SCHEDULER_HEALTH
    from app.core.config import settings

    page = query(task_id=focus_task_id)
    counts = summary()
    with get_connection() as c:
        health_counts = dict(
            c.execute("""SELECT SUM(status='SCHEDULED') scheduled_count,
            SUM(status='PUBLISHING') publishing_count,
            SUM(status='WAITING' AND error_code='schedule_missed') missed_schedule_count FROM publish_jobs""").fetchone()
        )
    health = {
        **_SCHEDULER_HEALTH,
        **health_counts,
        "enabled": settings.publish_scheduler_enabled,
        "interval_seconds": settings.publish_scheduler_interval_seconds,
        "worker_available": None,
        "worker_message": "正在检测发布服务",
    }
    return {
        "publish_jobs": page["jobs"],
        "publish_task_groups": service._build_publish_task_groups(page["jobs"]),
        "missing_cover_counts": {"douyin": counts["missing_cover_count"]},
        "missing_cover_count": counts["missing_cover_count"],
        "accounts": service.list_accounts(),
        "content_experiments": [],
        "app_timezone": settings.app_timezone,
        "scheduler_health": health,
        "center_pagination": page["pagination"],
    }
