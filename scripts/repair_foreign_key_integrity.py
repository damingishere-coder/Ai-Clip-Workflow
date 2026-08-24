"""预演并修复 output_clip 缺失造成的 SQLite 外键异常。

默认只读。只有显式传入 ``--apply`` 和预期异常数量时才会写入数据库。
修复策略是不删除发布/字幕历史，而是补充不可见、无媒体路径的占位 output_clip。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.database_backup_service import (  # noqa: E402
    create_schema_migration_backup,
    sqlite_quick_check,
)


SUPPORTED_CHILD_TABLES = ("publish_jobs", "subtitle_jobs")
TOMBSTONE_STATUS = "integrity_repair_tombstone"
TOMBSTONE_SOURCE = "integrity_repair_tombstone"


class ForeignKeyRepairSafetyError(RuntimeError):
    """当前异常不满足自动修复条件。"""


@dataclass(frozen=True)
class RepairTombstone:
    output_clip_id: str
    task_id: str
    source_tables: tuple[str, ...]


@dataclass(frozen=True)
class ForeignKeyRepairPlan:
    database_path: Path
    violation_count: int
    tombstones: tuple[RepairTombstone, ...]
    unsupported_violations: tuple[str, ...]

    @property
    def tombstone_count(self) -> int:
        return len(self.tombstones)

    @property
    def can_apply(self) -> bool:
        return not self.unsupported_violations


def _open_readonly(database_path: Path) -> sqlite3.Connection:
    resolved = database_path.resolve()
    if not resolved.is_file():
        raise ForeignKeyRepairSafetyError(f"数据库不存在：{resolved}")
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=ro",
        uri=True,
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _foreign_key_target(
    connection: sqlite3.Connection,
    table: str,
    foreign_key_id: int,
) -> tuple[str, str, str] | None:
    rows = connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    for row in rows:
        if int(row[0]) == foreign_key_id:
            return str(row[2]), str(row[3]), str(row[4])
    return None


def _build_plan_from_connection(
    connection: sqlite3.Connection,
    database_path: Path,
) -> ForeignKeyRepairPlan:
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    grouped: dict[str, dict[str, set[str]]] = {}
    unsupported: list[str] = []

    for violation in violations:
        table = str(violation[0])
        row_id = int(violation[1])
        parent = str(violation[2])
        foreign_key_id = int(violation[3])
        target = (
            _foreign_key_target(connection, table, foreign_key_id)
            if table in SUPPORTED_CHILD_TABLES
            else None
        )
        if (
            table not in SUPPORTED_CHILD_TABLES
            or parent != "output_clip"
            or target != ("output_clip", "output_clip_id", "id")
        ):
            unsupported.append(
                f"不支持的外键异常：table={table}, parent={parent}, fkid={foreign_key_id}"
            )
            continue

        row = connection.execute(
            f'SELECT output_clip_id, task_id FROM "{table}" WHERE rowid = ?',
            (row_id,),
        ).fetchone()
        if not row or not str(row["output_clip_id"] or "").strip():
            unsupported.append(f"{table} rowid={row_id} 缺少可修复的 output_clip_id")
            continue

        output_clip_id = str(row["output_clip_id"]).strip()
        task_id = str(row["task_id"] or "").strip()
        task_exists = connection.execute(
            "SELECT 1 FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        output_exists = connection.execute(
            "SELECT 1 FROM output_clip WHERE id = ?",
            (output_clip_id,),
        ).fetchone()
        if not task_id or not task_exists or output_exists:
            unsupported.append(
                f"{table} rowid={row_id} 的任务或 output_clip 状态不满足占位修复条件"
            )
            continue

        item = grouped.setdefault(output_clip_id, {"task_ids": set(), "tables": set()})
        item["task_ids"].add(task_id)
        item["tables"].add(table)

    tombstones: list[RepairTombstone] = []
    for output_clip_id, item in sorted(grouped.items()):
        task_ids = item["task_ids"]
        if len(task_ids) != 1:
            unsupported.append(
                f"同一缺失 output_clip 被多个任务引用，拒绝猜测归属：{output_clip_id}"
            )
            continue
        tombstones.append(
            RepairTombstone(
                output_clip_id=output_clip_id,
                task_id=next(iter(task_ids)),
                source_tables=tuple(sorted(item["tables"])),
            )
        )

    return ForeignKeyRepairPlan(
        database_path=database_path.resolve(),
        violation_count=len(violations),
        tombstones=tuple(tombstones),
        unsupported_violations=tuple(unsupported),
    )


def build_repair_plan(database_path: Path) -> ForeignKeyRepairPlan:
    connection = _open_readonly(database_path)
    try:
        return _build_plan_from_connection(connection, database_path)
    finally:
        connection.close()


def _plan_signature(plan: ForeignKeyRepairPlan) -> tuple:
    return (
        plan.violation_count,
        tuple(
            (item.output_clip_id, item.task_id, item.source_tables)
            for item in plan.tombstones
        ),
        plan.unsupported_violations,
    )


def _insert_tombstone(
    connection: sqlite3.Connection,
    tombstone: RepairTombstone,
    now: str,
) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(output_clip)").fetchall()
    }
    required = {"id", "task_id", "status", "created_at", "updated_at", "is_active"}
    missing = sorted(required - columns)
    if missing:
        raise ForeignKeyRepairSafetyError(
            "output_clip 缺少安全占位所需字段：" + ", ".join(missing)
        )

    values: dict[str, object] = {
        "id": tombstone.output_clip_id,
        "task_id": tombstone.task_id,
        "status": TOMBSTONE_STATUS,
        "created_at": now,
        "updated_at": now,
        "is_active": 0,
    }
    optional_values = {
        "clip_candidate_id": None,
        "output_file_path": "",
        "output_file_name": "",
        "error_message": "外键完整性修复生成的不可见占位记录；原 output_clip 已缺失",
        "cut_run_id": None,
        "snapshot_source": TOMBSTONE_SOURCE,
    }
    values.update({key: value for key, value in optional_values.items() if key in columns})

    names = tuple(values)
    placeholders = ", ".join("?" for _ in names)
    quoted_names = ", ".join(f'"{name}"' for name in names)
    connection.execute(
        f"INSERT INTO output_clip ({quoted_names}) VALUES ({placeholders})",
        tuple(values[name] for name in names),
    )


def apply_repair_plan(
    database_path: Path,
    backup_dir: Path,
    expected_violation_count: int,
) -> dict:
    database_path = database_path.resolve()
    backup_dir = backup_dir.resolve()
    initial_plan = build_repair_plan(database_path)
    if initial_plan.violation_count != expected_violation_count:
        raise ForeignKeyRepairSafetyError(
            "外键异常数量与人工确认值不一致："
            f"expected={expected_violation_count}, actual={initial_plan.violation_count}"
        )
    if initial_plan.unsupported_violations:
        raise ForeignKeyRepairSafetyError("；".join(initial_plan.unsupported_violations))

    connection = sqlite3.connect(str(database_path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        connection.execute("BEGIN IMMEDIATE")
        locked_plan = _build_plan_from_connection(connection, database_path)
        if _plan_signature(locked_plan) != _plan_signature(initial_plan):
            raise ForeignKeyRepairSafetyError("数据库在预演与写入之间发生变化，已中止")

        backup_path = create_schema_migration_backup(
            database_path,
            backup_dir,
            "foreign-key-repair",
        )
        backup_plan = build_repair_plan(backup_path)
        if _plan_signature(backup_plan) != _plan_signature(locked_plan):
            raise ForeignKeyRepairSafetyError("锁内备份与待修复状态不一致，已中止")

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for tombstone in locked_plan.tombstones:
            _insert_tombstone(connection, tombstone, now)

        remaining = connection.execute("PRAGMA foreign_key_check").fetchall()
        if remaining:
            raise ForeignKeyRepairSafetyError(
                f"事务内复查仍有 {len(remaining)} 条外键异常，已回滚"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    if sqlite_quick_check(database_path) != "ok":
        raise ForeignKeyRepairSafetyError("修复后数据库 quick_check 失败")
    final_plan = build_repair_plan(database_path)
    if final_plan.violation_count != 0:
        raise ForeignKeyRepairSafetyError(
            f"修复后仍有 {final_plan.violation_count} 条外键异常"
        )

    return {
        "mode": "apply",
        "database_path": str(database_path),
        "backup_path": str(backup_path),
        "before_violation_count": initial_plan.violation_count,
        "after_violation_count": final_plan.violation_count,
        "tombstone_count": initial_plan.tombstone_count,
        "quick_check": "ok",
    }


def _dry_run_report(plan: ForeignKeyRepairPlan) -> dict:
    table_counts = {table: 0 for table in SUPPORTED_CHILD_TABLES}
    for tombstone in plan.tombstones:
        for table in tombstone.source_tables:
            table_counts[table] += 1
    return {
        "mode": "dry-run",
        "database_path": str(plan.database_path),
        "violation_count": plan.violation_count,
        "tombstone_count": plan.tombstone_count,
        "tombstone_source_table_counts": table_counts,
        "can_apply": plan.can_apply,
        "unsupported_violations": list(plan.unsupported_violations),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="安全修复 output_clip 孤儿外键")
    parser.add_argument("--database", type=Path, default=settings.database_path)
    parser.add_argument("--backup-dir", type=Path, default=settings.data_dir / "backups")
    parser.add_argument("--apply", action="store_true", help="实际写入；默认只预演")
    parser.add_argument(
        "--expected-violation-count",
        type=int,
        help="应用时必须提供，且必须与预演数量完全一致",
    )
    args = parser.parse_args()

    try:
        if args.apply:
            if args.expected_violation_count is None:
                raise ForeignKeyRepairSafetyError(
                    "--apply 必须同时提供 --expected-violation-count"
                )
            report = apply_repair_plan(
                args.database,
                args.backup_dir,
                args.expected_violation_count,
            )
        else:
            report = _dry_run_report(build_repair_plan(args.database))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (sqlite3.Error, OSError, ForeignKeyRepairSafetyError) as exc:
        print(f"外键修复已中止：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
