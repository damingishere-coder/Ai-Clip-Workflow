"""pytest 测试环境必须与活动数据库和媒体目录隔离。"""

import os
from pathlib import Path


def test_pytest_paths_are_under_process_sandbox():
    sandbox = Path(os.environ["NIUMA_PYTEST_SANDBOX_ROOT"]).resolve()
    assert sandbox.exists()
    assert Path(os.environ["DATABASE_PATH"]).resolve().is_relative_to(sandbox)
    assert Path(os.environ["DATABASE_PATH"]).name == "test_workflow.sqlite3"
    for name in (
        "STORAGE_ROOT",
        "TASKS_DIR",
        "UPLOAD_TEMP_DIR",
        "DATA_DIR",
        "PUBLISH_SCHEDULER_EXPORT_DIR",
    ):
        assert Path(os.environ[name]).resolve().is_relative_to(sandbox)
