from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.db.database import get_connection


MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ROWS = 10_000
MAX_IMPORT_COLUMNS = 50
PREVIEW_TTL_HOURS = 24
DAILY_SOURCE_KIND = "account_daily_file"
DOUYIN_SYNC_SOURCE_KIND = "douyin_item_sync"

DAILY_HEADERS = (
    "日期",
    "投稿量",
    "总播放量",
    "总点赞量",
    "总分享量",
    "总评论量",
    "5秒完播率",
    "2秒跳出率",
    "封面点击率",
    "平均播放时长",
)

COUNT_FIELDS = {
    "投稿量": "post_count",
    "总播放量": "play_count",
    "总点赞量": "like_count",
    "总分享量": "share_count",
    "总评论量": "comment_count",
}
RATE_FIELDS = {
    "5秒完播率": "five_second_completion_rate",
    "2秒跳出率": "two_second_bounce_rate",
    "封面点击率": "cover_click_rate",
}
REJECT_REASON_LABELS = {
    "not_funny": "不好笑",
    "missing_setup": "铺垫缺失",
    "fragmented": "片段不完整",
    "duplicate": "重复",
    "dragging": "节奏拖沓",
    "other": "其他",
    "worth_publishing": "保留",
}
MATCHED_STATUSES = {"matched_exact", "matched_unique", "confirmed_manual"}


class ContentReviewError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now().astimezone()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _resolve_douyin_account_id(account_id: str = "") -> str:
    normalized = str(account_id or "").strip()
    with get_connection() as connection:
        if normalized:
            row = connection.execute(
                "SELECT id FROM publish_accounts WHERE id = ? AND platform = 'douyin'",
                (normalized,),
            ).fetchone()
            if row is None:
                raise ContentReviewError("没有找到这个抖音账号，请先在发送中心添加账号")
            return normalized
        rows = connection.execute(
            "SELECT id FROM publish_accounts WHERE platform = 'douyin' ORDER BY created_at, id"
        ).fetchall()
    if len(rows) == 1:
        return str(rows[0]["id"])
    if not rows:
        raise ContentReviewError("还没有抖音账号，请先在发送中心添加账号")
    raise ContentReviewError("存在多个抖音账号，请先选择本次数据对应的账号")


def list_douyin_accounts() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, account_name, account_uid, login_status, login_checked_at
            FROM publish_accounts
            WHERE platform = 'douyin'
            ORDER BY created_at, id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _normalize_header(value) -> str:
    return re.sub(r"\s+", "", str(value or "").replace("\ufeff", "").strip())


def _trim_row(row: tuple | list) -> list:
    values = list(row)
    while values and (values[-1] is None or str(values[-1]).strip() == ""):
        values.pop()
    return values


def _load_xlsx_rows(content: bytes) -> list[list]:
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(
            io.BytesIO(content),
            read_only=True,
            data_only=True,
            keep_links=False,
        )
    except Exception as exc:
        raise ContentReviewError("Excel 文件无法读取，可能已损坏或不是有效的 .xlsx 文件") from exc

    matching_sheets: list[tuple[str, list[list]]] = []
    try:
        for worksheet in workbook.worksheets:
            if int(worksheet.max_column or 0) > MAX_IMPORT_COLUMNS:
                raise ContentReviewError(f"工作表“{worksheet.title}”超过 {MAX_IMPORT_COLUMNS} 列限制")
            rows: list[list] = []
            for raw_row in worksheet.iter_rows(values_only=True):
                row = _trim_row(raw_row)
                if not row or all(value is None or str(value).strip() == "" for value in row):
                    continue
                rows.append(row)
                if len(rows) > MAX_IMPORT_ROWS + 1:
                    raise ContentReviewError(f"工作表“{worksheet.title}”超过 {MAX_IMPORT_ROWS} 行数据限制")
            if not rows:
                continue
            normalized = {_normalize_header(value) for value in rows[0]}
            if set(DAILY_HEADERS) <= normalized:
                matching_sheets.append((worksheet.title, rows))
    finally:
        workbook.close()

    if not matching_sheets:
        raise ContentReviewError("没有找到包含完整抖音日汇总表头的工作表")
    if len(matching_sheets) > 1:
        names = "、".join(name for name, _ in matching_sheets)
        raise ContentReviewError(f"发现多个可导入工作表（{names}），请只保留一个数据工作表")
    return matching_sheets[0][1]


