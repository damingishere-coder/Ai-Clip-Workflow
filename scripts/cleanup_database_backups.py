from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.database_backup_service import (  # noqa: E402
    BackupSafetyError,
    apply_cleanup_plan,
    build_cleanup_plan,
)


def _format_size(byte_count: int) -> str:
    return f"{byte_count / 1024**3:.3f} GiB"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="安全清理牛马片场重复或损坏的 SQLite 迁移备份。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际执行删除；不提供此参数时只做预演。",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=14,
        help="保留最近多少个备份日，每天只保留最后一份有效备份（默认 14）。",
    )
    args = parser.parse_args()

    database_path = settings.database_path.resolve()
    backup_dir = (settings.data_dir / "backups").resolve()
    try:
        plan = build_cleanup_plan(
            database_path,
            backup_dir,
            keep_days=args.keep_days,
        )
    except (BackupSafetyError, ValueError) as exc:
        print(f"安全检查未通过：{exc}", file=sys.stderr)
        return 2

    print(f"主数据库：{database_path}")
    print(f"备份目录：{backup_dir}")
    print(f"保留有效备份：{len(plan.keep_files):,} 份")
    print(f"待删除 SQLite 备份：{len(plan.delete_files):,} 份")
    print(f"其中损坏备份：{len(plan.invalid_files):,} 份")
    print(f"待删除 journal：{len(plan.journal_files):,} 份")
    print(f"预计释放：{_format_size(plan.release_bytes)}")

    if not args.apply:
        print("当前是预演，没有删除任何文件。确认后添加 --apply 执行。")
        return 0

    try:
        result = apply_cleanup_plan(plan)
    except BackupSafetyError as exc:
        print(f"删除前安全检查未通过：{exc}", file=sys.stderr)
        return 2

    print(f"清理完成：共删除 {result.deleted_files:,} 个文件。")
    print(f"实际释放：{_format_size(result.released_bytes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
