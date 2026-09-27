"""Preview/apply an authorized scoring revision against an explicit existing DB."""

import argparse
import json
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--target", choices=("playback", "legacy"), default="playback")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    if args.apply and not args.expected_sha256:
        parser.error("apply requires --expected-sha256 from preview")
    if not args.database.is_file():
        parser.error("database must already exist")
    from app.services.playback_activation_service import apply, preview
    mode = "rw" if args.apply else "ro"
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=" + mode, uri=True, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if args.apply:
            connection.execute("BEGIN IMMEDIATE")
            result = apply(connection, expected_sha256=args.expected_sha256, target=args.target)
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("外键检查失败，启用事务已回滚")
        else:
            result = preview(connection, target=args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
