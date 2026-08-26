from __future__ import annotations

import json
import socket
import threading
import time
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import uvicorn

playwright = pytest.importorskip("playwright.sync_api")

from app.core.config import settings  # noqa: E402
from app.db.database import get_connection, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import publish_service  # noqa: E402
from app.services.publish_scheduler import PublishScheduler  # noqa: E402


PREFIX = "test-browser-publish-"


def _seed_job(tmp_path: Path, index: int, platform: str = "douyin", task_key: str = "") -> str:
    now = (datetime.now(timezone.utc) + timedelta(seconds=index)).isoformat(timespec="seconds").replace("+00:00", "Z")
    task_id = f"{PREFIX}task-{task_key}" if task_key else f"{PREFIX}{uuid4().hex[:8]}"
    clip_id = f"{PREFIX}clip-{uuid4().hex[:8]}"
    job_id = f"{PREFIX}job-{uuid4().hex[:8]}"
    video = tmp_path / f"browser-{index}.mp4"
    video.write_bytes(b"fake-video")
    with get_connection() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO tasks (id, task_name, task_dir_name, platform, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'COMPLETED', ?, ?)",
            (task_id, f"浏览器排期任务 {index}", task_id, platform, now, now),
        )
        connection.execute(
            "INSERT INTO output_clip (id, task_id, output_file_path, output_file_name, status, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, 'completed', 1, ?, ?)",
            (clip_id, task_id, str(video), video.name, now, now),
        )
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption,
                tags, hashtags, scheduled_at, schedule_timezone, status, provider_response, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'manual_export', 'original', ?, ?, ?, '测试正文',
                '测试正文', '测试', '测试', '', 'Asia/Shanghai', 'WAITING', ?, ?, ?)
            """,
            (
                job_id,
                task_id,
                clip_id,
                clip_id,
                platform,
                str(video),
                str(video),
                f"浏览器测试片段 {index}",
                json.dumps({"metadata_policy_version": 2}),
                now,
                now,
            ),
        )
        connection.commit()
    return job_id


def _cleanup():
    with get_connection() as connection:
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_publish_center_schedule_preview_confirm_and_export(monkeypatch, tmp_path):
    init_db()
    _cleanup()
    douyin_jobs = [
        _seed_job(tmp_path, index, task_key="shared" if index in {1, 2} else "")
        for index in range(1, 11)
    ]
    unscheduled = _seed_job(tmp_path, 0)
    first = douyin_jobs[0]
    newest = douyin_jobs[-1]
    bilibili = _seed_job(tmp_path, 11, "bilibili")
    failed = _seed_job(tmp_path, 12)
    failed_schedule = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET status = 'FAILED', scheduled_at = ?, finished_at = ?,
                error_code = 'browser_test_failed', error_message = '测试失败记录'
            WHERE id = ?
            """,
            (failed_schedule, failed_schedule, failed),
        )
        connection.commit()
    generated_cover = tmp_path / "browser-batch-cover.jpg"
    generated_cover.write_bytes(b"fake-cover")
    preserved_cover = tmp_path / "browser-preserved-cover.jpg"
    preserved_cover.write_bytes(b"existing-cover")
    with get_connection() as connection:
        connection.execute(
            "UPDATE publish_jobs SET cover_file_path = ? WHERE id = ?",
            (str(preserved_cover), unscheduled),
        )
        connection.commit()

    def fake_backfill_covers(platform=None):
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id FROM publish_jobs
                WHERE task_id LIKE ? AND status = 'WAITING' AND platform = ?
                  AND TRIM(COALESCE(cover_file_path, '')) = ''
                """,
                (f"{PREFIX}%", platform),
            ).fetchall()
            connection.execute(
                """
                UPDATE publish_jobs
                SET cover_mode = 'time', cover_time_seconds = 30, cover_file_path = ?
                WHERE task_id LIKE ? AND status = 'WAITING' AND platform = ?
                  AND TRIM(COALESCE(cover_file_path, '')) = ''
                """,
                (str(generated_cover), f"{PREFIX}%", platform),
            )
            connection.commit()
        jobs = [publish_service.get_publish_job(row["id"]) for row in rows]
        return {
            "status": "ok",
            "message": f"已补齐 {len(jobs)} 条发布任务。",
            "generated_cover_count": len(jobs),
            "reused_cover_count": 0,
            "updated_job_count": len(jobs),
            "failed_clip_count": 0,
            "errors": [],
            "jobs": jobs,
        }

    monkeypatch.setattr(publish_service, "backfill_missing_publish_covers", fake_backfill_covers)
    future_start = datetime.now() + timedelta(days=2)
    future_day = future_start.strftime("%Y-%m-%d")
    future_day_label = f"{future_start.year} 年 {future_start.month} 月 {future_start.day} 日"
    following_day = (future_start + timedelta(days=1)).strftime("%Y-%m-%d")
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started

    try:
        with playwright.sync_playwright() as runtime:
            chrome_path = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
            if not chrome_path.exists():
                pytest.skip("浏览器级测试需要本机安装 Google Chrome")
            browser = runtime.chromium.launch(headless=True, executable_path=str(chrome_path))
            context = browser.new_context(timezone_id="Asia/Shanghai")
            page = context.new_page()
            page.route(
                "**/api/publish/schedules/next-start",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "status": "ok",
                            "timezone": "Asia/Shanghai",
                            "latest_scheduled_at_local_display": f"{future_day} 19:00",
                            "next_start_at_local": f"{future_day}T22:00",
                            "next_start_at_local_display": f"{future_day} 22:00",
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            def fulfill_metadata(route):
                job_id = route.request.url.split("/jobs/", 1)[1].split("/", 1)[0]
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "status": "ok",
                            "job": {
                                "id": job_id,
                                "platform": "douyin",
                                "status": "WAITING",
                                "title": "小S追问陈汉典到底在模仿谁",
                                "description": "陈汉典刚说自己像潘玮柏，小S立刻给出另一答案",
                                "tags": "综艺,高光,小S,反转",
                                "content_complete": True,
                                "content_status_message": "内容完整",
                                "content_status_tone": "green",
                            },
                        },
                        ensure_ascii=False,
                    ),
                )

            page.route("**/api/publish/jobs/*/metadata?use_ai=true", fulfill_metadata)
            page.goto(f"http://127.0.0.1:{port}/publish?platform=bilibili", wait_until="networkidle")

            assert page.locator('[data-publish-platform="bilibili"]').count() == 0
            assert page.locator(
                f'[data-publish-row][data-section="content"][data-job-id="{bilibili}"]'
            ).count() == 0
            first_content = page.locator(
                f'[data-publish-row][data-section="content"][data-job-id="{first}"]'
            )
            newest_content = page.locator(
                f'[data-publish-row][data-section="content"][data-job-id="{newest}"]'
            )
            cover_button = page.locator("[data-backfill-covers]")
            assert "抖音" in cover_button.inner_text()
            assert "10" in cover_button.inner_text()
            unsaved_title = newest_content.locator('[name="title"]')
            unsaved_title.fill("这段标题还没有保存")
            cover_button.click()
            page.locator("#send-center-message").filter(has_text="已补齐 10 条").wait_for()
            assert unsaved_title.input_value() == "这段标题还没有保存"
            assert newest_content.locator('[name="cover_file_path"]').input_value() == str(generated_cover)
            assert newest_content.locator("[data-cover-preview]").is_visible()
            assert cover_button.is_disabled()
            assert first_content.is_hidden()
            assert newest_content.is_visible()
            first_group = first_content.locator("xpath=ancestor::section[@data-publish-task-group]")
            newest_group = newest_content.locator("xpath=ancestor::section[@data-publish-task-group]")
            assert first_group.locator("[data-task-group-toggle]").inner_text() == "展开"
            assert newest_group.locator("[data-task-group-toggle]").inner_text() == "收起"
            first_group.locator("[data-task-group-toggle]").click()
            assert first_content.is_visible()
            second_content = page.locator(
                f'[data-publish-row][data-section="content"][data-job-id="{douyin_jobs[1]}"]'
            )
            group_select = first_group.locator("[data-task-group-select]")
            group_select.check()
            assert first_content.locator("[data-publish-select]").is_checked()
            assert second_content.locator("[data-publish-select]").is_checked()
            assert page.locator("[data-batch-ai]").inner_text() == "AI 重写已选文案"
            page.locator("[data-batch-ai]").click()
            page.locator("#send-center-message").filter(has_text="已选文案 AI 重写完成：成功 2 条").wait_for()
            assert first_content.locator('[name="description"]').input_value() == "陈汉典刚说自己像潘玮柏，小S立刻给出另一答案"
            assert second_content.locator('[name="tags"]').input_value() == "综艺,高光,小S,反转"
            first_content.locator("[data-publish-select]").uncheck()
            assert group_select.evaluate("element => element.indeterminate") is True
            group_select.check()
            assert first_group.locator("[data-task-group-select-label]").inner_text() == "取消全选"
            group_select.uncheck()
            assert not first_content.locator("[data-publish-select]").is_checked()
            assert not second_content.locator("[data-publish-select]").is_checked()
            first_group.locator("[data-task-group-toggle]").click()
            assert first_content.is_hidden()
            group_select.check()
            assert first_content.locator("[data-publish-select]").is_checked()
            assert second_content.locator("[data-publish-select]").is_checked()
            group_select.uncheck()

            page.locator('[data-center-tab="schedule"]').click()
            assert page.locator("[data-batch-ai]").is_hidden()
            assert page.locator('[data-schedule-calendar] .publish-calendar-day').count() == 42
            assert "抖音" in page.locator("[data-calendar-title]").inner_text()
            assert page.locator(
                f'[data-publish-row][data-section="schedule"][data-job-id="{bilibili}"]'
            ).count() == 0
            for job_id in douyin_jobs:
                page.locator(
                    f'[data-publish-row][data-section="schedule"][data-job-id="{job_id}"] [data-publish-select]'
                ).check()
            page.locator("[data-open-schedule-drawer]").click()
            assert page.locator('[name="daily_start_time"]').input_value() == "07:00"
            assert page.locator('[name="daily_end_time"]').input_value() == "00:00"
            page.locator("[data-use-latest-schedule]").click()
            page.locator("[data-latest-schedule-note]").filter(
                has_text=f"本次第 1 条：{future_day} 22:00"
            ).wait_for()
            assert page.locator('[name="start_at_local"]').input_value() == f"{future_day}T22:00"
            assert page.locator("[data-latest-schedule-note]").inner_text() == (
                f"当前最晚：{future_day} 19:00；本次第 1 条：{future_day} 22:00"
            )
            page.locator('[name="start_at_local"]').fill("2020-01-01T06:00")
            page.locator("[data-preview-schedule]").click()
            assert page.locator("[data-schedule-feedback].tone-red").filter(
                has_text="请选择晚于当前时间"
            ).is_visible()
            page.locator('[name="start_at_local"]').fill(f"{future_day}T06:00")
            page.locator('[name="daily_start_time"]').fill("06:00")
            page.locator('[name="daily_end_time"]').fill("00:00")
            preview_button = page.locator("[data-preview-schedule]")
            preview_button.click()
            page.locator("[data-confirm-schedule]:not([disabled])").wait_for()
            assert page.locator("[data-schedule-feedback]").filter(has_text="已生成 10 条").is_visible()
            assert page.locator("[data-schedule-preview] time").all_inner_texts() == [
                f"{future_day} 06:00",
                f"{future_day} 09:00",
                f"{future_day} 12:00",
                f"{future_day} 15:00",
                f"{future_day} 18:00",
                f"{future_day} 21:00",
                f"{following_day} 00:00",
                f"{following_day} 06:00",
                f"{following_day} 09:00",
                f"{following_day} 12:00",
            ]
            page.locator('[name="daily_end_time"]').fill("21:00")
            assert page.locator("[data-confirm-schedule]").is_disabled()
            assert "请先生成预览" in page.locator("[data-schedule-preview]").inner_text()
            page.locator('[name="daily_end_time"]').fill("00:00")
            preview_button.click()
            page.locator("[data-confirm-schedule]:not([disabled])").wait_for()

            page.locator("[data-confirm-schedule]").click()
            page.locator("[data-schedule-drawer]").wait_for(state="hidden")
            assert f"{future_day} 06:00" in page.locator(
                f'[data-publish-row][data-section="schedule"][data-job-id="{first}"] [data-row-schedule]'
            ).inner_text()
            visible_test_rows = page.locator(
                f'[data-publish-row][data-section="schedule"][data-job-id^="{PREFIX}"]:visible'
            )
            assert visible_test_rows.evaluate_all("rows => rows.map((row) => row.dataset.jobId)") == [
                *douyin_jobs,
                unscheduled,
            ]

            calendar_day = page.locator(f'[data-calendar-date="{future_day}"]')
            assert calendar_day.locator(".calendar-job-chip").all_inner_texts() == [
                "06:00 浏览器测试片段 1",
                "09:00 浏览器测试片段 2",
            ]
            assert calendar_day.locator(".calendar-job-more").inner_text() == "另有 4 条"
            calendar_day.click()
            day_detail = page.locator("[data-calendar-day-detail]")
            assert day_detail.is_visible()
            assert day_detail.locator("[data-calendar-day-title]").inner_text() == future_day_label
            assert "6 条排期" in day_detail.locator("[data-calendar-day-summary]").inner_text()
            assert day_detail.locator(".publish-calendar-day-item time").all_inner_texts() == [
                "06:00", "09:00", "12:00", "15:00", "18:00", "21:00",
            ]
            day_detail.locator(f'[data-calendar-detail-job="{first}"]').click()
            assert "is-calendar-focus" in page.locator(
                f'[data-publish-row][data-section="schedule"][data-job-id="{first}"]'
            ).get_attribute("class")
            page.locator("[data-calendar-day-close]").click()
            assert day_detail.is_hidden()
            calendar_day.focus()
            calendar_day.press("Enter")
            assert day_detail.is_visible()

            dialogs = []
            page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
            page.locator(
                f'[data-publish-row][data-section="schedule"][data-job-id="{newest}"] [data-cancel-job]'
            ).click()
            page.locator('[data-center-panel="content"].active').wait_for()
            assert any("返回“内容准备”" in message for message in dialogs)
            assert newest_content.is_visible()
            assert page.locator(
                f'[data-publish-row][data-section="schedule"][data-job-id="{newest}"]'
            ).get_attribute("data-status") == "WAITING"
            assert "未排期" in page.locator(
                f'[data-publish-row][data-section="schedule"][data-job-id="{newest}"] [data-row-schedule]'
            ).inner_text()
            assert "已取消发送并返回内容准备" in page.locator("#send-center-message").inner_text()

            page.locator('[data-center-tab="schedule"]').click()
            assert page.locator(
                f'[data-publish-row][data-section="schedule"][data-job-id^="{PREFIX}"]:visible'
            ).evaluate_all("rows => rows.map((row) => row.dataset.jobId)") == [
                *douyin_jobs[:-1],
                newest,
                unscheduled,
            ]
            page.locator(f'[data-publish-row][data-section="schedule"][data-job-id="{first}"] [data-publish-now]').click()
            assert any("抖音" in message for message in dialogs)
            page.locator("#send-center-message").filter(has_text="统一调度").wait_for()
            # 此测试使用 lifespan="off"，只显式执行当前测试任务，避免扫描同一数据库里的其他排期。
            PublishScheduler().execute_job(first)
            page.locator('[data-center-tab="history"]').click()
            page.wait_for_function(
                "jobId => document.querySelector(`[data-history-record][data-job-id=\"${jobId}\"]`)?.dataset.status === 'EXPORTED'",
                arg=first,
                timeout=10_000,
            )
            assert page.locator("[data-history-calendar] .publish-history-calendar-day").count() == 42
            assert "抖音" in page.locator("[data-history-calendar-title]").inner_text()

            failed_row = page.locator(f'[data-history-record][data-job-id="{failed}"]')
            failed_row.wait_for()
            assert failed_row.locator("[data-retry-job]").inner_text() == "立即发送"
            assert failed_row.locator("[data-restore-job]").count() == 0

            today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
            with page.expect_response(
                lambda response: "/api/publish/history/records?" in response.url
                and f"date={today}" in response.url
            ) as history_response:
                page.locator(f'[data-history-date="{today}"]').click()
            assert history_response.value.ok
            page.locator("[data-history-list-title]").filter(has_text=today).wait_for()
            page.locator("[data-history-clear-date]").click()

            exported_row = page.locator(f'[data-history-record][data-job-id="{first}"]')
            exported_row.locator("[data-history-hide]").click()
            exported_row.wait_for(state="detached")
            page.locator('[data-history-view="deleted"]').click()
            deleted_row = page.locator(f'[data-history-record][data-job-id="{first}"]')
            deleted_row.wait_for()
            deleted_row.locator("[data-history-restore]").click()
            deleted_row.wait_for(state="detached")
            page.locator('[data-history-view="active"]').click()
            page.locator(f'[data-history-record][data-job-id="{first}"]').wait_for()
            page.set_viewport_size({"width": 720, "height": 1000})
            page.wait_for_timeout(200)
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
            assert page.locator(
                f'[data-history-record][data-job-id="{failed}"] [data-retry-job]'
            ).is_visible()
            context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        _cleanup()