def _load_csv_rows(content: bytes) -> list[list]:
    decoded = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ContentReviewError("CSV 编码无法识别，请使用 UTF-8 或 GB18030")
    try:
        reader = csv.reader(io.StringIO(decoded, newline=""))
        rows = [_trim_row(row) for row in reader]
    except csv.Error as exc:
        raise ContentReviewError("CSV 文件格式损坏，无法读取") from exc
    rows = [row for row in rows if row and any(str(value).strip() for value in row)]
    if not rows:
        raise ContentReviewError("CSV 文件没有数据")
    if len(rows) - 1 > MAX_IMPORT_ROWS:
        raise ContentReviewError(f"CSV 超过 {MAX_IMPORT_ROWS} 行数据限制")
    if max(len(row) for row in rows) > MAX_IMPORT_COLUMNS:
        raise ContentReviewError(f"CSV 超过 {MAX_IMPORT_COLUMNS} 列限制")
    return rows


def _parse_date(value, *, row_number: int) -> str:
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = str(value or "").strip()
        parsed = None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
            try:
                parsed = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise ContentReviewError(f"第 {row_number} 行日期无法识别：{text or '空值'}")
    if parsed > _now().date():
        raise ContentReviewError(f"第 {row_number} 行日期晚于今天：{parsed.isoformat()}")
    return parsed.isoformat()


def _parse_count(value, *, header: str, row_number: int) -> int:
    text = ("" if value is None else str(value)).replace(",", "").strip()
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise ContentReviewError(f"第 {row_number} 行“{header}”不是有效数字") from exc
    if not math.isfinite(number) or number < 0 or number > 1_000_000_000_000:
        raise ContentReviewError(f"第 {row_number} 行“{header}”超出合理范围")
    if not number.is_integer():
        raise ContentReviewError(f"第 {row_number} 行“{header}”必须是整数")
    return int(number)


def _parse_rate(value, *, header: str, row_number: int) -> float:
    text = ("" if value is None else str(value)).replace(",", "").strip()
    is_percent = text.endswith("%")
    if is_percent:
        text = text[:-1].strip()
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise ContentReviewError(f"第 {row_number} 行“{header}”不是有效百分比") from exc
    if is_percent or number > 1:
        number /= 100
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ContentReviewError(f"第 {row_number} 行“{header}”必须在 0% 到 100% 之间")
    return round(number, 8)


def _parse_seconds(value, *, row_number: int) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    else:
        text = str(value or "").strip().lower().replace("秒", "").replace("s", "")
        try:
            seconds = float(text)
        except ValueError as exc:
            raise ContentReviewError(f"第 {row_number} 行“平均播放时长”不是有效秒数") from exc
    if not math.isfinite(seconds) or not 0 <= seconds <= 86_400:
        raise ContentReviewError(f"第 {row_number} 行“平均播放时长”超出合理范围")
    return round(seconds, 4)


def _normalize_daily_rows(rows: list[list]) -> list[dict]:
    if len(rows) < 2:
        raise ContentReviewError("文件只有表头，没有可导入的数据行")
    normalized_headers = [_normalize_header(value) for value in rows[0]]
    duplicates = sorted({header for header in normalized_headers if normalized_headers.count(header) > 1})
    if duplicates:
        raise ContentReviewError("表头存在重复列：" + "、".join(duplicates))
    missing = [header for header in DAILY_HEADERS if header not in normalized_headers]
    if missing:
        raise ContentReviewError("缺少必需表头：" + "、".join(missing))
    header_index = {header: normalized_headers.index(header) for header in DAILY_HEADERS}

    result: list[dict] = []
    seen_dates: set[str] = set()
    for row_number, row in enumerate(rows[1:], start=2):
        if not row or all(value is None or str(value).strip() == "" for value in row):
            continue

        def value_for(header: str):
            index = header_index[header]
            return row[index] if index < len(row) else None

        metric_date = _parse_date(value_for("日期"), row_number=row_number)
        if metric_date in seen_dates:
            raise ContentReviewError(f"日期重复：{metric_date}（第 {row_number} 行）")
        seen_dates.add(metric_date)
        item = {"metric_date": metric_date}
        for header, field in COUNT_FIELDS.items():
            item[field] = _parse_count(value_for(header), header=header, row_number=row_number)
        for header, field in RATE_FIELDS.items():
            item[field] = _parse_rate(value_for(header), header=header, row_number=row_number)
        item["average_watch_seconds"] = _parse_seconds(
            value_for("平均播放时长"),
            row_number=row_number,
        )
        result.append(item)
    if not result:
        raise ContentReviewError("没有可导入的有效数据行")
    result.sort(key=lambda item: item["metric_date"])
    return result


