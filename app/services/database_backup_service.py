from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo


PUBLISH_MIGRATION_BACKUP_PREFIX = "workflow-before-publish-migration-"
PUBLISH_MIGRATION_BACKUP_SUFFIX = ".sqlite3"
PUBLISH_MIGRATION_BACKUP_GLOB = (
    f"{PUBLISH_MIGRATION_BACKUP_PREFIX}*{PUBLISH_MIGRATION_BACKUP_SUFFIX}"
)
PUBLISH_MIGRATION_JOURNAL_GLOB = f"{PUBLISH_MIGRATION_BACKUP_GLOB}-journal"
PUBLISH_MIGRATION_BACKUP_COOLDOWN = timedelta(hours=24)
PUBLISH_MIGRATION_BACKUP_KEEP_DAYS = 14
MEDIA_CLEANUP_BACKUP_PREFIX = "workflow-before-media-cleanup-"
BACKUP_TIMEZONE = ZoneInfo("Asia/Shanghai")


class BackupSafetyError(RuntimeError):
    """Raised when a cleanup or migration backup cannot be completed safely."""


@dataclass(frozen=True)
class BackupCleanupPlan:
    database_path: Path
    backup_dir: Path
    keep_files: tuple[Path, ...]
    delete_files: tuple[Path, ...]
    invalid_files: tuple[Path, ...]
    journal_files: tuple[Path, ...]

    @property
    def release_bytes(self) -> int:
        paths = (*self.delete_files, *self.journal_files)
        return sum(path.stat().st_size for path in paths if path.exists())


@dataclass(frozen=True)
class BackupCleanupResult:
    deleted_files: int
    released_bytes: int


def _finalize_portable_backup(connection: sqlite3.Connection) -> None:
    """把 Online Backup 结果固定为无需 WAL/SHM sidecar 的单文件快照。"""
    connection.commit()
    row = connection.execute("PRAGMA journal_mode = DELETE").fetchone()
    journal_mode = str(row[0]).lower() if row else ""
    if journal_mode != "delete":
        raise BackupSafetyError(f"备份无法切换为单文件 journal_mode：{journal_mode or 'unknown'}")
    connection.commit()


def sqlite_quick_check(database_path: Path) -> str:
    path = database_path.resolve()
    if not path.is_file():
        return "missing"
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "no_result"
    except sqlite3.Error as exc:
        return f"error: {exc}"
    finally:
        connection.close()


