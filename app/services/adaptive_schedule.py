"""Account-scoped, deterministic scheduling. No model or publisher calls here.

All writes are performed under BEGIN IMMEDIATE.
Official data imports never enqueue or execute replans.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import math
import statistics
import sqlite3

from app.db.database import get_connection
from app.services.publish_time import app_zone, parse_datetime, to_utc_iso, utc_now

TARGET_ACCOUNT_NAME = "康熙来了"


class ScheduleConflict(ValueError):
    """A changed task invalidates the whole batch and requires recalculation."""


DEFAULTS = dict(
    enabled=False,
    daily_limit=8,
    min_gap_minutes=90,
    daily_start_time="07:00",
    daily_end_time="23:59",
    version=0,
)
DDL = (
    """CREATE TABLE IF NOT EXISTS adaptive_schedule_policies (
        account_id TEXT PRIMARY KEY REFERENCES publish_accounts(id), enabled INTEGER NOT NULL DEFAULT 0,
        daily_limit INTEGER NOT NULL DEFAULT 8, min_gap_minutes INTEGER NOT NULL DEFAULT 90,
        daily_start_time TEXT NOT NULL DEFAULT '07:00', daily_end_time TEXT NOT NULL DEFAULT '23:59',
        version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS adaptive_schedule_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL REFERENCES publish_accounts(id),
        source_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, finished_at TEXT,
        UNIQUE(account_id, source_key))""",
    """CREATE TABLE IF NOT EXISTS adaptive_schedule_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL REFERENCES publish_accounts(id),
        job_id TEXT NOT NULL, request_id INTEGER REFERENCES adaptive_schedule_requests(id),
        old_time TEXT, new_time TEXT, reason TEXT NOT NULL, strategy_token TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_adaptive_requests_pending ON adaptive_schedule_requests(status, id)",
)
CHECKSUM = hashlib.sha256(
    (
        "\n".join(DDL)
        + "adaptive_managed INTEGER NOT NULL DEFAULT 0; adaptive_fixed INTEGER NOT NULL DEFAULT 0"
    ).encode()
).hexdigest()