def preview_metric_import(*, account_id: str, filename: str, content: bytes) -> dict:
    resolved_account_id = _resolve_douyin_account_id(account_id)
    if not content:
        raise ContentReviewError("请选择要导入的数据文件")
    if len(content) > MAX_IMPORT_BYTES:
        raise ContentReviewError("文件超过 10MB 限制")
    safe_filename = Path(str(filename or "data")).name[:180]
    extension = Path(safe_filename).suffix.lower()
    if extension not in {".xlsx", ".csv"}:
        raise ContentReviewError("仅支持 .xlsx 和 .csv；不支持旧 .xls、宏文件或其他格式")
    rows = _load_xlsx_rows(content) if extension == ".xlsx" else _load_csv_rows(content)
    normalized_rows = _normalize_daily_rows(rows)
    source_sha256 = hashlib.sha256(content).hexdigest()
    now = _now()
    now_iso = now.isoformat(timespec="seconds")
    expires_at = (now + timedelta(hours=PREVIEW_TTL_HOURS)).isoformat(timespec="seconds")
    normalized_json = json.dumps(normalized_rows, ensure_ascii=False, separators=(",", ":"))

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT id, status, created_at, committed_at, expires_at
            FROM content_metric_import_batches
            WHERE account_id = ? AND source_kind = ? AND source_sha256 = ?
            """,
            (resolved_account_id, DAILY_SOURCE_KIND, source_sha256),
        ).fetchone()
        if existing is not None and existing["status"] == "committed":
            connection.commit()
            return {
                "status": "already_imported",
                "already_imported": True,
                "message": "这个账号已经导入过相同文件，无需重复导入。",
                "batch_id": existing["id"],
                "account_id": resolved_account_id,
                "row_count": len(normalized_rows),
                "period_start": normalized_rows[0]["metric_date"],
                "period_end": normalized_rows[-1]["metric_date"],
            }
        if existing is not None:
            batch_id = str(existing["id"])
            connection.execute(
                """
                UPDATE content_metric_import_batches
                SET source_filename = ?, status = 'previewed', period_start = ?, period_end = ?,
                    normalized_payload_json = ?, row_count = ?, invalid_count = 0,
                    created_at = ?, committed_at = NULL, expires_at = ?
                WHERE id = ?
                """,
                (
                    safe_filename,
                    normalized_rows[0]["metric_date"],
                    normalized_rows[-1]["metric_date"],
                    normalized_json,
                    len(normalized_rows),
                    now_iso,
                    expires_at,
                    batch_id,
                ),
            )
        else:
            batch_id = f"metric-{uuid4().hex[:16]}"
            connection.execute(
                """
                INSERT INTO content_metric_import_batches (
                    id, account_id, source_kind, source_filename, source_sha256, status,
                    period_start, period_end, normalized_payload_json, row_count,
                    invalid_count, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, 'previewed', ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    batch_id,
                    resolved_account_id,
                    DAILY_SOURCE_KIND,
                    safe_filename,
                    source_sha256,
                    normalized_rows[0]["metric_date"],
                    normalized_rows[-1]["metric_date"],
                    normalized_json,
                    len(normalized_rows),
                    now_iso,
                    expires_at,
                ),
            )
        connection.commit()
    return {
        "status": "previewed",
        "already_imported": False,
        "message": "预览校验通过；确认后才会写入账号级历史基线。",
        "batch_id": batch_id,
        "account_id": resolved_account_id,
        "filename": safe_filename,
        "row_count": len(normalized_rows),
        "period_start": normalized_rows[0]["metric_date"],
        "period_end": normalized_rows[-1]["metric_date"],
        "expires_at": expires_at,
        "sample_rows": normalized_rows[:5],
        "attribution": "unattributed_historical_baseline",
    }