def sqlite_diagnostic_report(
    database_path: Path,
    *,
    deep: bool = False,
) -> dict[str, Any]:
    """只读检查数据库、迁移账本及已应用迁移的不变量。"""
    path = database_path.resolve()
    report: dict[str, Any] = {
        "status": "ok",
        "database_path": str(path),
        "deep": bool(deep),
        "readable": False,
        "integrity_check": "skipped",
        "foreign_key_violation_count": 0,
        "foreign_key_violations": [],
        "migration_count": 0,
        "migration_errors": [],
        "errors": [],
    }
    errors: list[str] = report["errors"]
    migration_errors: list[str] = report["migration_errors"]
    if not path.is_file():
        errors.append("数据库文件不存在")
        report["status"] = "error"
        return report

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro",
            uri=True,
            timeout=3,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("SELECT 1").fetchone()
        report["readable"] = True

        table_info = connection.execute(
            "PRAGMA table_info(schema_migrations)"
        ).fetchall()
        required_ledger_columns = {"version", "name", "checksum", "applied_at"}
        actual_ledger_columns = {str(row["name"]) for row in table_info}
        missing_columns = sorted(required_ledger_columns - actual_ledger_columns)
        if not table_info:
            migration_errors.append("缺少 schema_migrations 迁移账本")
        elif missing_columns:
            migration_errors.append(
                "迁移账本缺少字段：" + ", ".join(missing_columns)
            )
        else:
            version_column = next(
                row for row in table_info if row["name"] == "version"
            )
            if int(version_column["pk"] or 0) != 1:
                migration_errors.append("迁移账本 version 字段不是主键")

            ledger_rows = connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            report["migration_count"] = len(ledger_rows)

            # 延迟导入可避免 database.py 在加载本服务时产生循环导入。
            from app.db import database as database_module

            registered = {
                migration.version: migration
                for migration in database_module._registered_schema_migrations()
            }
            for row in ledger_rows:
                version = str(row["version"])
                migration = registered.get(version)
                if migration is None:
                    migration_errors.append(f"存在当前程序无法识别的迁移：{version}")
                    continue
                if str(row["name"]) != migration.name:
                    migration_errors.append(f"迁移 {version} 的名称与程序定义不一致")
                if str(row["checksum"]) != migration.checksum:
                    migration_errors.append(f"迁移 {version} 的 checksum 与程序定义不一致")
                    continue
                try:
                    migration.verify(connection)
                except Exception as exc:
                    migration_errors.append(f"迁移 {version} 校验失败：{exc}")

        if deep:
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            integrity_messages = [str(row[0]) for row in integrity_rows]
            report["integrity_check"] = (
                "ok" if integrity_messages == ["ok"] else "; ".join(integrity_messages)
            )
            if report["integrity_check"] != "ok":
                errors.append(f"integrity_check 失败：{report['integrity_check']}")

            foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            report["foreign_key_violation_count"] = len(foreign_key_rows)
            report["foreign_key_violations"] = [
                {
                    "table": str(row[0]),
                    "rowid": row[1],
                    "parent": str(row[2]),
                    "foreign_key_id": row[3],
                }
                for row in foreign_key_rows[:20]
            ]
            if foreign_key_rows:
                errors.append(f"foreign_key_check 发现 {len(foreign_key_rows)} 条异常")
    except sqlite3.Error as exc:
        errors.append(f"数据库读取失败：{exc}")
    except Exception as exc:
        errors.append(f"数据库诊断失败：{exc}")
    finally:
        if connection is not None:
            connection.close()

    errors.extend(migration_errors)
    if errors:
        report["status"] = "error"
    return report


def assert_sqlite_database_ready(
    database_path: Path,
    *,
    deep: bool = True,
    label: str = "数据库",
) -> dict[str, Any]:
    report = sqlite_diagnostic_report(database_path, deep=deep)
    if report["status"] != "ok":
        details = "；".join(str(item) for item in report["errors"])
        raise BackupSafetyError(f"{label}校验失败：{details}")
    return report


def _ensure_safe_backup_path(path: Path, backup_dir: Path) -> None:
    resolved_dir = backup_dir.resolve()
    absolute_parent = Path(os.path.abspath(path.parent))
    if path.is_symlink() or absolute_parent != resolved_dir:
        raise BackupSafetyError(f"备份路径越界或使用了符号链接：{path}")


def _completed_backup_files(backup_dir: Path) -> list[Path]:
    if not backup_dir.exists():
        return []
    files = [
        path
        for path in backup_dir.glob(PUBLISH_MIGRATION_BACKUP_GLOB)
        if path.is_file()
    ]
    for path in files:
        _ensure_safe_backup_path(path, backup_dir)
    return sorted(
        files,
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )


def _backup_day(path: Path) -> str:
    timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=BACKUP_TIMEZONE)
    return timestamp.date().isoformat()


def build_cleanup_plan(
    database_path: Path,
    backup_dir: Path,
    *,
    keep_days: int = PUBLISH_MIGRATION_BACKUP_KEEP_DAYS,
) -> BackupCleanupPlan:
    if keep_days < 1:
        raise ValueError("keep_days 必须大于或等于 1")

    database_path = database_path.resolve()
    backup_dir = backup_dir.resolve()
    integrity = sqlite_quick_check(database_path)
    if integrity != "ok":
        raise BackupSafetyError(f"主数据库完整性检查失败：{integrity}")

    files_by_day: dict[str, list[Path]] = {}
    for path in _completed_backup_files(backup_dir):
        files_by_day.setdefault(_backup_day(path), []).append(path)

    selected_by_day: dict[str, Path] = {}
    invalid_files: list[Path] = []
    for day, files in files_by_day.items():
        for path in files:
            if sqlite_quick_check(path) == "ok":
                selected_by_day[day] = path
                break
            invalid_files.append(path)
        if day not in selected_by_day:
            raise BackupSafetyError(f"{day} 没有任何通过完整性检查的备份，已中止清理")

    retained_days = set(sorted(selected_by_day, reverse=True)[:keep_days])
    keep_files = {
        path for day, path in selected_by_day.items() if day in retained_days
    }
    all_files = {path for files in files_by_day.values() for path in files}
    delete_files = all_files - keep_files

    journal_files: list[Path] = []
    if backup_dir.exists():
        for path in backup_dir.glob(PUBLISH_MIGRATION_JOURNAL_GLOB):
            if not path.is_file():
                continue
            _ensure_safe_backup_path(path, backup_dir)
            journal_files.append(path)

    def sort_key(path: Path) -> tuple[int, str]:
        return path.stat().st_mtime_ns, path.name

    return BackupCleanupPlan(
        database_path=database_path,
        backup_dir=backup_dir,
        keep_files=tuple(sorted(keep_files, key=sort_key)),
        delete_files=tuple(sorted(delete_files, key=sort_key)),
        invalid_files=tuple(sorted(set(invalid_files), key=sort_key)),
        journal_files=tuple(sorted(journal_files, key=sort_key)),
    )


