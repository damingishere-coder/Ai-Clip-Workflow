"""Pytest 共享配置与 fixtures"""

import os
import sys
from pathlib import Path

# 让测试代码可以直接导入 app 模块
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("STORAGE_ROOT", str(PROJECT_ROOT / "data" / "test_storage"))
os.environ.setdefault("TASKS_DIR", str(PROJECT_ROOT / "data" / "test_storage"))
os.environ.setdefault("DATA_DIR", str(PROJECT_ROOT / "data"))
os.environ.setdefault("DATABASE_PATH", str(PROJECT_ROOT / "data" / "test_workflow.sqlite3"))
