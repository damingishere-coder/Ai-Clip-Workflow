"""预览或永久清理已隐藏任务的系统托管媒体文件。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.db.database import get_connection  # noqa: E402
from app.services.database_backup_service import create_media_cleanup_backup  # noqa: E402
from app.services.storage_service import (  # noqa: E402
    StorageSafetyError,
    build_task_media_cleanup_plan,
    task_media_cleanup_plan_size,
)
from app.services.task_lifecycle_service import delete_task_permanently  # noqa: E402


def _deleted_tasks() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, task_name, task_dir_name, source_type,
                   original_video_path, nas_file_path, status,
                   COALESCE(is_deleted, 0) AS is_deleted
            FROM tasks
            WHERE COALESCE(is_deleted, 0) = 1
            ORDER BY deleted_at, created_at, id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _active_task_directories() -> dict[str, bool]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, task_dir_name
            FROM tasks
            WHERE COALESCE(is_deleted, 0) = 0
            ORDER BY id
            """
        ).fetchall()
    return {
        str(settings.tasks_dir / str(row["task_dir_name"] or row["id"])): (
            settings.tasks_dir / str(row["task_dir_name"] or row["id"])
        ).exists()
        for row in rows
    }


def build_report() -> dict:
    items = []
    total_bytes = 0
    existing_directories = 0
    for task in _deleted_tasks():
        plan = build_task_media_cleanup_plan(task)
        size_bytes = task_media_cleanup_plan_size(plan)
        targets = [str(target.path) for target in plan.existing_targets]
        total_bytes += size_bytes
        existing_directories += len(targets)
        items.append(
            {
                "task_id": task["id"],
                "task_name": task["task_name"],
                "size_bytes": size_bytes,
                "existing_targets": targets,
                "external_source_preserved": str(plan.external_source_path or ""),
            }
        )
    return {
        "mode": "dry-run",
        "database_path": str(settings.database_path),
        "tasks_dir": str(settings.tasks_dir),
        "deleted_task_count": len(items),
        "existing_directory_count": existing_directories,
        "total_bytes": total_bytes,
        "items": items,
    }


def apply_report(report: dict) -> dict:
    active_before = _active_task_directories()
    missing_active_before = [path for path, existed in active_before.items() if not existed]
    if missing_active_before:
        raise RuntimeError(
            "发现有效任务目录在清理前已经缺失，已中止：" + "；".join(missing_active_before)
        )

    active_resolved = {Path(path).resolve(strict=False) for path in active_before}
    cleanup_resolved = {
        Path(path).resolve(strict=False)
        for item in report["items"]
        for path in item["existing_targets"]
    }
    overlaps = []
    for cleanup_path in cleanup_resolved:
        for active_path in active_resolved:
            if (
                cleanup_path == active_path
                or cleanup_path in active_path.parents
                or active_path in cleanup_path.parents
            ):
                overlaps.append(f"清理目标 {cleanup_path} 与有效任务 {active_path} 重叠")
    if overlaps:
        raise RuntimeError("发现清理目标与有效任务目录重叠，已中止：" + "；".join(overlaps))

    backup_path = create_media_cleanup_backup(
        settings.database_path,
        settings.data_dir / "backups",
    )
    results = []
    for item in report["items"]:
        results.append(delete_task_permanently(str(item["task_id"])))

    missing_active_after = [path for path in active_before if not Path(path).exists()]
    if missing_active_after:
        raise RuntimeError(
            "清理后发现有效任务目录缺失，请立即检查数据库备份：" + "；".join(missing_active_after)
        )

    released_bytes = sum(int(result["freed_bytes"]) for result in results)
    return {
        **report,
        "mode": "apply",
        "backup_path": str(backup_path),
        "released_bytes": released_bytes,
        "results": results,
        "active_task_count_verified": len(active_before),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="永久清理已隐藏任务的系统托管媒体")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际永久删除；不带此参数时只生成预览清单",
    )
    args = parser.parse_args()

    try:
        report = build_report()
        if args.apply:
            report = apply_report(report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (StorageSafetyError, RuntimeError) as exc:
        print(f"清理已中止：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