def apply_cleanup_plan(
    plan: BackupCleanupPlan,
    *,
    progress_every: int = 10_000,
) -> BackupCleanupResult:
    if sqlite_quick_check(plan.database_path) != "ok":
        raise BackupSafetyError("删除前主数据库完整性检查失败，已中止清理")

    current_files = set(_completed_backup_files(plan.backup_dir))
    planned_files = {*plan.keep_files, *plan.delete_files}
    if current_files != planned_files:
        raise BackupSafetyError("备份目录在预演后发生变化，请重新生成清理计划")

    for path in plan.keep_files:
        if sqlite_quick_check(path) != "ok":
            raise BackupSafetyError(f"拟保留备份完整性检查失败：{path.name}")

    deleted_files = 0
    released_bytes = 0
    paths_to_delete = (*plan.delete_files, *plan.journal_files)
    for path in paths_to_delete:
        if not path.exists():
            continue
        _ensure_safe_backup_path(path, plan.backup_dir)
        size = path.stat().st_size
        path.unlink()
        deleted_files += 1
        released_bytes += size
        if progress_every > 0 and deleted_files % progress_every == 0:
            print(f"已安全删除 {deleted_files:,} 个文件……", flush=True)

    return BackupCleanupResult(
        deleted_files=deleted_files,
        released_bytes=released_bytes,
    )


def _recent_valid_backup(
    backup_dir: Path,
    *,
    now: datetime,
    cooldown: timedelta,
) -> Path | None:
    cutoff = now.timestamp() - cooldown.total_seconds()
    for path in _completed_backup_files(backup_dir):
        if path.stat().st_mtime < cutoff:
            break
        if sqlite_quick_check(path) == "ok":
            return path
    return None


def create_publish_migration_backup(
    database_path: Path,
    backup_dir: Path,
    *,
    now: datetime | None = None,
    cooldown: timedelta = PUBLISH_MIGRATION_BACKUP_COOLDOWN,
    keep_days: int = PUBLISH_MIGRATION_BACKUP_KEEP_DAYS,
) -> Path | None:
    database_path = database_path.resolve()
    backup_dir = backup_dir.resolve()
    now = now.astimezone(BACKUP_TIMEZONE) if now else datetime.now(BACKUP_TIMEZONE)
    backup_dir.mkdir(parents=True, exist_ok=True)

    if _recent_valid_backup(backup_dir, now=now, cooldown=cooldown):
        return None

    timestamp = now.strftime("%Y%m%d-%H%M%S-%f")
    unique_suffix = f"{os.getpid()}-{uuid4().hex[:8]}"
    final_path = backup_dir / (
        f"{PUBLISH_MIGRATION_BACKUP_PREFIX}{timestamp}-{unique_suffix}"
        f"{PUBLISH_MIGRATION_BACKUP_SUFFIX}"
    )
    temporary_path = final_path.with_name(
        f"{final_path.name}.tmp-{uuid4().hex}"
    )

    source_connection: sqlite3.Connection | None = None
    backup_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro",
            uri=True,
            timeout=10,
        )
        backup_connection = sqlite3.connect(str(temporary_path), timeout=10)
        source_connection.backup(backup_connection)
        _finalize_portable_backup(backup_connection)
        backup_connection.close()
        backup_connection = None
        source_connection.close()
        source_connection = None

        integrity = sqlite_quick_check(temporary_path)
        if integrity != "ok":
            raise BackupSafetyError(f"新备份完整性检查失败：{integrity}")
        os.replace(temporary_path, final_path)

        cleanup_plan = build_cleanup_plan(
            database_path,
            backup_dir,
            keep_days=keep_days,
        )
        apply_cleanup_plan(cleanup_plan, progress_every=0)
        return final_path
    except Exception as exc:
        if temporary_path.exists():
            temporary_path.unlink()
        if isinstance(exc, BackupSafetyError):
            raise
        raise BackupSafetyError(f"创建迁移前备份失败：{exc}") from exc
    finally:
        if backup_connection is not None:
            backup_connection.close()
        if source_connection is not None:
            source_connection.close()


