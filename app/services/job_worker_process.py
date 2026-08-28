"""持久化 Job 的独立子进程入口。"""

from __future__ import annotations

import sys

from app.services.job_worker import execute_job


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "用法：python -m app.services.job_worker_process <job_id> <lease_owner> <lease_token>",
            file=sys.stderr,
        )
        return 2
    job_id, lease_owner, lease_token = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        execute_job(
            job_id,
            lease_owner=lease_owner,
            lease_token=lease_token,
            already_claimed=True,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
