from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import sqlite3
from uuid import uuid4
import zipfile

from scripts import backup_restore as core


SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_BLOCKED_DATABASE_ENTRIES = {
    f"{core.DATABASE_ENTRY}{suffix}" for suffix in SQLITE_SIDECAR_SUFFIXES
}
_ORIGINAL_BUILD_FILE_MANIFEST = core._build_file_manifest
_ORIGINAL_VERIFY_BACKUP_BUNDLE = core.verify_backup_bundle


def _sqlite_sidecar_paths(database_path: Path) -> tuple[Path, ...]:
    return tuple(
        Path(f"{database_path}{suffix}") for suffix in SQLITE_SIDECAR_SUFFIXES
    )


def _remove_sqlite_sidecars(database_path: Path) -> None:
    for sidecar in _sqlite_sidecar_paths(database_path):
        if sidecar.exists():
            sidecar.unlink()


def _create_sqlite_snapshot(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise core.BackupRestoreError(f"未找到数据库：{source}")

    integrity = core.sqlite_quick_check(source)
    if integrity != "ok":
        raise core.BackupRestoreError(f"源数据库完整性检查失败：{integrity}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f"{destination.name}.partial-{uuid4().hex}"
    )
    source_connection: sqlite3.Connection | None = None
    target_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(
            f"{source.as_uri()}?mode=ro",
            uri=True,
            timeout=10,
        )
        target_connection = sqlite3.connect(str(temporary), timeout=10)
        source_connection.backup(target_connection)

        # SQLite backup 会复制数据库头中的 WAL 模式。Windows 上后续只读检查也
        # 可能创建 -wal / -shm 文件，因此在关闭连接前把快照固化为独立文件。
        target_connection.commit()
        mode_row = target_connection.execute("PRAGMA journal_mode = DELETE").fetchone()
        target_connection.commit()
        journal_mode = str(mode_row[0]).lower() if mode_row else ""
        if journal_mode != "delete":
            raise core.BackupRestoreError(
                f"无法将备份快照转换为独立日志模式：{journal_mode or 'unknown'}"
            )

        target_connection.close()
        target_connection = None
        source_connection.close()
        source_connection = None

        _remove_sqlite_sidecars(temporary)
        remaining = [
            sidecar.name
            for sidecar in _sqlite_sidecar_paths(temporary)
            if sidecar.exists()
        ]
        if remaining:
            raise core.BackupRestoreError(
                "SQLite 快照仍包含临时 sidecar：" + ", ".join(remaining)
            )

        snapshot_integrity = core.sqlite_quick_check(temporary)
        if snapshot_integrity != "ok":
            raise core.BackupRestoreError(
                f"新数据库快照完整性检查失败：{snapshot_integrity}"
            )
        os.replace(temporary, destination)
    except core.BackupRestoreError:
        raise
    except sqlite3.Error as exc:
        raise core.BackupRestoreError(f"创建 SQLite 快照失败：{exc}") from exc
    finally:
        if target_connection is not None:
            target_connection.close()
        if source_connection is not None:
            source_connection.close()
        _remove_sqlite_sidecars(temporary)
        if temporary.exists():
            temporary.unlink()


def _blocked_staged_entries(stage_root: Path) -> list[str]:
    blocked: list[str] = []
    for path in stage_root.rglob("*"):
        if not path.is_file():
            continue
        relative = PurePosixPath(path.relative_to(stage_root).as_posix())
        relative_text = str(relative)
        if relative_text in _BLOCKED_DATABASE_ENTRIES:
            blocked.append(relative_text)
    return sorted(blocked)


def _build_file_manifest(stage_root: Path) -> list[dict[str, object]]:
    blocked = _blocked_staged_entries(stage_root)
    if blocked:
        raise core.BackupRestoreError(
            "备份暂存目录包含 SQLite 临时文件：" + ", ".join(blocked)
        )
    return _ORIGINAL_BUILD_FILE_MANIFEST(stage_root)


def verify_backup_bundle(archive_path: Path) -> dict[str, object]:
    archive_path = archive_path.resolve()
    try:
        with zipfile.ZipFile(archive_path, mode="r") as archive:
            blocked = sorted(
                name for name in archive.namelist() if name in _BLOCKED_DATABASE_ENTRIES
            )
    except zipfile.BadZipFile as exc:
        raise core.BackupRestoreError(f"备份包不是有效 ZIP：{exc}") from exc

    if blocked:
        raise core.BackupRestoreError(
            "备份包包含 SQLite 临时 sidecar：" + ", ".join(blocked)
        )
    return _ORIGINAL_VERIFY_BACKUP_BUNDLE(archive_path)


# 保持原模块的公开 API 与 CLI，不复制恢复逻辑；只替换三个安全关键钩子。
core._create_sqlite_snapshot = _create_sqlite_snapshot
core._build_file_manifest = _build_file_manifest
core.verify_backup_bundle = verify_backup_bundle

BackupRestoreError = core.BackupRestoreError
create_backup_bundle = core.create_backup_bundle
restore_backup_bundle = core.restore_backup_bundle


def main() -> int:
    return core.main()


if __name__ == "__main__":
    raise SystemExit(main())