def create_schema_migration_backup(database_path: Path, backup_dir: Path, label: str) -> Path:
    """使用 SQLite Online Backup API 创建一次带完整性检查的结构迁移备份。"""
    database_path = database_path.resolve()
    backup_dir = backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(character for character in label.lower() if character.isalnum() or character == "-")
    timestamp = datetime.now(BACKUP_TIMEZONE).strftime("%Y%m%d-%H%M%S-%f")
    final_path = backup_dir / f"workflow-before-{safe_label}-{timestamp}-{uuid4().hex[:8]}.sqlite3"
    temporary_path = final_path.with_suffix(final_path.suffix + ".tmp")
    source_connection: sqlite3.Connection | None = None
    backup_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True, timeout=10)
        backup_connection = sqlite3.connect(str(temporary_path), timeout=10)
        source_connection.backup(backup_connection)
        _finalize_portable_backup(backup_connection)
        backup_connection.close()
        backup_connection = None
        source_connection.close()
        source_connection = None
        integrity = sqlite_quick_check(temporary_path)
        if integrity != "ok":
            raise BackupSafetyError(f"新备份完整性检查失败：{integrity}")
        os.replace(temporary_path, final_path)
        return final_path
    except Exception as exc:
        if temporary_path.exists():
            temporary_path.unlink()
        if isinstance(exc, BackupSafetyError):
            raise
        raise BackupSafetyError(f"创建结构迁移前备份失败：{exc}") from exc
    finally:
        if backup_connection is not None:
            backup_connection.close()
        if source_connection is not None:
            source_connection.close()


def create_media_cleanup_backup(
    database_path: Path,
    backup_dir: Path,
    *,
    now: datetime | None = None,
) -> Path:
    """永久删除任务媒体前，原子创建一份仅包含 SQLite 元数据的备份。"""
    database_path = database_path.resolve()
    backup_dir = backup_dir.resolve()
    now = now.astimezone(BACKUP_TIMEZONE) if now else datetime.now(BACKUP_TIMEZONE)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = now.strftime("%Y%m%d-%H%M%S-%f")
    final_path = backup_dir / (
        f"{MEDIA_CLEANUP_BACKUP_PREFIX}{timestamp}-{os.getpid()}-{uuid4().hex[:8]}.sqlite3"
    )
    temporary_path = final_path.with_name(f"{final_path.name}.tmp-{uuid4().hex}")
    source_connection: sqlite3.Connection | None = None
    backup_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro",
            uri=True,
            timeout=10,
        )
        backup_connection = sqlite3.connect(str(temporary_path), timeout=10)
        source_connection.backup(backup_connection)
        _finalize_portable_backup(backup_connection)
        backup_connection.close()
        backup_connection = None
        source_connection.close()
        source_connection = None

        integrity = sqlite_quick_check(temporary_path)
        if integrity != "ok":
            raise BackupSafetyError(f"媒体清理前备份完整性检查失败：{integrity}")
        os.replace(temporary_path, final_path)
        return final_path
    except Exception as exc:
        if temporary_path.exists():
            temporary_path.unlink()
        if isinstance(exc, BackupSafetyError):
            raise
        raise BackupSafetyError(f"创建媒体清理前备份失败：{exc}") from exc
    finally:
        if backup_connection is not None:
            backup_connection.close()
        if source_connection is not None:
            source_connection.close()
