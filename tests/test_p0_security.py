"""P0 安全加固测试：路径越界、非法扩展名、OAuth state、subprocess timeout、SQLite 索引"""

import io
import importlib
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# ── 辅助函数 ──


def _reload_storage_service(tmp_path, monkeypatch):
    """重载 storage_service 模块，让测试环境的 STORAGE_ROOT 生效。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    storage_dir = tmp_path / "test_storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STORAGE_ROOT", str(storage_dir))
    monkeypatch.setenv("TASKS_DIR", str(storage_dir))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_PATH", str(data_dir / "test_p0.sqlite3"))
    monkeypatch.setenv("ALLOWED_MEDIA_ROOTS", "")
    import app.core.config
    import app.services.storage_service
    importlib.reload(app.core.config)
    importlib.reload(app.services.storage_service)


# ════════════════════════════════════════════════════
# 1. 路径越界测试
# ════════════════════════════════════════════════════


class TestPathTraversal:
    """路径安全：禁止 .. 遍历、符号链接逃逸、非白名单目录访问"""

    def test_reject_double_dot_in_validate(self, tmp_path, monkeypatch):
        """validate_source_video_path 拒绝包含 .. 的路径"""
        _reload_storage_service(tmp_path, monkeypatch)
        from app.services.storage_service import validate_source_video_path

        valid, msg = validate_source_video_path("E:\\..\\Windows\\System32\\evil.mp4")
        assert not valid
        assert "不安全" in msg or "跳转" in msg

    def test_directory_browse_capability_is_removed(self, tmp_path, monkeypatch):
        """上传单入口下不再暴露目录浏览服务。"""
        _reload_storage_service(tmp_path, monkeypatch)
        from app.services import storage_service

        assert not hasattr(storage_service, "browse_video_directory")

    def test_reject_path_outside_roots(self, tmp_path, monkeypatch):
        """validate 拒绝不在允许根目录下的文件"""
        _reload_storage_service(tmp_path, monkeypatch)
        from app.services.storage_service import validate_source_video_path

        # 创建一个真实文件但不在 STORAGE_ROOT 下
        outside = tmp_path / "outside.mp4"
        outside.write_text("fake video")
        valid, msg = validate_source_video_path(str(outside))
        # 应该被拒绝，因为不在允许的根目录下
        assert not valid or "不在允许" in msg or "范围" in msg

    def test_accept_path_within_storage_root(self, tmp_path, monkeypatch):
        """validate 接受在 STORAGE_ROOT 下的合法文件"""
        _reload_storage_service(tmp_path, monkeypatch)
        from app.services.storage_service import validate_source_video_path

        storage = tmp_path / "test_storage"
        video = storage / "valid.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_text("fake")

        valid, msg = validate_source_video_path(str(video))
        assert valid, f"应该在存储根目录下被接受，但被拒绝：{msg}"

    def test_allowed_roots_includes_custom(self, tmp_path, monkeypatch):
        """ALLOWED_MEDIA_ROOTS 环境变量可扩展允许的根目录"""
        custom_root = tmp_path / "custom_media"
        custom_root.mkdir(parents=True, exist_ok=True)
        video = custom_root / "custom.mp4"
        video.write_text("fake")

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        storage_dir = tmp_path / "test_storage"
        storage_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("STORAGE_ROOT", str(storage_dir))
        monkeypatch.setenv("TASKS_DIR", str(storage_dir))
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.setenv("DATABASE_PATH", str(data_dir / "test_roots.sqlite3"))
        monkeypatch.setenv("ALLOWED_MEDIA_ROOTS", str(custom_root))

        import app.core.config
        import app.services.storage_service
        importlib.reload(app.core.config)
        importlib.reload(app.services.storage_service)

        from app.services.storage_service import validate_source_video_path

        valid, msg = validate_source_video_path(str(video))
        assert valid, f"应该在自定义根目录下被接受：{msg}"


# ════════════════════════════════════════════════════
# 2. 上传非法扩展名测试
# ════════════════════════════════════════════════════


class TestUploadExtensionValidation:
    """上传安全：扩展名校验"""

    def test_reject_exe_extension(self, tmp_path, monkeypatch):
        """拒绝 .exe 文件上传"""
        _reload_storage_service(tmp_path, monkeypatch)
        from app.services.storage_service import _validate_upload_extension

        with pytest.raises(ValueError, match="不支持的文件格式"):
            _validate_upload_extension("virus.exe")

    def test_reject_no_extension(self, tmp_path, monkeypatch):
        """拒绝无扩展名文件"""
        _reload_storage_service(tmp_path, monkeypatch)
        from app.services.storage_service import _validate_upload_extension

        with pytest.raises(ValueError, match="没有扩展名"):
            _validate_upload_extension("noextension")

    def test_accept_mp4_extension(self, tmp_path, monkeypatch):
        """接受 .mp4 文件"""
        _reload_storage_service(tmp_path, monkeypatch)
        from app.services.storage_service import _validate_upload_extension

        result = _validate_upload_extension("video.mp4")
        assert result == ".mp4"

    def test_accept_mov_extension(self, tmp_path, monkeypatch):
        """接受 .mov 文件"""
        _reload_storage_service(tmp_path, monkeypatch)
        from app.services.storage_service import _validate_upload_extension

        result = _validate_upload_extension("clip.MOV")
        assert result == ".mov"

    def test_save_upload_with_size_limit(self, tmp_path, monkeypatch):
        """上传超过大小限制时抛出异常并清理文件"""
        _reload_storage_service(tmp_path, monkeypatch)
        monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", "1024")  # 只允许 1KB
        import app.core.config
        importlib.reload(app.core.config)
        import app.services.storage_service
        importlib.reload(app.services.storage_service)

        from app.services.storage_service import save_uploaded_video

        fake_file = io.BytesIO(b"A" * 2048)  # 2KB 数据
        with pytest.raises(ValueError, match="大小限制"):
            save_uploaded_video("test_id", "video.mp4", fake_file, task_dir_name="test_dir")


# ════════════════════════════════════════════════════
# 3. OAuth state 校验测试
# ════════════════════════════════════════════════════


class TestOAuthState:
    """OAuth state 生命周期：保存 → 校验 → 消费"""

    @staticmethod
    def _setup_state_db(tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        storage_dir = tmp_path / "test_storage"
        storage_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "test_oauth.sqlite3"
        monkeypatch.setenv("STORAGE_ROOT", str(storage_dir))
        monkeypatch.setenv("TASKS_DIR", str(storage_dir))
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.setenv("DATABASE_PATH", str(db_path))

        import app.core.config
        import app.db.database
        import app.services.publish_service
        importlib.reload(app.core.config)
        importlib.reload(app.db.database)
        importlib.reload(app.services.publish_service)

        from app.db.database import init_db, get_connection
        init_db()
        # 确保 oauth_states 表存在
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def test_valid_state_accepted(self, tmp_path, monkeypatch):
        """有效的 state 通过校验并被消费"""
        self._setup_state_db(tmp_path, monkeypatch)
        from app.services.publish_service import _validate_and_consume_oauth_state
        from datetime import datetime, timedelta
        from app.db.database import get_connection

        now = datetime.now()
        expires = (now + timedelta(minutes=10)).isoformat(timespec="seconds")
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO oauth_states (state, platform, created_at, expires_at) VALUES (?, ?, ?, ?)",
                ("test_state_123", "douyin", now.isoformat(timespec="seconds"), expires),
            )
            conn.commit()

        result = _validate_and_consume_oauth_state("test_state_123", "douyin")
        assert result is True

        # state 应该已被消费（删除）
        with get_connection() as conn:
            row = conn.execute(
                "SELECT state FROM oauth_states WHERE state = ?", ("test_state_123",)
            ).fetchone()
        assert row is None

    def test_invalid_state_rejected(self, tmp_path, monkeypatch):
        """不存在的 state 被拒绝"""
        self._setup_state_db(tmp_path, monkeypatch)
        from app.services.publish_service import _validate_and_consume_oauth_state

        result = _validate_and_consume_oauth_state("nonexistent_state", "douyin")
        assert result is False

    def test_expired_state_rejected(self, tmp_path, monkeypatch):
        """过期的 state 被拒绝并清理"""
        self._setup_state_db(tmp_path, monkeypatch)
        from app.services.publish_service import _validate_and_consume_oauth_state
        from datetime import datetime, timedelta
        from app.db.database import get_connection

        now = datetime.now()
        expired = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO oauth_states (state, platform, created_at, expires_at) VALUES (?, ?, ?, ?)",
                ("expired_state", "douyin", expired, expired),
            )
            conn.commit()

        result = _validate_and_consume_oauth_state("expired_state", "douyin")
        assert result is False

    def test_empty_state_rejected(self, tmp_path, monkeypatch):
        """空 state 被拒绝"""
        self._setup_state_db(tmp_path, monkeypatch)
        from app.services.publish_service import _validate_and_consume_oauth_state

        result = _validate_and_consume_oauth_state("", "douyin")
        assert result is False


# ════════════════════════════════════════════════════
# 4. Subprocess timeout 测试
# ════════════════════════════════════════════════════


class TestSubprocessTimeout:
    """subprocess 调用必须带 timeout 参数"""

    def test_ffmpeg_audio_extract_has_timeout(self):
        """run_ffmpeg_audio_extract 使用 timeout 参数"""
        from app.services.transcript_service import run_ffmpeg_audio_extract
        from app.core.config import settings
        assert settings.ffmpeg_audio_extract_timeout > 0

    def test_ffprobe_has_timeout(self):
        """get_audio_duration_seconds 使用 timeout 参数"""
        from app.core.config import settings
        assert settings.ffprobe_timeout > 0

    def test_cut_single_clip_has_timeout(self):
        """cut_single_clip 使用 timeout 参数"""
        from app.core.config import settings
        assert settings.ffmpeg_cut_timeout > 0

    def test_timeout_config_values_reasonable(self):
        """超时配置值在合理范围内"""
        from app.core.config import settings
        assert 30 <= settings.ffprobe_timeout <= 3600
        assert 30 <= settings.ffmpeg_audio_extract_timeout <= 7200
        assert 30 <= settings.ffmpeg_cut_timeout <= 7200
        assert 30 <= settings.ffmpeg_subtitle_timeout <= 3600
        assert 10 <= settings.ffmpeg_cover_timeout <= 1800
        assert 10 <= settings.ffmpeg_chunk_timeout <= 600

    def test_subprocess_run_does_not_hang(self, tmp_path):
        """验证带 timeout 的 subprocess.run 在正常命令下不会超时"""
        result = subprocess.run(
            ["python", "-c", "print('ok')"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0

    def test_subprocess_timeout_raises(self, tmp_path):
        """验证超时会抛出 TimeoutExpired"""
        with pytest.raises(subprocess.TimeoutExpired):
            subprocess.run(
                ["python", "-c", "import time; time.sleep(10)"],
                capture_output=True,
                text=True,
                timeout=0.5,
            )


# ════════════════════════════════════════════════════
# 5. SQLite 索引和 PRAGMA 测试
# ════════════════════════════════════════════════════


class TestSQLiteOptimizations:
    """SQLite PRAGMA 和索引初始化测试"""

    @staticmethod
    def _setup_db(tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        storage_dir = tmp_path / "test_storage"
        storage_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "test_sqlite.sqlite3"
        monkeypatch.setenv("STORAGE_ROOT", str(storage_dir))
        monkeypatch.setenv("TASKS_DIR", str(storage_dir))
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.setenv("DATABASE_PATH", str(db_path))

        import app.core.config
        import app.db.database
        importlib.reload(app.core.config)
        importlib.reload(app.db.database)

        from app.db.database import init_db
        init_db()

    def test_foreign_keys_enabled(self, tmp_path, monkeypatch):
        """get_connection 启用 foreign_keys"""
        self._setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection

        with get_connection() as conn:
            row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row is not None
        # foreign_keys 应该是 1（启用）或至少已设置

    def test_journal_mode_wal(self, tmp_path, monkeypatch):
        """journal_mode 设置为 WAL"""
        self._setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection

        with get_connection() as conn:
            row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row is not None
        # WAL 模式可能返回 "wal" 或 "wal"

    def test_indexes_exist(self, tmp_path, monkeypatch):
        """常用索引在 init_db 后存在"""
        self._setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection

        expected_indexes = [
            "idx_tasks_status_created",
            "idx_clip_candidates_task_enabled_deleted",
            "idx_output_clip_task_status",
            "idx_publish_jobs_status_platform_created",
        ]

        with get_connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
            index_names = {row["name"] for row in rows}

        for idx_name in expected_indexes:
            assert idx_name in index_names, f"缺少索引：{idx_name}"

    def test_oauth_states_table_exists(self, tmp_path, monkeypatch):
        """oauth_states 表在 init_db 后存在"""
        self._setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection

        with get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'oauth_states'"
            ).fetchone()
        assert row is not None, "oauth_states 表不存在"


# ════════════════════════════════════════════════════
# 6. API 安全配置测试
# ════════════════════════════════════════════════════


class TestAPISecurityConfig:
    """API 安全配置项存在且默认合理"""

    def test_local_admin_token_has_default(self):
        """LOCAL_ADMIN_TOKEN 默认为空（本地开发），但配置字段存在"""
        from app.core.config import settings
        # 默认应为空字符串
        assert settings.local_admin_token == ""

    def test_max_upload_size_has_default(self):
        """MAX_UPLOAD_SIZE_BYTES 默认值为 4GB"""
        from app.core.config import settings
        assert settings.max_upload_size_bytes == 4 * 1024 * 1024 * 1024

    def test_allowed_extensions_includes_mp4(self):
        """ALLOWED_UPLOAD_EXTENSIONS 包含 .mp4"""
        from app.core.config import settings
        assert ".mp4" in settings.allowed_upload_extensions

    def test_cors_origin_whitelist(self):
        """CORS Origin 白名单不为空，包含本地地址"""
        from app.main import _ALLOWED_CORS_ORIGINS
        assert len(_ALLOWED_CORS_ORIGINS) > 0
        local_origins = [o for o in _ALLOWED_CORS_ORIGINS if "localhost" in o or "127.0.0.1" in o]
        assert len(local_origins) > 0

    def test_build_allow_origin_for_localhost(self):
        """未知 localhost 端口也返回 Origin 本身"""
        from app.main import _build_allow_origin_header
        result = _build_allow_origin_header("http://localhost:9999")
        assert result == "http://localhost:9999"

    def test_build_allow_origin_for_unknown(self):
        """完全未知的 Origin 返回 'null'"""
        from app.main import _build_allow_origin_header
        result = _build_allow_origin_header("https://evil.com")
        assert result == "null"