def commit_metric_import(batch_id: str) -> dict:
    now = _now()
    now_iso = now.isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        batch = connection.execute(
            "SELECT * FROM content_metric_import_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        if batch is None:
            connection.rollback()
            raise ContentReviewError("导入预览不存在或已被清理", status_code=404)
        if batch["status"] == "committed":
            connection.commit()
            return {
                "status": "already_imported",
                "already_imported": True,
                "batch_id": batch_id,
                "message": "这批数据已经确认导入。",
            }
        if batch["status"] != "previewed":
            connection.rollback()
            raise ContentReviewError("这批预览当前不能确认导入", status_code=409)
        expires_at = datetime.fromisoformat(str(batch["expires_at"]))
        if expires_at < now:
            connection.execute(
                "UPDATE content_metric_import_batches SET status = 'expired' WHERE id = ?",
                (batch_id,),
            )
            connection.commit()
            raise ContentReviewError("预览已超过 24 小时，请重新选择文件预览", status_code=409)
        try:
            rows = json.loads(str(batch["normalized_payload_json"] or "[]"))
        except json.JSONDecodeError as exc:
            connection.rollback()
            raise ContentReviewError("预览数据损坏，请重新上传文件", status_code=409) from exc
        if not isinstance(rows, list) or not rows:
            connection.rollback()
            raise ContentReviewError("预览没有可导入数据，请重新上传文件", status_code=409)
        for row in rows:
            connection.execute(
                """
                INSERT INTO douyin_account_daily_metric_snapshots (
                    id, batch_id, account_id, metric_date, post_count, play_count,
                    like_count, share_count, comment_count, five_second_completion_rate,
                    two_second_bounce_rate, cover_click_rate, average_watch_seconds, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"daily-{uuid4().hex[:16]}",
                    batch_id,
                    batch["account_id"],
                    row["metric_date"],
                    row["post_count"],
                    row["play_count"],
                    row["like_count"],
                    row["share_count"],
                    row["comment_count"],
                    row["five_second_completion_rate"],
                    row["two_second_bounce_rate"],
                    row["cover_click_rate"],
                    row["average_watch_seconds"],
                    now_iso,
                ),
            )
        connection.execute(
            """
            UPDATE content_metric_import_batches
            SET status = 'committed', committed_at = ?, expires_at = NULL
            WHERE id = ?
            """,
            (now_iso, batch_id),
        )
        connection.commit()
    return {
        "status": "committed",
        "already_imported": False,
        "batch_id": batch_id,
        "row_count": len(rows),
        "message": f"已导入 {len(rows)} 天账号级数据，并标记为未归因历史基线。",
        "attribution": "unattributed_historical_baseline",
    }


def list_import_batches(account_id: str = "", limit: int = 20) -> list[dict]:
    resolved = _resolve_douyin_account_id(account_id)
    safe_limit = max(1, min(100, int(limit)))
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, account_id, source_kind, source_filename, source_sha256, status,
                   period_start, period_end, row_count, matched_count, ambiguous_count,
                   invalid_count, created_at, committed_at, expires_at
            FROM content_metric_import_batches
            WHERE account_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (resolved, safe_limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _aggregate_period(rows: list[dict]) -> dict:
    if not rows:
        return {
            "post_count": 0,
            "play_count": 0,
            "interaction_count": 0,
            "five_second_completion_rate": None,
            "two_second_bounce_rate": None,
            "cover_click_rate": None,
            "average_watch_seconds": None,
        }
    rate_fields = (
        "five_second_completion_rate",
        "two_second_bounce_rate",
        "cover_click_rate",
        "average_watch_seconds",
    )
    result = {
        "post_count": sum(int(row["post_count"] or 0) for row in rows),
        "play_count": sum(int(row["play_count"] or 0) for row in rows),
        "interaction_count": sum(
            int(row["like_count"] or 0)
            + int(row["share_count"] or 0)
            + int(row["comment_count"] or 0)
            for row in rows
        ),
    }
    for field in rate_fields:
        values = [float(row[field]) for row in rows if row[field] is not None]
        result[field] = round(sum(values) / len(values), 6) if values else None
    return result


def _comparison_delta(current, previous) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((float(current) - float(previous)) / abs(float(previous)), 6)


def get_content_review_summary(account_id: str = "", days: int = 28) -> dict:
    resolved = _resolve_douyin_account_id(account_id)
    safe_days = max(14, min(180, int(days)))
    with get_connection() as connection:
        rows = connection.execute(
            """
            WITH ranked AS (
                SELECT s.*, b.committed_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.metric_date
                           ORDER BY b.committed_at DESC, s.created_at DESC, s.rowid DESC
                       ) AS metric_rank
                FROM douyin_account_daily_metric_snapshots s
                JOIN content_metric_import_batches b ON b.id = s.batch_id
                WHERE s.account_id = ? AND b.status = 'committed'
            )
            SELECT * FROM ranked WHERE metric_rank = 1 ORDER BY metric_date ASC
            """,
            (resolved,),
        ).fetchall()
        sync_row = connection.execute(
            """
            SELECT MAX(committed_at) AS last_sync_at,
                   COUNT(*) AS completed_cycles
            FROM content_metric_import_batches
            WHERE account_id = ? AND status = 'committed'
            """,
            (resolved,),
        ).fetchone()
    history = [dict(row) for row in rows]
    if not history:
        return {
            "account_id": resolved,
            "has_data": False,
            "message": "还没有确认导入的账号级数据。",
            "last_sync_at": None,
            "days_since_sync": None,
            "completed_cycles": 0,
            "current_period": _aggregate_period([]),
            "previous_period": _aggregate_period([]),
            "comparisons": {},
            "history": [],
        }

    latest_date = date.fromisoformat(history[-1]["metric_date"])
    current_start = latest_date - timedelta(days=6)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=6)
    current_rows = [row for row in history if current_start <= date.fromisoformat(row["metric_date"]) <= latest_date]
    previous_rows = [
        row for row in history if previous_start <= date.fromisoformat(row["metric_date"]) <= previous_end
    ]
    current = _aggregate_period(current_rows)
    previous = _aggregate_period(previous_rows)
    comparisons = {
        key: _comparison_delta(current.get(key), previous.get(key))
        for key in current
    }
    last_sync_at = sync_row["last_sync_at"] if sync_row else None
    days_since = None
    if last_sync_at:
        days_since = max(0, (_now().date() - datetime.fromisoformat(str(last_sync_at)).date()).days)
    history_start = latest_date - timedelta(days=safe_days - 1)
    visible_history = [row for row in history if date.fromisoformat(row["metric_date"]) >= history_start]
    return {
        "account_id": resolved,
        "has_data": True,
        "latest_metric_date": latest_date.isoformat(),
        "last_sync_at": last_sync_at,
        "days_since_sync": days_since,
        "completed_cycles": int(sync_row["completed_cycles"] or 0),
        "current_period": current,
        "previous_period": previous,
        "comparisons": comparisons,
        "history": visible_history,
        "attribution": "unattributed_account_daily_baseline",
    }


def list_content_review_works(account_id: str = "", limit: int = 100) -> list[dict]:
    resolved = _resolve_douyin_account_id(account_id)
    safe_limit = max(1, min(200, int(limit)))
    with get_connection() as connection:
        rows = connection.execute(
            """
            WITH latest_items AS (
                SELECT i.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY i.aweme_id
                           ORDER BY i.captured_at DESC, i.created_at DESC, i.rowid DESC
                       ) AS item_rank
                FROM douyin_item_metric_snapshots i
                JOIN content_metric_import_batches b ON b.id = i.batch_id
                WHERE i.account_id = ? AND b.status = 'committed'
            )
            SELECT i.*, pj.title AS publish_title, pj.published_at AS job_published_at,
                   pj.status AS publish_status, pj.output_clip_id, oc.clip_candidate_id,
                   c.title AS candidate_title, c.source_analysis_run_id,
                   ar.run_number AS analysis_run_number, ar.provider_label, ar.model,
                   ar.prompt_version_id, pv.version_number AS prompt_version_number,
                   pv.preset_name_snapshot AS prompt_name,
                   (
                       SELECT f.decision FROM clip_feedback f
                       WHERE f.clip_candidate_id = c.id
                       ORDER BY f.created_at DESC, f.rowid DESC LIMIT 1
                   ) AS review_decision,
                   (
                       SELECT f.reason_code FROM clip_feedback f
                       WHERE f.clip_candidate_id = c.id
                       ORDER BY f.created_at DESC, f.rowid DESC LIMIT 1
                   ) AS review_reason_code
            FROM latest_items i
            LEFT JOIN publish_jobs pj ON pj.id = i.publish_job_id
            LEFT JOIN output_clip oc ON oc.id = pj.output_clip_id
            LEFT JOIN clip_candidates c ON c.id = oc.clip_candidate_id
            LEFT JOIN ai_analysis_runs ar ON ar.id = c.source_analysis_run_id
            LEFT JOIN ai_prompt_versions pv ON pv.id = ar.prompt_version_id
            WHERE i.item_rank = 1
            ORDER BY COALESCE(i.published_at, i.captured_at) DESC
            LIMIT ?
            """,
            (resolved, safe_limit),
        ).fetchall()
    works = []
    for row in rows:
        work = dict(row)
        work["review_reason_label"] = REJECT_REASON_LABELS.get(
            str(work.get("review_reason_code") or ""),
            str(work.get("review_reason_code") or ""),
        )
        work["attribution_complete"] = bool(
            work.get("publish_job_id")
            and work.get("clip_candidate_id")
            and work.get("source_analysis_run_id")
            and work.get("prompt_version_id")
        )
        works.append(work)
    return works


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, 6)


def get_prompt_comparison(account_id: str = "") -> dict:
    resolved = _resolve_douyin_account_id(account_id)
    with get_connection() as connection:
        candidate_rows = connection.execute(
            """
            SELECT pv.id AS prompt_version_id, pv.preset_id, pv.version_number,
                   pv.preset_name_snapshot, pv.created_at,
                   c.id AS candidate_id, c.enabled,
                   (
                       SELECT f.decision FROM clip_feedback f
                       WHERE f.clip_candidate_id = c.id
                       ORDER BY f.created_at DESC, f.rowid DESC LIMIT 1
                   ) AS latest_decision
            FROM ai_prompt_versions pv
            LEFT JOIN ai_analysis_runs ar ON ar.prompt_version_id = pv.id
            LEFT JOIN clip_candidates c ON c.source_analysis_run_id = ar.id AND c.is_deleted = 0
            ORDER BY pv.created_at, pv.preset_id, pv.version_number
            """
        ).fetchall()
        work_rows = connection.execute(
            """
            WITH latest_items AS (
                SELECT i.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY i.aweme_id
                           ORDER BY i.captured_at DESC, i.created_at DESC, i.rowid DESC
                       ) AS item_rank
                FROM douyin_item_metric_snapshots i
                JOIN content_metric_import_batches b ON b.id = i.batch_id
                WHERE i.account_id = ? AND b.status = 'committed'
                  AND i.match_status IN ('matched_exact', 'matched_unique', 'confirmed_manual')
            )
            SELECT i.*, c.id AS candidate_id, ar.prompt_version_id
            FROM latest_items i
            JOIN publish_jobs pj ON pj.id = i.publish_job_id
            JOIN output_clip oc ON oc.id = pj.output_clip_id
            JOIN clip_candidates c ON c.id = oc.clip_candidate_id
            JOIN ai_analysis_runs ar ON ar.id = c.source_analysis_run_id
            WHERE i.item_rank = 1 AND ar.prompt_version_id IS NOT NULL
            """,
            (resolved,),
        ).fetchall()
        cycle_row = connection.execute(
            """
            SELECT COUNT(*) FROM content_metric_import_batches
            WHERE account_id = ? AND status = 'committed'
            """,
            (resolved,),
        ).fetchone()

    groups: dict[str, dict] = {}
    for row in candidate_rows:
        version_id = str(row["prompt_version_id"])
        group = groups.setdefault(
            version_id,
            {
                "prompt_version_id": version_id,
                "preset_id": row["preset_id"],
                "version_number": int(row["version_number"]),
                "prompt_name": row["preset_name_snapshot"],
                "created_at": row["created_at"],
                "candidate_ids": set(),
                "kept_ids": set(),
                "published_candidate_ids": set(),
                "works": [],
            },
        )
        if row["candidate_id"]:
            candidate_id = str(row["candidate_id"])
            group["candidate_ids"].add(candidate_id)
            if row["latest_decision"] == "keep" or (
                row["latest_decision"] is None and int(row["enabled"] or 0) == 1
            ):
                group["kept_ids"].add(candidate_id)

    for row in work_rows:
        version_id = str(row["prompt_version_id"])
        group = groups.get(version_id)
        if group is None:
            continue
        group["works"].append(dict(row))
        group["published_candidate_ids"].add(str(row["candidate_id"]))

    versions = []
    for group in groups.values():
        candidate_count = len(group["candidate_ids"])
        works = group["works"]
        plays = [int(work["play_count"] or 0) for work in works]
        five_rates = [float(work["five_second_completion_rate"]) for work in works if work["five_second_completion_rate"] is not None]
        bounce_rates = [float(work["two_second_bounce_rate"]) for work in works if work["two_second_bounce_rate"] is not None]
        watch_ratios = [
            float(work["average_watch_seconds"]) / float(work["duration_seconds"])
            for work in works
            if work["average_watch_seconds"] is not None and float(work["duration_seconds"] or 0) > 0
        ]
        interactions = sum(
            int(work["like_count"] or 0)
            + int(work["comment_count"] or 0)
            + int(work["share_count"] or 0)
            for work in works
        )
        total_plays = sum(plays)
        versions.append(
            {
                "prompt_version_id": group["prompt_version_id"],
                "preset_id": group["preset_id"],
                "version_number": group["version_number"],
                "prompt_name": group["prompt_name"],
                "created_at": group["created_at"],
                "candidate_count": candidate_count,
                "accurate_published_count": len(works),
                "keep_rate": _safe_ratio(len(group["kept_ids"]), candidate_count),
                "publish_rate": _safe_ratio(len(group["published_candidate_ids"]), candidate_count),
                "median_play_count": statistics.median(plays) if plays else None,
                "five_second_completion_rate": statistics.fmean(five_rates) if five_rates else None,
                "two_second_bounce_rate": statistics.fmean(bounce_rates) if bounce_rates else None,
                "average_watch_ratio": statistics.fmean(watch_ratios) if watch_ratios else None,
                "interaction_rate": _safe_ratio(interactions, total_plays),
                "evaluable": len(works) >= 30,
            }
        )
    versions.sort(key=lambda item: (item["created_at"], item["preset_id"], item["version_number"]))
    comparisons = []
    for previous, current in zip(versions, versions[1:], strict=False):
        if previous["accurate_published_count"] < 20 or current["accurate_published_count"] < 20:
            continue
        comparisons.append(
            {
                "from_prompt_version_id": previous["prompt_version_id"],
                "to_prompt_version_id": current["prompt_version_id"],
                "note": "仅相关性，不代表因果；请结合选题、发布时间和样本结构判断。",
            }
        )
    completed_cycles = int(cycle_row[0] or 0) if cycle_row else 0
    return {
        "account_id": resolved,
        "completed_cycles": completed_cycles,
        "minimum_cycles": 3,
        "can_review_prompt": completed_cycles >= 3 and any(item["evaluable"] for item in versions),
        "versions": versions,
        "comparisons": comparisons,
        "message": (
            "已达到评估门槛，系统只提供建议，不会自动修改 Prompt。"
            if completed_cycles >= 3 and any(item["evaluable"] for item in versions)
            else "数据不足：默认需要 3 个完整周期，且当前 Prompt 至少 30 条准确关联作品。"
        ),
        "causality_notice": "所有对比仅表示相关性，不代表因果。",
    }


def set_item_match(snapshot_id: str, publish_job_id: str) -> dict:
    now = _now_iso()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        snapshot = connection.execute(
            "SELECT id, account_id FROM douyin_item_metric_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        if snapshot is None:
            connection.rollback()
            raise ContentReviewError("作品指标记录不存在", status_code=404)
        job = connection.execute(
            """
            SELECT id FROM publish_jobs
            WHERE id = ? AND platform = 'douyin' AND account_id = ?
            """,
            (publish_job_id, snapshot["account_id"]),
        ).fetchone()
        if job is None:
            connection.rollback()
            raise ContentReviewError("发布记录不存在，或不属于同一个抖音账号", status_code=409)
        connection.execute(
            """
            UPDATE douyin_item_metric_snapshots
            SET publish_job_id = ?, match_status = 'confirmed_manual',
                match_method = 'manual_confirmation', created_at = ?
            WHERE id = ?
            """,
            (publish_job_id, now, snapshot_id),
        )
        connection.commit()
    return {"status": "ok", "snapshot_id": snapshot_id, "publish_job_id": publish_job_id}


def delete_item_match(snapshot_id: str) -> dict:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE douyin_item_metric_snapshots
            SET publish_job_id = NULL, match_status = 'unmatched', match_method = NULL
            WHERE id = ?
            """,
            (snapshot_id,),
        )
        connection.commit()
    if cursor.rowcount != 1:
        raise ContentReviewError("作品指标记录不存在", status_code=404)
    return {"status": "ok", "snapshot_id": snapshot_id, "message": "已解除错误关联。"}


