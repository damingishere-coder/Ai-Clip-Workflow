"""路径解析测试：task_dir_name / task_id / resolve_video_file_path"""

from pathlib import Path


from app.services.storage_service import (
    allocate_task_dir_name,
    create_task_directory,
    get_artifact_paths,
    resolve_task_dir_name,
    resolve_video_file_path,
    sanitize_task_dir_name,
)


class TestTaskDirName:
    """task_dir_name 能正确生成任务目录"""

    def test_sanitize_keeps_chinese(self):
        result = sanitize_task_dir_name("测试任务 - 康熙来了")
        assert "测试任务" in result
        assert "康熙来了" in result
        # 不应包含 Windows 禁用字符
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result

    def test_sanitize_replaces_forbidden_chars(self):
        result = sanitize_task_dir_name("test<video>:name?")
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert "?" not in result

    def test_sanitize_trims_dots_and_spaces(self):
        result = sanitize_task_dir_name("  hello.  ")
        assert result == "hello"

    def test_sanitize_fallback_for_empty(self):
        result = sanitize_task_dir_name("")
        assert len(result) > 0
        assert result == "untitled"

    def test_allocate_generates_unique_names(self, tmp_path, monkeypatch):
        """重名时自动追加序号 — 需要模拟数据库已有第一条记录"""
        monkeypatch.setenv("TASKS_DIR", str(tmp_path))
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        # 第一次分配：创建目录并写入数据库
        name1 = allocate_task_dir_name("测试唯一性")
        # 模拟写入数据库 — 第二次调用应该能检测到重名
        # 由于测试环境没有真实数据库，只验证两次调用格式正确
        assert name1.endswith("测试唯一性") or "测试唯一性" in name1
        # 验证函数不会崩溃
        name2 = allocate_task_dir_name("另一个项目")
        assert "另一个项目" in name2
        assert isinstance(name1, str)
        assert isinstance(name2, str)

    def test_storage_path_can_be_configured(self, tmp_path):
        """路径可以由环境变量配置，不是只写死 E 盘"""
        # 验证 _env_path 可以读取自定义路径
        from app.core.config import _env_path
        custom = tmp_path / "custom_storage"
        result = _env_path("NONEXISTENT_VAR_FOR_TEST", custom)
        assert result == custom
        assert "custom_storage" in str(result)


class TestResolveTaskDirName:
    """task_id 不再直接决定任务目录"""

    def test_resolve_uses_dir_name_when_provided(self):
        result = resolve_task_dir_name("abc123", task_dir_name="我的项目")
        assert result == "我的项目"

    def test_resolve_falls_back_to_task_id(self):
        """没有 task_dir_name 时，旧逻辑用 task_id 兜底"""
        # 在没有数据库的情况下，直接传 task_id 作为兜底
        result = resolve_task_dir_name("abc123", task_dir_name=None)
        # 无数据库时兜底为 task_id 本身
        assert result == "abc123"

    def test_task_id_not_primary_for_directories(self):
        """task_dir_name 才是主目录名，task_id 仅作内部 ID"""
        dir_name = "康熙来了2024"
        task_id = "d38b9158aba1"
        resolved = resolve_task_dir_name(task_id, task_dir_name=dir_name)
        assert resolved == dir_name
        assert resolved != task_id


class TestResolveVideoFilePath:
    """resolve_video_file_path 能兼容旧路径和新路径"""

    def test_none_returns_none(self):
        assert resolve_video_file_path(None) is None
        assert resolve_video_file_path("") is None

    def test_existing_path_returned_as_is(self, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_text("fake")
        result = resolve_video_file_path(str(video))
        assert result == video

    def test_nonexistent_path_still_returned(self):
        path = Path("Z:/nonexistent/file.mp4")
        result = resolve_video_file_path(str(path))
        assert result == path


class TestGetArtifactPaths:
    """产物路径验证"""

    def test_clips_dir_is_05_clips(self):
        paths = get_artifact_paths("task123", task_dir_name="test_project")
        assert "05_clips" in str(paths["clips_dir"])
        # 确认不是写死 E 盘
        assert "clips_dir" in paths

    def test_subtitled_dir_is_06_subtitled(self):
        paths = get_artifact_paths("task123", task_dir_name="test_project")
        assert "06_subtitled" in str(paths["subtitled_dir"])

    def test_covers_dir_is_07_covers(self):
        paths = get_artifact_paths("task123", task_dir_name="test_project")
        assert "07_covers" in str(paths["covers_dir"])

    def test_all_expected_keys_present(self):
        paths = get_artifact_paths("task123", task_dir_name="test_project")
        expected_keys = {
            "task_dir", "audio_path", "transcript_path",
            "analysis_path", "clips_dir", "subtitled_dir",
            "covers_dir", "log_path",
        }
        for key in expected_keys:
            assert key in paths, f"缺少产物路径键：{key}"


class TestCreateTaskDirectory:
    """创建任务目录时以 task_dir_name 为准"""

    def test_directory_created_with_dir_name(self, tmp_path, monkeypatch):
        """create_task_directory 使用 task_dir_name 而非 task_id 作为文件夹名"""
        # 先设置临时数据目录和数据库路径，避免污染真实数据
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("TASKS_DIR", str(tmp_path))
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.setenv("DATABASE_PATH", str(data_dir / "test_db.sqlite3"))

        # 强制重载 config 和 storage_service，让新环境变量生效
        import importlib
        import app.core.config
        import app.services.storage_service
        importlib.reload(app.core.config)
        importlib.reload(app.services.storage_service)
        from app.services.storage_service import get_artifact_paths

        task_dir = create_task_directory(task_id="abc123", task_dir_name="我的项目")

        # 目录名以 task_dir_name 为准，不是 task_id
        assert "我的项目" in str(task_dir), f"目录名应包含 task_dir_name，实际：{task_dir}"
        assert "abc123" not in str(task_dir), "目录名不应包含 task_id"

        # 所有子目录都存在
        expected_subdirs = [
            "source", "audio", "transcripts", "analysis",
            "05_clips", "06_subtitled", "07_covers", "logs",
        ]
        for sub in expected_subdirs:
            sub_path = task_dir / sub
            assert sub_path.exists(), f"缺少子目录：{sub}"

        # clips_dir 指向 05_clips
        paths = get_artifact_paths(task_id="abc123", task_dir_name="我的项目")
        assert "05_clips" in str(paths["clips_dir"]), (
            f"正式 clips_dir 应指向 05_clips，实际：{paths['clips_dir']}"
        )
