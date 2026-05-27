from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path, PureWindowsPath
import re
import shutil
import sqlite3


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "workflow.sqlite3"
STORAGE_ROOT = Path(r"E:\直播间切片工作流存储")
TRASH_DIR_NAME = "_回收站"
WINDOWS_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
PATH_COLUMNS = {
    "tasks": ("original_video_path", "nas_file_path", "source_path"),
    "output_clip": ("output_file_path",),
    "subtitle_jobs": ("subtitle_file_path", "output_file_path"),
    "publish_jobs": ("video_file_path", "cover_file_path"),
}


def sanitize_task_dir_name(task_name: str | None, fallback: str) -> str:
    raw_name = (task_name or "").strip() or fallback
    sanitized = WINDOWS_FORBIDDEN_CHARS.sub("_", raw_name)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" .")
    if not sanitized:
        sanitized = fallback
    if sanitized.upper() in WINDOWS_RESERVED_NAMES:
        sanitized = f"{sanitized}_"
    return sanitized[:120].strip(" .") or fallback


def relative_parts(value: str) -> tuple[str, ...]:
    return tuple(part for part in PureWindowsPath(value).parts if part not in {"", "."})


def relative_path(*parts: str) -> str:
    return str(PureWindowsPath(*parts))


def storage_path(relative_name: str) -> Path:
    return STORAGE_ROOT.joinpath(*relative_parts(relative_name))


def allocate_relative_name(
    base_name: str,
    parent: str | None,
    used_relative_names: set[str],
) -> str:
    parent_parts = relative_parts(parent or "")
    for index in range(1, 1000):
        candidate_name = base_name if index == 1 else f"{base_name} ({index})"
        candidate = relative_path(*parent_parts, candidate_name)
        if candidate.lower() not in used_relative_names and not storage_path(candidate).exists():
            used_relative_names.add(candidate.lower())
            return candidate
    fallback = relative_path(*parent_parts, f"{base_name}-{datetime.now().strftime('%H%M%S')}")
    used_relative_names.add(fallback.lower())
    return fallback


def ensure_task_dir_name_column(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
    if "task_dir_name" not in columns:
        connection.execute("ALTER TABLE tasks ADD COLUMN task_dir_name TEXT")
        connection.execute("UPDATE tasks SET task_dir_name = id WHERE task_dir_name IS NULL OR task_dir_name = ''")


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}


def path_replacements(old_relative: str, new_relative: str) -> list[tuple[str, str]]:
    old_dir = storage_path(old_relative)
    new_dir = storage_path(new_relative)
    old_posix = old_relative.replace("\\", "/")
    new_posix = new_relative.replace("\\", "/")
    replacements = [
        (str(old_dir), str(new_dir)),
        (str(old_dir).replace("\\", "/"), str(new_dir).replace("\\", "/")),
        (f"/workspace/tasks/{old_posix}", f"/workspace/tasks/{new_posix}"),
    ]
    return sorted({item for item in replacements if item[0] != item[1]}, key=lambda item: len(item[0]), reverse=True)


def replace_path_prefix(value: str | None, replacements: list[tuple[str, str]]) -> str | None:
    if not value:
        return value
    for old_prefix, new_prefix in replacements:
        if value.lower() == old_prefix.lower():
            return new_prefix
        for separator in ("\\", "/"):
            full_prefix = f"{old_prefix}{separator}"
            if value.lower().startswith(full_prefix.lower()):
                return f"{new_prefix}{separator}{value[len(full_prefix):]}"
    return value


def update_path_columns(
    connection: sqlite3.Connection,
    task_id: str,
    old_relative: str,
    new_relative: str,
) -> None:
    replacements = path_replacements(old_relative, new_relative)
    for table_name, candidate_columns in PATH_COLUMNS.items():
        existing = table_columns(connection, table_name)
        columns = [column for column in candidate_columns if column in existing]
        if not columns:
            continue
        where_clause = "id = ?" if table_name == "tasks" else "task_id = ?"
        rows = connection.execute(
            f"SELECT rowid, {', '.join(columns)} FROM {table_name} WHERE {where_clause}",
            (task_id,),
        ).fetchall()
        for row in rows:
            changed = {}
            for column in columns:
                updated = replace_path_prefix(row[column], replacements)
                if updated != row[column]:
                    changed[column] = updated
            if not changed:
                continue
            set_clause = ", ".join(f"{column} = ?" for column in changed)
            connection.execute(
                f"UPDATE {table_name} SET {set_clause} WHERE rowid = ?",
                (*changed.values(), row["rowid"]),
            )


