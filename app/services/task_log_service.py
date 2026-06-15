"""任务日志服务

从 task_service 中拆分出来的日志写入和读取函数。
"""

from datetime import datetime

from app.services.storage_service import get_artifact_paths


def append_task_log(task_id: str, message: str) -> None:
    """向任务日志文件追加一条带时间戳的记录"""
    paths = get_artifact_paths(task_id)
    paths["log_path"].parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with paths["log_path"].open("a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")


def read_task_log_tail(task_id: str, limit: int = 80) -> list[str]:
    """读取任务日志文件的最后 N 行"""
    paths = get_artifact_paths(task_id)
    log_path = paths["log_path"]
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-limit:]