def normalize_title(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def match_douyin_item_with_connection(connection, *, account_id: str, item: dict) -> dict:
    aweme_id = str(item.get("aweme_id") or "").strip()
    exact_rows = connection.execute(
        """
        SELECT id FROM publish_jobs
        WHERE platform = 'douyin' AND account_id = ?
          AND (platform_item_id = ? OR remote_video_id = ?)
        """,
        (account_id, aweme_id, aweme_id),
    ).fetchall()
    if len(exact_rows) == 1:
        return {"status": "matched_exact", "method": "platform_item_id", "publish_job_id": exact_rows[0]["id"]}
    if len(exact_rows) > 1:
        return {"status": "ambiguous", "method": "platform_item_id", "publish_job_id": None}

    normalized_title = normalize_title(str(item.get("title") or ""))
    published_at = item.get("published_at")
    if not normalized_title or not published_at:
        return {"status": "unmatched", "method": None, "publish_job_id": None}
    try:
        item_time = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
    except ValueError:
        return {"status": "unmatched", "method": None, "publish_job_id": None}
    candidates = connection.execute(
        """
        SELECT pj.id, pj.title, pj.published_at, oc.source_duration_ms
        FROM publish_jobs pj
        LEFT JOIN output_clip oc ON oc.id = pj.output_clip_id
        WHERE pj.platform = 'douyin' AND pj.account_id = ? AND pj.published_at IS NOT NULL
        """,
        (account_id,),
    ).fetchall()
    matches = []
    for candidate in candidates:
        if normalize_title(str(candidate["title"] or "")) != normalized_title:
            continue
        try:
            candidate_time = datetime.fromisoformat(str(candidate["published_at"]).replace("Z", "+00:00"))
            seconds_apart = abs((candidate_time - item_time).total_seconds())
        except (TypeError, ValueError):
            continue
        if seconds_apart > 600:
            continue
        item_duration = float(item.get("duration_seconds") or 0)
        candidate_duration = float(candidate["source_duration_ms"] or 0) / 1000
        if item_duration and candidate_duration and abs(item_duration - candidate_duration) > 3:
            continue
        matches.append(candidate)
    if len(matches) == 1:
        return {"status": "matched_unique", "method": "title_time_duration", "publish_job_id": matches[0]["id"]}
    if len(matches) > 1:
        return {"status": "ambiguous", "method": "title_time_duration", "publish_job_id": None}
    return {"status": "unmatched", "method": None, "publish_job_id": None}


def stage_douyin_item_sync(*, account_id: str, items: list[dict], captured_at: str) -> dict:
    resolved = _resolve_douyin_account_id(account_id)
    if len(items) > 50:
        raise ContentReviewError("单次最多同步最近 50 条作品")
    normalized_items = []
    seen_ids = set()
    allowed_count_fields = ("play_count", "like_count", "comment_count", "share_count", "collect_count")
    allowed_rate_fields = (
        "five_second_completion_rate",
        "two_second_bounce_rate",
        "cover_click_rate",
        "average_watch_seconds",
    )
    for raw in items:
        aweme_id = str(raw.get("aweme_id") or "").strip()
        if not aweme_id or aweme_id in seen_ids:
            continue
        seen_ids.add(aweme_id)
        item = {
            "aweme_id": aweme_id[:120],
            "title": str(raw.get("title") or "")[:240],
            "published_at": raw.get("published_at"),
            "duration_seconds": raw.get("duration_seconds"),
        }
        for field in allowed_count_fields:
            value = raw.get(field)
            item[field] = max(0, int(value)) if value is not None else None
        for field in allowed_rate_fields:
            value = raw.get(field)
            item[field] = float(value) if value is not None else None
        normalized_items.append(item)
    payload_json = json.dumps(normalized_items, ensure_ascii=False, separators=(",", ":"))
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    batch_id = f"sync-{uuid4().hex[:16]}"
    now_iso = _now_iso()
    matched = ambiguous = 0
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT id FROM content_metric_import_batches
            WHERE account_id = ? AND source_kind = ? AND source_sha256 = ?
            """,
            (resolved, DOUYIN_SYNC_SOURCE_KIND, payload_hash),
        ).fetchone()
        if existing is not None:
            connection.commit()
            return {
                "status": "already_imported",
                "batch_id": existing["id"],
                "message": "最近作品指标没有变化，无需重复保存。",
            }
        connection.execute(
            """
            INSERT INTO content_metric_import_batches (
                id, account_id, source_kind, source_filename, source_sha256, status,
                normalized_payload_json, row_count, created_at, committed_at
            ) VALUES (?, ?, ?, 'douyin-worker', ?, 'committed', ?, ?, ?, ?)
            """,
            (batch_id, resolved, DOUYIN_SYNC_SOURCE_KIND, payload_hash, payload_json, len(normalized_items), now_iso, now_iso),
        )
        for item in normalized_items:
            match = match_douyin_item_with_connection(connection, account_id=resolved, item=item)
            if match["status"] in MATCHED_STATUSES:
                matched += 1
            elif match["status"] == "ambiguous":
                ambiguous += 1
            connection.execute(
                """
                INSERT INTO douyin_item_metric_snapshots (
                    id, batch_id, publish_job_id, account_id, aweme_id, title,
                    published_at, duration_seconds, captured_at, play_count, like_count,
                    comment_count, share_count, collect_count, five_second_completion_rate,
                    two_second_bounce_rate, cover_click_rate, average_watch_seconds,
                    match_status, match_method, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"item-{uuid4().hex[:16]}",
                    batch_id,
                    match["publish_job_id"],
                    resolved,
                    item["aweme_id"],
                    item["title"],
                    item["published_at"],
                    item["duration_seconds"],
                    captured_at,
                    item["play_count"],
                    item["like_count"],
                    item["comment_count"],
                    item["share_count"],
                    item["collect_count"],
                    item["five_second_completion_rate"],
                    item["two_second_bounce_rate"],
                    item["cover_click_rate"],
                    item["average_watch_seconds"],
                    match["status"],
                    match["method"],
                    now_iso,
                ),
            )
        connection.execute(
            """
            UPDATE content_metric_import_batches
            SET matched_count = ?, ambiguous_count = ? WHERE id = ?
            """,
            (matched, ambiguous, batch_id),
        )
        connection.commit()
    return {
        "status": "committed",
        "batch_id": batch_id,
        "row_count": len(normalized_items),
        "matched_count": matched,
        "ambiguous_count": ambiguous,
        "unmatched_count": len(normalized_items) - matched - ambiguous,
        "message": f"已同步 {len(normalized_items)} 条作品指标；存在歧义的记录等待人工确认。",
    }
