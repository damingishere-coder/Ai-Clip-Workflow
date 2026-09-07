"""只读复制生产 SQLite，在独立副本中回放动态排期；不启动 Worker。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--as-of", help="可选带时区的回放时间")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output_dir.resolve()
    if output == source.parent or output == source or output in source.parents:
        parser.error("输出必须是独立的新目录")
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "replay.sqlite3"
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(snapshot) as dst:
            src.backup(dst)
    os.environ.update(
        DATABASE_PATH=str(snapshot),
        DATA_DIR=str(output),
        STORAGE_ROOT=str(output / "storage"),
        TASKS_DIR=str(output / "storage"),
        UPLOAD_TEMP_DIR=str(output / "uploads"),
        PUBLISH_SCHEDULER_ENABLED="false",
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.db.database import get_connection, init_db
    from app.services import adaptive_schedule as service

    init_db()
    if args.as_of:
        at = datetime.fromisoformat(args.as_of)
        if at.tzinfo is None:
            parser.error("回放时间必须携带时区")
        service._now = lambda: at
    now = service._now()
    info = service.context()
    if not info["available"]:
        parser.error("没有唯一的目标账号")
    account_id = info["policy"]["account_id"]
    with get_connection() as c:
        before = {r["id"]: dict(r) for r in c.execute("SELECT * FROM publish_jobs")}
    service.save_policy(
        account_id, {**service.DEFAULTS, "enabled": True}, include_existing=True
    )
    requests = service.process_pending()
    with get_connection() as c:
        after = {r["id"]: dict(r) for r in c.execute("SELECT * FROM publish_jobs")}
        changes = [
            dict(r)
            for r in c.execute("SELECT * FROM adaptive_schedule_changes ORDER BY id")
        ]
        integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = list(c.execute("PRAGMA foreign_key_check"))
    assert before.keys() == after.keys(), "任务集合发生变化"
    for key, row in before.items():
        time = service._time(row)
        if (
            row["account_id"] != account_id
            or row["platform"] != "douyin"
            or (time and time.date() <= now.date())
        ):
            assert row["scheduled_at"] == after[key]["scheduled_at"], "受保护任务被改期"
        for field in (
            "task_id",
            "output_clip_id",
            "title",
            "caption",
            "video_file_path",
            "platform",
            "account_id",
        ):
            assert row[field] == after[key][field], f"任务内容被修改：{field}"
    assert integrity == "ok" and not foreign_keys, "副本数据库完整性失败"
    report = {
        "source_open_mode": "ro",
        "snapshot": str(snapshot),
        "as_of": now.isoformat(),
        "total_jobs": len(before),
        "strategy": info["strategy"],
        "requests": requests,
        "changes": changes,
        "changed_jobs": len(changes),
        "protected_dates_and_accounts_unchanged": True,
        "content_unchanged": True,
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"changes", "strategy"}},
            ensure_ascii=False,
        )
    )
    print(f"Report: {output / 'report.json'}")


if __name__ == "__main__":
    main()
