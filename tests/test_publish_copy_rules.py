from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.database import get_connection, init_db
from app.models.task import PublishSendJobUpdate
from app.services import publish_service
from app.services.publish_copy_rules import (
    PUBLISH_COPY_RULE_VERSION,
    build_generated_douyin_publish_copy,
    split_publish_tags,
    validate_douyin_publish_copy,
)


PREFIX = "test-publish-copy-rules-"
VALID_TITLE = "小S当场追问陈汉典到底在模仿谁"
VALID_DESCRIPTION = "陈汉典刚说自己像潘玮柏，小S立刻给出另一答案"
VALID_TAGS = "综艺,高光,小S,反转"


@pytest.fixture(autouse=True)
def clean_rows():
    init_db()
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM publish_accounts WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _seed_job(
    *,
    platform: str = "douyin",
    status: str = "WAITING",
    scheduled_at: str = "",
    output_active: bool = True,
    provider_response: str = "",
    title: str = "旧标题",
    description: str = "旧简介不会被失败结果覆盖",
    tags: str = "旧标签",
) -> str:
    suffix = uuid4().hex[:8]
    task_id = f"{PREFIX}task-{suffix}"
    output_id = f"{PREFIX}output-{suffix}"
    job_id = f"{PREFIX}job-{suffix}"
    now = _now()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (id, task_name, task_dir_name, platform, status, created_at, updated_at)
            VALUES (?, '文案规则测试', ?, ?, 'COMPLETED', ?, ?)
            """,
            (task_id, task_id, platform, now, now),
        )
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, output_file_path, output_file_name, status, is_active, created_at, updated_at
            ) VALUES (?, ?, 'test.mp4', 'test.mp4', 'completed', ?, ?, ?)
            """,
            (output_id, task_id, 1 if output_active else 0, now, now),
        )
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption,
                tags, hashtags, cover_file_path, scheduled_at, schedule_timezone,
                status, provider_response, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'local_browser', 'original', 'test.mp4', 'test.mp4',
                ?, ?, ?, ?, ?, 'cover.jpg', ?, 'Asia/Shanghai', ?, ?, ?, ?)
            """,
            (
                job_id,
                task_id,
                output_id,
                output_id,
                platform,
                title,
                description,
                description,
                tags,
                tags,
                scheduled_at,
                status,
                provider_response,
                now,
                now,
            ),
        )
        connection.commit()
    return job_id


def _raw_job(job_id: str) -> dict:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row)


def _seed_account(*, login_status: str = "normal") -> str:
    account_id = f"{PREFIX}account-{uuid4().hex[:8]}"
    now = _now()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO publish_accounts (
                id, platform, account_name, auth_type, login_status, login_message, created_at, updated_at
            ) VALUES (?, 'douyin', '抖音测试账号', 'browser_profile', ?, '', ?, ?)
            """,
            (account_id, login_status, now, now),
        )
        connection.commit()
    return account_id


def test_generated_douyin_copy_obeys_all_limits_and_removes_cliches() -> None:
    copy = build_generated_douyin_publish_copy(
        "长" * 40,
        "小S刚开口就反转，现场爆笑，引发热议，不容错过，后面还有很多重复说明" * 2,
        ["这是一段很长的标签", "综艺", "综艺", "小S", "反转故事"],
    )

    assert len(copy["title"]) <= 30
    assert 15 <= len(copy["description"]) <= 35
    assert not any(phrase in copy["description"] for phrase in ("现场爆笑", "引发热议", "不容错过"))
    tags = split_publish_tags(copy["tags"])
    assert 4 <= len(tags) <= 6
    assert len(tags) == len(set(tags))
    assert all(2 <= len(tag) <= 3 for tag in tags)
    validate_douyin_publish_copy(copy["title"], copy["description"], copy["tags"])


def test_manual_douyin_copy_rejects_overlong_title_and_bilibili_keeps_80_chars() -> None:
    with pytest.raises(ValueError, match="不能超过 30 字"):
        validate_douyin_publish_copy("长" * 31, VALID_DESCRIPTION, VALID_TAGS)

    assert len(publish_service._sanitize_publish_title("长" * 90, platform="bilibili")) == 80


