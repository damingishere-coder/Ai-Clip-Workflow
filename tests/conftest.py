"""Pytest 共享配置与 fixtures"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# 让测试代码可以直接导入 app 模块
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# 测试进程必须拥有自己的数据库和文件根目录。这里刻意使用无条件赋值，
# 防止调用 pytest 时继承 DATABASE_PATH、STORAGE_ROOT 等活动环境变量。
PYTEST_SANDBOX_ROOT = Path(tempfile.mkdtemp(prefix="niuma-pytest-")).resolve()
PYTEST_STORAGE_ROOT = PYTEST_SANDBOX_ROOT / "storage"
PYTEST_TASKS_DIR = PYTEST_STORAGE_ROOT / "tasks"
PYTEST_UPLOAD_TEMP_DIR = PYTEST_STORAGE_ROOT / "_临时上传"
PYTEST_DATA_DIR = PYTEST_SANDBOX_ROOT / "data"
PYTEST_DATABASE_PATH = PYTEST_DATA_DIR / "test_workflow.sqlite3"
PYTEST_PUBLISH_EXPORT_DIR = PYTEST_STORAGE_ROOT / "_发布包"

os.environ.update(
    {
        "STORAGE_ROOT": str(PYTEST_STORAGE_ROOT),
        "TASKS_DIR": str(PYTEST_TASKS_DIR),
        "UPLOAD_TEMP_DIR": str(PYTEST_UPLOAD_TEMP_DIR),
        "DATA_DIR": str(PYTEST_DATA_DIR),
        "DATABASE_PATH": str(PYTEST_DATABASE_PATH),
        "PUBLISH_SCHEDULER_EXPORT_DIR": str(PYTEST_PUBLISH_EXPORT_DIR),
        "NIUMA_PYTEST_SANDBOX_ROOT": str(PYTEST_SANDBOX_ROOT),
    }
)


@pytest.fixture(scope="session", autouse=True)
def initialize_isolated_test_database():
    """在任何测试访问服务层之前初始化本进程专属临时数据库。"""

    from app.db.database import init_db

    init_db()