def needs_migration(path):
    if not path.is_file() or path.stat().st_size == 0:
        return False
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as c:
        tables = {
            r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "schema_migrations" not in tables:
            return True
        return (
            c.execute(
                "SELECT 1 FROM schema_migrations WHERE version='20260906_01_adaptive_schedule'"
            ).fetchone()
            is None
        )


def migrate(connection):
    for sql in DDL:
        connection.execute(sql)
    if "adaptive_managed" not in {
        r[1] for r in connection.execute("PRAGMA table_info(publish_jobs)")
    }:
        connection.execute(
            "ALTER TABLE publish_jobs ADD COLUMN adaptive_managed INTEGER NOT NULL DEFAULT 0"
        )
    if "adaptive_fixed" not in {
        r[1] for r in connection.execute("PRAGMA table_info(publish_jobs)")
    }:
        connection.execute(
            "ALTER TABLE publish_jobs ADD COLUMN adaptive_fixed INTEGER NOT NULL DEFAULT 0"
        )


def verify_schema(connection):
    for table in (
        "adaptive_schedule_policies",
        "adaptive_schedule_requests",
        "adaptive_schedule_changes",
    ):
        connection.execute(f"SELECT * FROM {table} LIMIT 0")
    connection.execute(
        "SELECT adaptive_managed,adaptive_fixed FROM publish_jobs LIMIT 0"
    )


def _now():
    return utc_now().astimezone(app_zone("Asia/Shanghai"))


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _target(connection, account_id):
    row = connection.execute(
        "SELECT * FROM publish_accounts WHERE id=?", (account_id,)
    ).fetchone()
    if (
        not row
        or row["platform"] != "douyin"
        or row["account_name"] != TARGET_ACCOUNT_NAME
    ):
        raise ValueError("动态排期仅支持抖音「康熙来了」账号")
    return dict(row)


def policy(connection, account_id):
    _target(connection, account_id)
    row = connection.execute(
        "SELECT * FROM adaptive_schedule_policies WHERE account_id=?", (account_id,)
    ).fetchone()
    return dict(row) if row else {**DEFAULTS, "account_id": account_id}


def validate_options(options):
    result = {k: options.get(k, v) for k, v in DEFAULTS.items()}
    result["daily_limit"] = int(result["daily_limit"])
    result["min_gap_minutes"] = int(result["min_gap_minutes"])
    if (
        not 1 <= result["daily_limit"] <= 24
        or not 90 <= result["min_gap_minutes"] <= 1440
    ):
        raise ValueError("每天条数必须为 1–24，最小间隔必须为 90–1440 分钟")
    for key in ("daily_start_time", "daily_end_time"):
        value = str(result[key])
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("每日时间必须为 HH:MM") from exc
        result[key] = parsed.strftime("%H:%M")
    if result["daily_start_time"] >= result["daily_end_time"]:
        raise ValueError(
            "动态排期使用自然日窗口，结束时间须晚于开始时间；午夜请填 23:59"
        )
    return result


def enqueue(connection, account_id, source_key):
    if source_key.startswith("import:"):
        return  # Compatibility guard for old callers: imports only update metrics.
    row = connection.execute(
        "SELECT enabled FROM adaptive_schedule_policies WHERE account_id=?",
        (account_id,),
    ).fetchone()
    if row and row[0]:
        connection.execute(
            "INSERT OR IGNORE INTO adaptive_schedule_requests(account_id,source_key,created_at) VALUES(?,?,?)",
            (account_id, source_key, to_utc_iso(_now())),
        )


def _save_policy(connection, account_id, options, include_existing=False):
    current = policy(connection, account_id)
    values = validate_options({**current, **options})
    version = current["version"] + 1
    connection.execute(
        """INSERT INTO adaptive_schedule_policies
        (account_id,enabled,daily_limit,min_gap_minutes,daily_start_time,daily_end_time,version,updated_at)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET
        enabled=excluded.enabled,daily_limit=excluded.daily_limit,min_gap_minutes=excluded.min_gap_minutes,
        daily_start_time=excluded.daily_start_time,daily_end_time=excluded.daily_end_time,
        version=excluded.version,updated_at=excluded.updated_at""",
        (
            account_id,
            int(values["enabled"]),
            values["daily_limit"],
            values["min_gap_minutes"],
            values["daily_start_time"],
            values["daily_end_time"],
            version,
            to_utc_iso(_now()),
        ),
    )
    if values["enabled"] and include_existing:
        connection.execute(
            """UPDATE publish_jobs SET adaptive_managed=1,updated_at=?
            WHERE account_id=? AND platform='douyin' AND status='SCHEDULED'
            AND adaptive_managed=0 AND adaptive_fixed=0 AND claimed_at IS NULL AND COALESCE(needs_manual_review,0)=0""",
            (to_utc_iso(_now()), account_id),
        )
    enqueue(connection, account_id, f"policy:{version}")
    return policy(connection, account_id)


def save_policy(account_id, options, include_existing=False):
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        result = _save_policy(connection, account_id, options, include_existing)
        connection.commit()
    return result


def set_managed(job_id, managed):
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM publish_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if (
            not row
            or row["status"] != "SCHEDULED"
            or row["claimed_at"]
            or row["needs_manual_review"]
        ):
            raise ValueError("仅可固定或管理尚未执行的排期")
        _target(connection, row["account_id"])
        connection.execute(
            "UPDATE publish_jobs SET adaptive_managed=?,adaptive_fixed=?,updated_at=? WHERE id=?",
            (int(managed), int(not managed), to_utc_iso(_now()), job_id),
        )
        enqueue(connection, row["account_id"], f"managed:{job_id}:{_now().isoformat()}")
        connection.commit()


def _age_bucket(days):
    return 0 if days < 7 else 1 if days < 14 else 2 if days < 30 else 3


def _duration_bucket(seconds):
    return 0 if not seconds else 1 if seconds <= 60 else 2 if seconds <= 120 else 3


def score_times(connection, account_id, now=None):
    now = now or _now()
    rows = [
        dict(r)
        for r in connection.execute(
            """WITH ranked AS (
        SELECT i.*, ROW_NUMBER() OVER(PARTITION BY i.aweme_id ORDER BY i.captured_at DESC,i.created_at DESC,i.rowid DESC) rn
        FROM douyin_item_metric_snapshots i JOIN content_metric_import_batches b ON b.id=i.batch_id
        WHERE i.account_id=? AND b.status='committed' AND b.source_kind='douyin_item_export')
        SELECT * FROM ranked WHERE rn=1""",
            (account_id,),
        )
    ]
    latest = max((parse_datetime(r["captured_at"]) for r in rows), default=None)
    result = {
        "ready": False,
        "captured_at": latest.isoformat() if latest else None,
        "sample_count": 0,
        "reason": "作品数据不足，保留原排期；新排期使用测试基线",
        "bins": [],
    }
    if not latest or (now - latest).total_seconds() > 7 * 86400:
        result["reason"] = "尚无官方作品数据，或最新数据已超过 7 天；保留原排期"
        return result
    usable = []
    for row in rows:
        try:
            pub = parse_datetime(row["published_at"]).astimezone(
                app_zone("Asia/Shanghai")
            )
            captured = parse_datetime(row["captured_at"])
            age = (captured - pub).total_seconds() / 86400
            if (
                age < 3
                or pub > now
                or captured > now
                or (now - pub).days > 90
                or row["play_count"] is None
            ):
                continue
            row.update(
                pub=pub,
                age=_age_bucket(age),
                duration=_duration_bucket(row["duration_seconds"]),
                bin=pub.hour // 2,
                days=(now - pub).days,
            )
            usable.append(row)
        except (ValueError, TypeError):
            continue

    def comparable(data):
        output = []
        for row in data:
            cohort = [
                r
                for r in data
                if r["age"] == row["age"] and r["duration"] == row["duration"]
            ]
            if len(cohort) < 8:
                cohort = [r for r in data if r["age"] == row["age"]]
            if len(cohort) < 8:
                continue
            baseline = max(1, statistics.median(r["play_count"] for r in cohort))
            output.append(
                {**row, "ratio": min(4.0, max(0.25, row["play_count"] / baseline))}
            )
        return output

    eligible = comparable([r for r in usable if r["days"] <= 30])
    window = 30
    if len(eligible) < 30:
        eligible, window = comparable(usable), 90
    result.update(sample_count=len(eligible), window_days=window)
    if len(eligible) < 30:
        return result
    bins = []
    for b in range(12):
        group = [r for r in eligible if r["bin"] == b]
        n = len(group)
        raw = statistics.median(r["ratio"] for r in group) if group else 1.0
        score = 1 + (raw - 1) * n / (n + 5) if n >= 5 else 1.0
        bins.append(
            {
                "bin": b,
                "label": f"{b * 2:02d}:00–{b * 2 + 1:02d}:59",
                "count": n,
                "score": round(score, 6),
                "status": "待验证"
                if n < 5
                else "较强"
                if score >= 1.2
                else "偏弱"
                if score <= 0.8
                else "一般",
                "median_play": statistics.median(r["play_count"] for r in group)
                if group
                else None,
                "completion_rate": _median(group, "completion_rate"),
                "follower_gain_count": _median(group, "follower_gain_count"),
            }
        )
    result.update(
        ready=True,
        reason="按同发布年龄与片长比较播放中位表现；时段关系不等于因果",
        bins=bins,
    )
    return result


def _median(rows, key):
    values = [r[key] for r in rows if r.get(key) is not None]
    return statistics.median(values) if values else None


def _jobs(connection, account_id):
    return [
        dict(r)
        for r in connection.execute(
            "SELECT * FROM publish_jobs WHERE account_id=? AND platform='douyin'",
            (account_id,),
        )
    ]


def _time(row):
    value = (
        row.get("published_at")
        if row["status"] == "PUBLISHED"
        else row.get("scheduled_at")
    )
    return (
        parse_datetime(value).astimezone(app_zone("Asia/Shanghai")) if value else None
    )


def _occupied(rows, selected):
    return [
        (r["id"], _time(r))
        for r in rows
        if r["id"] not in selected
        and r["status"] in {"SCHEDULED", "PUBLISHING", "PUBLISHED", "NEED_REVIEW"}
        and _time(r)
    ]


def allocate(rows, all_rows, options, scores, start, now):
    """Deterministic constrained greedy allocation, with a feasibility pass.

    Exploration is rotated by date. Candidate selection prefers original slots
    on ties. Daily slots are sorted before assigning the original video order.
    """
    options = validate_options(options)
    selected = {r["id"] for r in rows}
    occupied = _occupied(all_rows, selected)
    protected = [
        r
        for r in rows
        if r["status"] == "SCHEDULED"
        and _time(r)
        and (_time(r).date() <= now.date() or not scores["ready"])
    ]
    occupied += [(r["id"], _time(r)) for r in protected]
    pending = [r for r in rows if r not in protected]
    result = [
        _entry(
            r,
            _time(r),
            "当天排期保持不变"
            if _time(r).date() <= now.date()
            else "数据不足，保留已有排期",
            scores,
        )
        for r in protected
    ]
    gap = timedelta(minutes=options["min_gap_minutes"])
    day = max(start.date(), now.date())
    end_day = day + timedelta(days=max(366, len(pending) * 2))
    original = {_time(r) for r in pending if _time(r)}
    exposure = Counter(
        t.hour // 2 for _, t in occupied if t >= now - timedelta(days=14)
    )
    bins = {r["bin"]: r for r in scores["bins"]}
    while pending and day <= end_day:
        fixed = [t for _, t in occupied if t.date() == day]
        quota = max(0, options["daily_limit"] - len(fixed))
        begin = parse_datetime(f"{day}T{options['daily_start_time']}", "Asia/Shanghai")
        end = parse_datetime(f"{day}T{options['daily_end_time']}", "Asia/Shanghai")
        candidates = []
        cursor = begin.replace(minute=(begin.minute // 30) * 30, second=0)
        if cursor < begin:
            cursor += timedelta(minutes=30)
        while cursor <= end:
            if (
                cursor >= start
                and cursor > now
                and all(abs(cursor - t) >= gap for _, t in occupied)
            ):
                candidates.append(cursor)
            cursor += timedelta(minutes=30)
        count = min(quota, len(pending))

        def capacity(available):
            total, previous = 0, None
            for t in sorted(available):
                if previous is None or t - previous >= gap:
                    total, previous = total + 1, t
            return total

        count = min(count, capacity(candidates))
        chosen = []
        exploration = (
            min(count, math.ceil(options["daily_limit"] / 4)) if scores["ready"] else 0
        )
        for index in range(count):
            testing = index >= count - exploration

            def rank(t):
                b = t.hour // 2
                if not scores["ready"]:
                    baseline = t.minute == 0 and t.hour in range(9, 24, 2)
                    return (int(baseline), int(t in original), -t.timestamp())
                if testing:
                    return (
                        -exposure[b],
                        -bins.get(b, {}).get("count", 0),
                        -((b - day.toordinal()) % 12),
                        int(t in original),
                        -t.timestamp(),
                    )
                return (
                    bins.get(b, {}).get("score", 1),
                    int(t in original),
                    -t.timestamp(),
                )

            feasible = [
                t
                for t in candidates
                if capacity([v for v in candidates if abs(v - t) >= gap])
                >= count - index - 1
            ]
            if not feasible:
                break
            picked = max(feasible, key=rank)
            chosen.append(
                (picked, "测试" if testing or not scores["ready"] else "优先")
            )
            exposure[picked.hour // 2] += 1
            candidates = [t for t in candidates if abs(t - picked) >= gap]
        for picked, reason in sorted(chosen):
            row = pending.pop(0)
            result.append(_entry(row, picked, reason, scores))
            occupied.append((row["id"], picked))
        day += timedelta(days=1)
    if pending:
        raise ValueError("发布窗口无法容纳任务，请扩大窗口或检查固定排期")
    order = {row["id"]: i for i, row in enumerate(rows)}
    return sorted(result, key=lambda item: order[item["job_id"]])


def _entry(row, time, reason, scores):
    b = next((b for b in scores["bins"] if b["bin"] == time.hour // 2), {})
    return dict(
        job_id=row["id"],
        scheduled_at_utc=to_utc_iso(time),
        scheduled_at_local=time.isoformat(timespec="seconds"),
        scheduled_at_local_display=time.strftime("%Y-%m-%d %H:%M"),
        timezone="Asia/Shanghai",
        reason=reason,
        sample_count=b.get("count", 0),
    )


def _preview(connection, ids, account_id, options, start, now):
    current = policy(connection, account_id)
    all_rows = _jobs(connection, account_id)
    row_map = {r["id"]: r for r in all_rows}
    if len(set(ids)) != len(ids) or any(i not in row_map for i in ids):
        raise ValueError("所选任务须属于同一个抖音账号，且不能重复")
    rows = [row_map[i] for i in ids]
    if any(
        r["status"] not in {"DRAFT", "WAITING", "SCHEDULED"}
        or r["claimed_at"]
        or r["needs_manual_review"]
        for r in rows
    ):
        raise ValueError("所选任务状态已变化，请刷新后重新预览")
    scores = score_times(connection, account_id, now)
    times = allocate(rows, all_rows, options, scores, start, now)
    # Includes protected/fixed occupancy and policy version. No remote content/secrets in token.
    fingerprint = [
        current["version"],
        options,
        scores,
        ids,
        start.isoformat(),
        now.date().isoformat(),
        [
            (
                r["id"],
                r["updated_at"],
                r["status"],
                r["scheduled_at"],
                r["adaptive_managed"],
            )
            for r in all_rows
        ],
    ]
    token = hashlib.sha256(_json(fingerprint).encode()).hexdigest()
    return {
        "status": "ok",
        "schedule": times,
        "strategy_token": token,
        "strategy": scores,
        "timezone": "Asia/Shanghai",
    }


def preview(ids, account_id, options, start_at_local):
    start = parse_datetime(start_at_local, "Asia/Shanghai")
    with get_connection() as connection:
        return _preview(
            connection, ids, account_id, validate_options(options), start, _now()
        )


def _write_changes(connection, account_id, schedule, token, request_id=None):
    now = to_utc_iso(_now())
    for item in schedule:
        row = connection.execute(
            "SELECT * FROM publish_jobs WHERE id=?", (item["job_id"],)
        ).fetchone()
        if item["reason"] in {"当天排期保持不变", "数据不足，保留已有排期"}:
            continue
        old_time, new_time = row["scheduled_at"], item["scheduled_at_utc"]
        if old_time == new_time and row["adaptive_managed"]:
            continue
        cursor = connection.execute(
            """UPDATE publish_jobs SET scheduled_at=?,schedule_timezone='Asia/Shanghai',
            timezone='Asia/Shanghai',adaptive_managed=1,adaptive_fixed=0,status='SCHEDULED',next_attempt_at=NULL,updated_at=?
            WHERE id=? AND account_id=? AND status IN ('DRAFT','WAITING','SCHEDULED')
            AND claimed_at IS NULL AND COALESCE(needs_manual_review,0)=0 AND updated_at=?""",
            (new_time, now, row["id"], account_id, row["updated_at"]),
        )
        if cursor.rowcount != 1:
            raise ScheduleConflict("排期并发冲突，整批未应用，请重新预览")
        connection.execute(
            """INSERT INTO adaptive_schedule_changes
            (account_id,job_id,request_id,old_time,new_time,reason,strategy_token,created_at) VALUES(?,?,?,?,?,?,?,?)""",
            (
                account_id,
                row["id"],
                request_id,
                old_time,
                new_time,
                item["reason"],
                token,
                now,
            ),
        )


def apply(ids, account_id, options, start_at_local, strategy_token, confirmed_schedule):
    from app.services.publish_scheduler import PublishScheduler, wake_scheduler

    scheduler = PublishScheduler()
    scheduler._validate_batch_jobs(ids, "douyin")
    scheduler._reject_legacy_schedule_apply(ids)
    scheduler._require_ready_jobs(ids, resolve_legacy=True, check_worker=True)
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        result = _preview(
            connection,
            ids,
            account_id,
            validate_options(options),
            parse_datetime(start_at_local, "Asia/Shanghai"),
            _now(),
        )
        expected = [(r["job_id"], r["scheduled_at_utc"]) for r in result["schedule"]]
        confirmed = [
            (r.get("job_id"), r.get("scheduled_at_utc")) for r in confirmed_schedule
        ]
        if strategy_token != result["strategy_token"] or expected != confirmed:
            raise ValueError("数据、设置或排期已变化，请重新预览后确认")
        _write_changes(connection, account_id, result["schedule"], strategy_token)
        current = policy(connection, account_id)
        _save_policy(
            connection,
            account_id,
            {**options, "enabled": True},
            include_existing=not current["enabled"],
        )
        connection.commit()
    wake_scheduler()
    return {
        **result,
        "updated_count": len(ids),
        "message": f"已保存 {len(ids)} 条动态排期",
        "jobs": [scheduler._public_job(i) for i in ids],
    }


def process_pending(limit=2):
    """At most a small batch per scheduler tick; never invokes a publisher."""
    results = []
    for _ in range(limit):
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            request = connection.execute(
                "SELECT * FROM adaptive_schedule_requests WHERE status='pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if not request:
                break
            now = _now()
            if request["source_key"].startswith("import:"):
                message = "导入联动已停用，保留当前排期；请到发送中心预览并确认调整"
                connection.execute(
                    "UPDATE adaptive_schedule_requests SET status='skipped',message=?,finished_at=? WHERE id=?",
                    (message, to_utc_iso(now), request["id"]),
                )
                connection.commit()
                results.append({"id": request["id"], "status": "skipped", "message": message})
                continue
            account_id = request["account_id"]
            try:
                options = policy(connection, account_id)
                scores = score_times(connection, account_id, now)
                rows = _jobs(connection, account_id)
                for row in rows:
                    if row["status"] in {
                        "SCHEDULED",
                        "PUBLISHING",
                        "PUBLISHED",
                        "NEED_REVIEW",
                    }:
                        _time(row)
            except (ValueError, TypeError) as exc:
                connection.execute(
                    "UPDATE adaptive_schedule_requests SET status='failed',message=?,attempts=attempts+1,finished_at=? WHERE id=?",
                    (str(exc), to_utc_iso(now), request["id"]),
                )
                connection.commit()
                results.append(
                    {"id": request["id"], "status": "failed", "message": str(exc)}
                )
                continue
            message = scores["reason"]
            status = "skipped"
            if options["enabled"] and scores["ready"]:
                rows = _jobs(connection, account_id)
                eligible = [
                    r
                    for r in rows
                    if r["status"] == "SCHEDULED"
                    and r["adaptive_managed"]
                    and not r["claimed_at"]
                    and not r["needs_manual_review"]
                    and _time(r)
                    and _time(r).date() > now.date()
                ]
                eligible.sort(key=lambda r: (_time(r), r["created_at"], r["id"]))
                tomorrow = (now + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                connection.execute("SAVEPOINT adaptive_replan")
                try:
                    schedule = allocate(eligible, rows, options, scores, tomorrow, now)
                    token = hashlib.sha256(
                        _json([options, scores, request["source_key"]]).encode()
                    ).hexdigest()
                    _write_changes(
                        connection, account_id, schedule, token, request["id"]
                    )
                    status, message = (
                        "completed",
                        f"已核对 {len(schedule)} 条未来排期，当天保持不变",
                    )
                    connection.execute("RELEASE adaptive_replan")
                except ScheduleConflict as exc:
                    connection.execute("ROLLBACK TO adaptive_replan")
                    connection.execute("RELEASE adaptive_replan")
                    status, message = "pending", str(exc)
                except ValueError as exc:
                    connection.execute("ROLLBACK TO adaptive_replan")
                    connection.execute("RELEASE adaptive_replan")
                    status, message = "failed", str(exc)
            elif not options["enabled"]:
                message = "自动调整已关闭，保留当前时间"
            connection.execute(
                "UPDATE adaptive_schedule_requests SET status=?,message=?,attempts=attempts+1,finished_at=? WHERE id=?",
                (
                    status,
                    message,
                    None if status == "pending" else to_utc_iso(now),
                    request["id"],
                ),
            )
            connection.commit()
            results.append({"id": request["id"], "status": status, "message": message})
            if status == "pending":
                break  # Recompute on the next scheduler tick, without a tight retry loop.
    return results


def context(account_id=None):
    with get_connection() as connection:
        if not account_id:
            found = connection.execute(
                "SELECT id FROM publish_accounts WHERE platform='douyin' AND account_name=?",
                (TARGET_ACCOUNT_NAME,),
            ).fetchall()
            if len(found) != 1:
                return {"available": False}
            account_id = found[0][0]
        current = policy(connection, account_id)
        changes = [
            dict(r)
            for r in connection.execute(
                "SELECT c.*,p.title AS title FROM adaptive_schedule_changes c LEFT JOIN publish_jobs p ON p.id=c.job_id WHERE c.account_id=? ORDER BY c.id DESC LIMIT 50",
                (account_id,),
            )
        ]
        requests = [
            dict(r)
            for r in connection.execute(
                "SELECT * FROM adaptive_schedule_requests WHERE account_id=? ORDER BY id DESC LIMIT 10",
                (account_id,),
            )
        ]
        return {
            "available": True,
            "policy": current,
            "strategy": score_times(connection, account_id),
            "changes": changes,
            "requests": requests,
        }