def build_migration_plan(connection: sqlite3.Connection) -> list[dict]:
    ensure_task_dir_name_column(connection)
    rows = connection.execute(
        """
        SELECT id, task_name, task_dir_name, is_deleted
        FROM tasks
        ORDER BY created_at ASC, id ASC
        """
    ).fetchall()
    used_relative_names = {
        str(row["task_dir_name"]).lower()
        for row in rows
        if row["task_dir_name"]
    }
    plan = []
    for row in rows:
        task_id = row["id"]
        current_relative = row["task_dir_name"] or task_id
        source_relative = current_relative if storage_path(current_relative).exists() else task_id
        source_dir = storage_path(source_relative)
        if not source_dir.exists():
            plan.append(
                {
                    "task_id": task_id,
                    "task_name": row["task_name"] or task_id,
                    "skip": True,
                    "reason": "E盘任务目录不存在，本次不处理",
                    "old_relative": source_relative,
                }
            )
            continue

        base_name = sanitize_task_dir_name(row["task_name"], fallback=task_id)
        parent = TRASH_DIR_NAME if row["is_deleted"] else None
        desired_relative = relative_path(*relative_parts(parent or ""), base_name)
        used_relative_names.discard(str(current_relative).lower())
        if source_relative.lower() == desired_relative.lower():
            new_relative = source_relative
            action = "keep"
        else:
            new_relative = allocate_relative_name(base_name, parent, used_relative_names)
            action = "move"
        plan.append(
            {
                "task_id": task_id,
                "task_name": row["task_name"] or task_id,
                "skip": False,
                "action": action,
                "old_relative": source_relative,
                "new_relative": new_relative,
                "old_path": str(source_dir),
                "new_path": str(storage_path(new_relative)),
            }
        )
    return plan


def print_plan(plan: list[dict]) -> None:
    for item in plan:
        if item.get("skip"):
            print(f"[跳过] {item['task_id']} / {item['task_name']}：{item['reason']}")
            continue
        if item["action"] == "keep":
            print(f"[保持] {item['task_id']} / {item['task_name']}：{item['old_relative']}")
            continue
        print(f"[移动] {item['task_id']} / {item['task_name']}")
        print(f"  {item['old_path']}")
        print(f"  -> {item['new_path']}")


def apply_plan(connection: sqlite3.Connection, plan: list[dict]) -> Path:
    backup_path = DATABASE_PATH.with_name(
        f"{DATABASE_PATH.name}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    shutil.copy2(DATABASE_PATH, backup_path)
    (STORAGE_ROOT / TRASH_DIR_NAME).mkdir(parents=True, exist_ok=True)

    for item in plan:
        if item.get("skip"):
            continue
        old_relative = item["old_relative"]
        new_relative = item["new_relative"]
        old_path = storage_path(old_relative)
        new_path = storage_path(new_relative)
        if item["action"] == "move":
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_path), str(new_path))
        connection.execute(
            "UPDATE tasks SET task_dir_name = ? WHERE id = ?",
            (new_relative, item["task_id"]),
        )
        update_path_columns(connection, item["task_id"], old_relative, new_relative)
    connection.commit()
    return backup_path


def main() -> None:
    parser = argparse.ArgumentParser(description="把任务文件夹从短 ID 迁移为项目名。")
    parser.add_argument("--apply", action="store_true", help="真正执行迁移；不加时只预览。")
    args = parser.parse_args()

    if not DATABASE_PATH.exists():
        raise SystemExit(f"数据库不存在：{DATABASE_PATH}")
    if not STORAGE_ROOT.exists():
        raise SystemExit(f"E盘存储目录不存在：{STORAGE_ROOT}")

    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        plan = build_migration_plan(connection)
        print_plan(plan)
        if not args.apply:
            print("\n当前是 dry-run 预览，没有移动文件夹，也没有修改数据库。")
            return
        backup_path = apply_plan(connection, plan)
        print(f"\n迁移完成。数据库备份：{backup_path}")


if __name__ == "__main__":
    main()
