from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
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
