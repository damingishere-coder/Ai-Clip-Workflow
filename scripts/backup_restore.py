from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import tempfile
from typing import BinaryIO, Iterable
from uuid import uuid4
import zipfile
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.database_backup_service import sqlite_quick_check


APP_VERSION = "2.1.0"
ARCHIVE_FORMAT_VERSION = 1
BACKUP_TIMEZONE = ZoneInfo("Asia/Shanghai")
MANIFEST_ENTRY = "manifest.json"
DATABASE_ENTRY = "database/workflow.sqlite3"
ENV_ENTRY = "config/.env"
MEDIA_PREFIX = "media/tasks/"
COUNT_TABLES = (
    "tasks",
    "clip_candidates",
    "output_clip",
    "publish_jobs",
)
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class BackupRestoreError(RuntimeError):
    """Raised when a backup or restore operation cannot be completed safely."""


def _now() -> datetime:
    return datetime.now(BACKUP_TIMEZONE)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_stream(file: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: file.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_name(name: str) -> PurePosixPath:
    normalized = PurePosixPath(name.replace("\\", "/"))
    if normalized.is_absolute() or not normalized.parts:
        raise BackupRestoreError(f"备份包包含非法路径：{name}")
    if any(part in {"", ".", ".."} for part in normalized.parts):
        raise BackupRestoreError(f"备份包包含越界路径：{name}")
    return normalized


def _git_commit(project_root: Path | None) -> str:
    if not project_root:
        return ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _table_counts(database_path: Path) -> dict[str, int]:
    database_path = database_path.resolve()
    connection = sqlite3.connect(
        f"{database_path.as_uri()}?mode=ro",
        uri=True,
        timeout=10,
    )
    try:
        existing = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = [table for table in COUNT_TABLES if table not in existing]
        if missing:
            raise BackupRestoreError(
                "数据库缺少必要表：" + ", ".join(missing)
            )
        return {
            table: int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in COUNT_TABLES
        }
    except sqlite3.Error as exc:
        raise BackupRestoreError(f"读取数据库统计失败：{exc}") from exc
    finally:
        connection.close()


def _create_sqlite_snapshot(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise BackupRestoreError(f"未找到数据库：{source}")
    integrity = sqlite_quick_check(source)
    if integrity != "ok":
        raise BackupRestoreError(f"源数据库完整性检查失败：{integrity}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.partial-{uuid4().hex}")
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
        target_connection.close()
        target_connection = None
        source_connection.close()
        source_connection = None

        snapshot_integrity = sqlite_quick_check(temporary)
        if snapshot_integrity != "ok":
            raise BackupRestoreError(
                f"新数据库快照完整性检查失败：{snapshot_integrity}"
            )
        os.replace(temporary, destination)
    except sqlite3.Error as exc:
        raise BackupRestoreError(f"创建 SQLite 快照失败：{exc}") from exc
    finally:
        if target_connection is not None:
            target_connection.close()
        if source_connection is not None:
            source_connection.close()
        if temporary.exists():
            temporary.unlink()


def _iter_media_files(media_root: Path) -> Iterable[tuple[Path, PurePosixPath]]:
    media_root = media_root.resolve()
    if not media_root.exists():
        return
    if media_root.is_symlink() or not media_root.is_dir():
        raise BackupRestoreError(f"媒体目录不是安全的普通目录：{media_root}")

    for path in sorted(media_root.rglob("*")):
        if path.is_symlink():
            raise BackupRestoreError(f"媒体目录包含符号链接，已中止：{path}")
        if not path.is_file():
            continue
        relative = path.relative_to(media_root)
        archive_name = PurePosixPath(MEDIA_PREFIX) / PurePosixPath(
            relative.as_posix()
        )
        yield path, archive_name


def _stage_file(
    source: Path,
    stage_root: Path,
    archive_name: PurePosixPath,
) -> Path:
    _safe_archive_name(str(archive_name))
    destination = stage_root.joinpath(*archive_name.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def _build_file_manifest(stage_root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(stage_root.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_ENTRY:
            continue
        relative = PurePosixPath(path.relative_to(stage_root).as_posix())
        entries.append(
            {
                "path": str(relative),
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return entries


def create_backup_bundle(
    *,
    database_path: Path,
    env_path: Path,
    backup_dir: Path,
    include_env: bool = True,
    include_media: bool = False,
    media_root: Path | None = None,
    label: str = "manual",
    project_root: Path | None = None,
) -> Path:
    if not LABEL_PATTERN.fullmatch(label):
        raise BackupRestoreError(
            "备份标签只能包含英文字母、数字、点、下划线和连字符"
        )
    if include_media and media_root is None:
        raise BackupRestoreError("包含媒体文件时必须提供 media_root")

    database_path = database_path.resolve()
    env_path = env_path.resolve()
    backup_dir = backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _now().strftime("%Y%m%d-%H%M%S")
    archive_path = backup_dir / f"niuma-studio-{label}-{timestamp}.zip"
    if archive_path.exists():
        archive_path = backup_dir / (
            f"niuma-studio-{label}-{timestamp}-{uuid4().hex[:8]}.zip"
        )
    partial_path = archive_path.with_name(
        f"{archive_path.name}.partial-{uuid4().hex}"
    )

    with tempfile.TemporaryDirectory(
        prefix="niuma-backup-",
        dir=backup_dir,
    ) as temp_dir:
        stage_root = Path(temp_dir)
        snapshot_path = stage_root.joinpath(*PurePosixPath(DATABASE_ENTRY).parts)
        _create_sqlite_snapshot(database_path, snapshot_path)
        counts = _table_counts(snapshot_path)

        env_included = include_env and env_path.is_file()
        if env_included:
            _stage_file(env_path, stage_root, PurePosixPath(ENV_ENTRY))

        media_file_count = 0
        media_bytes = 0
        if include_media and media_root is not None:
            for source, archive_name in _iter_media_files(media_root):
                staged = _stage_file(source, stage_root, archive_name)
                media_file_count += 1
                media_bytes += staged.stat().st_size

        manifest = {
            "archive_format_version": ARCHIVE_FORMAT_VERSION,
            "app_version": APP_VERSION,
            "git_commit": _git_commit(project_root),
            "created_at": _now().isoformat(),
            "label": label,
            "contains_secrets": env_included,
            "includes_env": env_included,
            "includes_media": bool(include_media),
            "media_file_count": media_file_count,
            "media_bytes": media_bytes,
            "database_entry": DATABASE_ENTRY,
            "environment_entry": ENV_ENTRY if env_included else "",
            "table_counts": counts,
            "files": _build_file_manifest(stage_root),
        }
        (stage_root / MANIFEST_ENTRY).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        try:
            with zipfile.ZipFile(
                partial_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                for path in sorted(stage_root.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(stage_root).as_posix())
            os.replace(partial_path, archive_path)
        finally:
            if partial_path.exists():
                partial_path.unlink()

    verify_backup_bundle(archive_path)
    return archive_path


def verify_backup_bundle(archive_path: Path) -> dict[str, object]:
    archive_path = archive_path.resolve()
    if not archive_path.is_file():
        raise BackupRestoreError(f"未找到备份包：{archive_path}")

    try:
        with zipfile.ZipFile(archive_path, mode="r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise BackupRestoreError("备份包包含重复文件名")
            for name in names:
                _safe_archive_name(name)
            if MANIFEST_ENTRY not in names:
                raise BackupRestoreError("备份包缺少 manifest.json")

            try:
                manifest = json.loads(
                    archive.read(MANIFEST_ENTRY).decode("utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupRestoreError(f"备份清单无法读取：{exc}") from exc

            if manifest.get("archive_format_version") != ARCHIVE_FORMAT_VERSION:
                raise BackupRestoreError(
                    "不支持的备份格式版本："
                    f"{manifest.get('archive_format_version')}"
                )

            file_entries = manifest.get("files")
            if not isinstance(file_entries, list):
                raise BackupRestoreError("备份清单 files 字段无效")

            expected_names = {MANIFEST_ENTRY}
            for entry in file_entries:
                if not isinstance(entry, dict):
                    raise BackupRestoreError("备份清单包含无效文件记录")
                name = str(entry.get("path") or "")
                _safe_archive_name(name)
                expected_names.add(name)
                if name not in names:
                    raise BackupRestoreError(f"备份包缺少清单文件：{name}")
                info = archive.getinfo(name)
                if int(entry.get("size") or -1) != info.file_size:
                    raise BackupRestoreError(f"文件大小校验失败：{name}")
                with archive.open(name, "r") as file:
                    actual_hash = _sha256_stream(file)
                if actual_hash != str(entry.get("sha256") or ""):
                    raise BackupRestoreError(f"文件哈希校验失败：{name}")

            unexpected = set(names) - expected_names
            if unexpected:
                raise BackupRestoreError(
                    "备份包包含清单外文件：" + ", ".join(sorted(unexpected))
                )
            if DATABASE_ENTRY not in names:
                raise BackupRestoreError("备份包缺少数据库快照")

            with tempfile.TemporaryDirectory(prefix="niuma-verify-") as temp_dir:
                database_path = Path(temp_dir) / "workflow.sqlite3"
                with (
                    archive.open(DATABASE_ENTRY, "r") as source,
                    database_path.open("wb") as target,
                ):
                    shutil.copyfileobj(source, target)
                integrity = sqlite_quick_check(database_path)
                if integrity != "ok":
                    raise BackupRestoreError(
                        f"备份数据库完整性检查失败：{integrity}"
                    )
                counts = _table_counts(database_path)
                if counts != manifest.get("table_counts"):
                    raise BackupRestoreError(
                        "备份数据库数量与清单不一致："
                        f"actual={counts}, expected={manifest.get('table_counts')}"
                    )
    except zipfile.BadZipFile as exc:
        raise BackupRestoreError(f"备份包不是有效 ZIP：{exc}") from exc

    return manifest


def _extract_archive_entry(
    archive: zipfile.ZipFile,
    entry_name: str,
    destination: Path,
) -> None:
    _safe_archive_name(entry_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        archive.open(entry_name, "r") as source,
        destination.open("wb") as target,
    ):
        shutil.copyfileobj(source, target)


def _remove_sqlite_sidecars(database_path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{database_path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()


def _atomic_restore_files(
    *,
    new_database: Path,
    database_path: Path,
    new_env: Path | None,
    env_path: Path,
) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if new_env is not None:
        env_path.parent.mkdir(parents=True, exist_ok=True)

    token = uuid4().hex
    staged_database = database_path.with_name(
        f"{database_path.name}.restore-new-{token}"
    )
    previous_database = database_path.with_name(
        f"{database_path.name}.restore-old-{token}"
    )
    staged_env = env_path.with_name(f"{env_path.name}.restore-new-{token}")
    previous_env = env_path.with_name(f"{env_path.name}.restore-old-{token}")

    shutil.copy2(new_database, staged_database)
    if new_env is not None:
        shutil.copy2(new_env, staged_env)

    database_moved = False
    env_moved = False
    try:
        if database_path.exists():
            os.replace(database_path, previous_database)
            database_moved = True
        if new_env is not None and env_path.exists():
            os.replace(env_path, previous_env)
            env_moved = True

        _remove_sqlite_sidecars(database_path)
        os.replace(staged_database, database_path)
        if new_env is not None:
            os.replace(staged_env, env_path)

        integrity = sqlite_quick_check(database_path)
        if integrity != "ok":
            raise BackupRestoreError(
                f"恢复后的数据库完整性检查失败：{integrity}"
            )
    except Exception:
        if database_path.exists():
            database_path.unlink()
        if database_moved and previous_database.exists():
            os.replace(previous_database, database_path)
        if new_env is not None:
            if env_path.exists():
                env_path.unlink()
            if env_moved and previous_env.exists():
                os.replace(previous_env, env_path)
        raise
    finally:
        for path in (staged_database, staged_env):
            if path.exists():
                path.unlink()

    if previous_database.exists():
        previous_database.unlink()
    if previous_env.exists():
        previous_env.unlink()


def _preflight_media_destination(destination: Path) -> tuple[Path, bool]:
    destination = destination.resolve()
    existed_empty = False
    if destination.exists():
        if not destination.is_dir():
            raise BackupRestoreError(
                f"媒体恢复目标不是目录：{destination}"
            )
        if any(destination.iterdir()):
            raise BackupRestoreError(
                f"媒体恢复目录必须为空，避免覆盖现有文件：{destination}"
            )
        existed_empty = True
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination, existed_empty


def _restore_media_before_database(
    archive: zipfile.ZipFile,
    destination: Path,
) -> tuple[int, bool]:
    destination, existed_empty = _preflight_media_destination(destination)
    temporary = destination.with_name(
        f"{destination.name}.restore-new-{uuid4().hex}"
    )
    temporary.mkdir(parents=True, exist_ok=False)
    restored = 0
    try:
        for name in archive.namelist():
            if not name.startswith(MEDIA_PREFIX) or name.endswith("/"):
                continue
            relative = PurePosixPath(name).relative_to(PurePosixPath(MEDIA_PREFIX))
            _safe_archive_name(str(relative))
            target = temporary.joinpath(*relative.parts)
            _extract_archive_entry(archive, name, target)
            restored += 1
        if destination.exists():
            destination.rmdir()
        os.replace(temporary, destination)
        return restored, existed_empty
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        if existed_empty and not destination.exists():
            destination.mkdir(parents=True, exist_ok=True)
        raise


def _rollback_media_destination(destination: Path, existed_empty: bool) -> None:
    destination = destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    if existed_empty:
        destination.mkdir(parents=True, exist_ok=True)


def restore_backup_bundle(
    *,
    archive_path: Path,
    database_path: Path,
    env_path: Path,
    backup_dir: Path,
    restore_env: bool = False,
    media_destination: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, object]:
    manifest = verify_backup_bundle(archive_path)
    if restore_env and not manifest.get("includes_env"):
        raise BackupRestoreError("该备份包不包含 .env，无法恢复配置")
    if media_destination is not None and not manifest.get("includes_media"):
        raise BackupRestoreError("该备份包不包含媒体文件")

    database_path = database_path.resolve()
    env_path = env_path.resolve()
    backup_dir = backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)

    if media_destination is not None:
        _preflight_media_destination(media_destination)

    rollback_path: Path | None = None
    if database_path.exists():
        rollback_path = create_backup_bundle(
            database_path=database_path,
            env_path=env_path,
            backup_dir=backup_dir,
            include_env=env_path.is_file(),
            include_media=False,
            label="pre-restore",
            project_root=project_root,
        )

    restored_media_files = 0
    media_destination_existed_empty = False
    media_committed = False

    with tempfile.TemporaryDirectory(
        prefix="niuma-restore-",
        dir=backup_dir,
    ) as temp_dir:
        stage_root = Path(temp_dir)
        new_database = stage_root / "workflow.sqlite3"
        new_env = stage_root / ".env" if restore_env else None

        with zipfile.ZipFile(archive_path, mode="r") as archive:
            _extract_archive_entry(archive, DATABASE_ENTRY, new_database)
            if new_env is not None:
                _extract_archive_entry(archive, ENV_ENTRY, new_env)

            integrity = sqlite_quick_check(new_database)
            if integrity != "ok":
                raise BackupRestoreError(
                    f"待恢复数据库完整性检查失败：{integrity}"
                )
            expected_counts = manifest.get("table_counts")
            actual_counts = _table_counts(new_database)
            if actual_counts != expected_counts:
                raise BackupRestoreError(
                    "待恢复数据库数量与清单不一致："
                    f"actual={actual_counts}, expected={expected_counts}"
                )

            if media_destination is not None:
                (
                    restored_media_files,
                    media_destination_existed_empty,
                ) = _restore_media_before_database(archive, media_destination)
                media_committed = True

            try:
                _atomic_restore_files(
                    new_database=new_database,
                    database_path=database_path,
                    new_env=new_env,
                    env_path=env_path,
                )
            except Exception:
                if media_committed and media_destination is not None:
                    _rollback_media_destination(
                        media_destination,
                        media_destination_existed_empty,
                    )
                raise

    restored_counts = _table_counts(database_path)
    if restored_counts != manifest.get("table_counts"):
        raise BackupRestoreError(
            "恢复完成后数量复核失败："
            f"actual={restored_counts}, expected={manifest.get('table_counts')}"
        )

    return {
        "archive": str(archive_path.resolve()),
        "database": str(database_path),
        "environment_restored": restore_env,
        "media_destination": (
            str(media_destination.resolve()) if media_destination else ""
        ),
        "media_files_restored": restored_media_files,
        "table_counts": restored_counts,
        "rollback_archive": str(rollback_path) if rollback_path else "",
    }


def _default_backup_dir() -> Path:
    return settings.project_root / "backups"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="牛马片场数据库、配置与媒体备份恢复工具"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="创建经过校验的备份包")
    backup_parser.add_argument("--database", type=Path, default=settings.database_path)
    backup_parser.add_argument(
        "--env-file",
        type=Path,
        default=settings.project_root / ".env",
    )
    backup_parser.add_argument(
        "--output-dir",
        type=Path,
        default=_default_backup_dir(),
    )
    backup_parser.add_argument("--label", default="manual")
    backup_parser.add_argument("--exclude-env", action="store_true")
    backup_parser.add_argument("--include-media", action="store_true")
    backup_parser.add_argument("--media-root", type=Path, default=settings.tasks_dir)

    verify_parser = subparsers.add_parser("verify", help="验证备份包完整性")
    verify_parser.add_argument("archive", type=Path)

    restore_parser = subparsers.add_parser("restore", help="安全恢复备份包")
    restore_parser.add_argument("archive", type=Path)
    restore_parser.add_argument("--database", type=Path, default=settings.database_path)
    restore_parser.add_argument(
        "--env-file",
        type=Path,
        default=settings.project_root / ".env",
    )
    restore_parser.add_argument(
        "--backup-dir",
        type=Path,
        default=_default_backup_dir(),
    )
    restore_parser.add_argument("--restore-env", action="store_true")
    restore_parser.add_argument("--media-destination", type=Path)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        if args.command == "backup":
            archive = create_backup_bundle(
                database_path=args.database,
                env_path=args.env_file,
                backup_dir=args.output_dir,
                include_env=not args.exclude_env,
                include_media=args.include_media,
                media_root=args.media_root,
                label=args.label,
                project_root=settings.project_root,
            )
            manifest = verify_backup_bundle(archive)
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "archive": str(archive),
                        "contains_secrets": manifest.get("contains_secrets"),
                        "includes_media": manifest.get("includes_media"),
                        "table_counts": manifest.get("table_counts"),
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        if args.command == "verify":
            manifest = verify_backup_bundle(args.archive)
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "archive": str(args.archive.resolve()),
                        "app_version": manifest.get("app_version"),
                        "created_at": manifest.get("created_at"),
                        "contains_secrets": manifest.get("contains_secrets"),
                        "includes_media": manifest.get("includes_media"),
                        "table_counts": manifest.get("table_counts"),
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        if args.command == "restore":
            result = restore_backup_bundle(
                archive_path=args.archive,
                database_path=args.database,
                env_path=args.env_file,
                backup_dir=args.backup_dir,
                restore_env=args.restore_env,
                media_destination=args.media_destination,
                project_root=settings.project_root,
            )
            print(json.dumps({"status": "ok", **result}, ensure_ascii=False))
            return 0
    except BackupRestoreError as exc:
        parser.exit(1, f"错误：{exc}\n")

    parser.error("未知命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