def test_normalized_job_uses_the_same_account_resolution_as_readiness() -> None:
    job_id = _seed_job(title=VALID_TITLE, description=VALID_DESCRIPTION, tags=VALID_TAGS)
    unique_account = _seed_account()

    unique = publish_service.get_publish_job(job_id)
    assert unique["effective_account_id"] == unique_account
    assert unique["content_complete"] is True
    assert unique["content_status_message"] == "内容完整"

    _seed_account()
    multiple = publish_service.get_publish_job(job_id)
    assert multiple["content_complete"] is False
    assert multiple["missing_fields"] == ["发布账号"]

    with get_connection() as connection:
        connection.execute("DELETE FROM publish_accounts WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()
    _seed_account(login_status="invalid")
    invalid = publish_service.get_publish_job(job_id)
    assert invalid["content_status_message"] == "账号需登录"


def test_ai_failure_does_not_overwrite_existing_copy(monkeypatch) -> None:
    job_id = _seed_job(title=VALID_TITLE, description=VALID_DESCRIPTION, tags=VALID_TAGS)
    before = _raw_job(job_id)

    monkeypatch.setattr(
        publish_service,
        "generate_publish_metadata",
        lambda *_args, **_kwargs: {
            "title": "失败回退标题",
            "description": "失败回退简介不应写入数据库",
            "tags": "综艺,高光,笑点,反转",
            "source": "rule",
            "error": "模拟 AI 网络失败",
        },
    )

    with pytest.raises(ValueError, match="已保留原文"):
        publish_service.regenerate_send_job_metadata(job_id, use_ai=True)
    after = _raw_job(job_id)
    assert (after["title"], after["description"], after["caption"], after["tags"], after["hashtags"]) == (
        before["title"],
        before["description"],
        before["caption"],
        before["tags"],
        before["hashtags"],
    )


def test_manual_save_binds_unique_account_and_synchronizes_alias_fields() -> None:
    account_id = _seed_account()
    job_id = _seed_job(title=VALID_TITLE, description=VALID_DESCRIPTION, tags=VALID_TAGS)

    publish_service.update_send_job(
        job_id,
        PublishSendJobUpdate(
            title=VALID_TITLE,
            description=VALID_DESCRIPTION,
            tags=VALID_TAGS,
            cover_file_path="cover.jpg",
        ),
    )

    saved = _raw_job(job_id)
    assert saved["account_id"] == account_id
    assert saved["description"] == saved["caption"] == VALID_DESCRIPTION
    assert saved["tags"] == saved["hashtags"] == "综艺, 高光, 小S, 反转"
    assert json.loads(saved["provider_response"])["metadata_upgrade_status"] == "manual_saved"

    with pytest.raises(ValueError, match="不能超过 30 字"):
        publish_service.update_send_job(
            job_id,
            PublishSendJobUpdate(
                title="长" * 31,
                description=VALID_DESCRIPTION,
                tags=VALID_TAGS,
                cover_file_path="cover.jpg",
            ),
        )


def test_pending_upgrade_is_backed_up_idempotent_and_preserves_failures(monkeypatch, tmp_path: Path) -> None:
    account_id = _seed_account()
    success_id = _seed_job()
    failure_id = _seed_job(title="失败旧标题", description="失败旧简介仍需原样保留", tags="旧标签")
    racing_id = _seed_job(title="并发编辑旧标题", description="并发编辑旧简介仍需原样保留", tags="旧标签")
    scheduled_id = _seed_job(scheduled_at="2026-09-01T00:00:00+00:00")
    bilibili_id = _seed_job(platform="bilibili")
    inactive_id = _seed_job(output_active=False)
    events: list[str] = []
    calls: list[str] = []

    def fake_backup(*_args, **kwargs):
        assert kwargs["cooldown"].total_seconds() == 0
        events.append("backup")
        return tmp_path / "backup.sqlite3"

    def fake_generate(item, use_ai=False, *, platform="douyin"):
        assert events == ["backup"]
        assert use_ai is True
        assert platform == "douyin"
        calls.append(item["output_clip_id"])
        if item["output_clip_id"] == _raw_job(failure_id)["output_clip_id"]:
            return {
                "title": "失败回退标题",
                "description": "失败回退简介不应写入",
                "tags": VALID_TAGS,
                "source": "rule",
                "error": "模拟生成失败",
            }
        if item["output_clip_id"] == _raw_job(racing_id)["output_clip_id"]:
            with get_connection() as connection:
                connection.execute(
                    "UPDATE publish_jobs SET scheduled_at = ?, status = 'SCHEDULED', updated_at = ? WHERE id = ?",
                    ("2026-09-02T00:00:00+00:00", datetime.now(timezone.utc).isoformat(), racing_id),
                )
                connection.commit()
        return {
            "title": VALID_TITLE,
            "description": VALID_DESCRIPTION,
            "tags": VALID_TAGS,
            "source": "ai:test",
            "error": "",
            "policy_version": PUBLISH_COPY_RULE_VERSION,
        }

    monkeypatch.setattr(publish_service, "create_publish_migration_backup", fake_backup)
    monkeypatch.setattr(publish_service, "generate_publish_metadata", fake_generate)

    result = publish_service.upgrade_pending_douyin_metadata()
    assert result["upgraded_count"] == 1
    assert result["failed_count"] == 1
    assert result["backup_created"] is True
    assert len(calls) == 3

    success = _raw_job(success_id)
    assert success["description"] == success["caption"] == VALID_DESCRIPTION
    assert success["tags"] == success["hashtags"] == VALID_TAGS
    assert success["account_id"] == account_id
    assert json.loads(success["provider_response"])["metadata_upgrade_status"] == "upgraded"

    failure = _raw_job(failure_id)
    assert failure["title"] == "失败旧标题"
    assert failure["description"] == failure["caption"] == "失败旧简介仍需原样保留"
    assert failure["tags"] == failure["hashtags"] == "旧标签"
    assert failure["account_id"] == account_id
    failure_provider = json.loads(failure["provider_response"])
    assert failure_provider["metadata_policy_version"] == PUBLISH_COPY_RULE_VERSION
    assert failure_provider["metadata_upgrade_status"] == "failed"

    racing = _raw_job(racing_id)
    assert racing["title"] == "并发编辑旧标题"
    assert racing["scheduled_at"] == "2026-09-02T00:00:00+00:00"

    assert _raw_job(scheduled_id)["title"] == "旧标题"
    assert _raw_job(bilibili_id)["title"] == "旧标题"
    assert _raw_job(inactive_id)["title"] == "旧标题"

    second = publish_service.upgrade_pending_douyin_metadata()
    assert second["upgraded_count"] == 0
    assert second["failed_count"] == 0
    assert len(calls) == 3
