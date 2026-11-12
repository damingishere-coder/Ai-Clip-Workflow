"""Large queues stay bounded without changing publication or evidence semantics."""

import json
import os
import socket
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.db.database import get_connection
from app.main import app
from app.services import production_review_service as production
from app.services import publish_service, publish_center_reads
from tests.test_publish_task_grouping import (
    _insert_task,
    _insert_clip_job,
    _time,
    clean_publish_group_data as _clean,
)
from tests.test_weekly_content_review import sample as _sample, ready_report

clean_publish_group_data = _clean
sample = _sample


@pytest.fixture
def large_queue(tmp_path):
    task, _ = _insert_task(tmp_path, "分页性能", created_at=_time())
    base, clip, _ = _insert_clip_job(
        tmp_path,
        task,
        platform="douyin",
        status="SCHEDULED",
        created_at=_time(),
        publish_mode="manual_export",
    )
    with get_connection() as c:
        for i in range(1, 2000):
            output = f"{clip}-{i:04d}"
            job = f"{base}-{i:04d}"
            c.execute(
                """INSERT INTO output_clip(id,task_id,output_file_path,output_file_name,status,is_active,created_at,updated_at)
                SELECT ?,task_id,output_file_path,output_file_name,status,is_active,created_at,updated_at FROM output_clip WHERE id=?""",
                (output, clip),
            )
            c.execute(
                """INSERT INTO publish_jobs(id,task_id,output_clip_id,clip_id,platform,publish_mode,video_source,
                video_file_path,video_path,title,description,caption,tags,hashtags,scheduled_at,status,created_at,updated_at)
                SELECT ?,task_id,?,?,platform,publish_mode,video_source,video_file_path,video_path,?,description,caption,tags,hashtags,scheduled_at,?,created_at,updated_at
                FROM publish_jobs WHERE id=?""",
                (
                    job,
                    output,
                    output,
                    f"分页视频{i:04d}",
                    "SCHEDULED" if i < 700 else "PUBLISHED",
                    base,
                ),
            )
        c.commit()
    return task, base


def test_large_queue_has_bounded_html_and_exact_cross_page_selection(
    large_queue, monkeypatch
):
    task, base = large_queue
    normal = Mock(wraps=publish_service._normalize_job)
    monkeypatch.setattr(publish_service, "_normalize_job", normal)
    client = TestClient(app)
    page = client.get("/publish", params={"task_id": task})
    assert page.status_code == 200 and len(page.content) < 300000
    assert page.text.count("data-publish-row") == 50
    assert "<textarea" not in page.text
    assert normal.call_count == 50
    first = client.get("/api/publish/center/jobs", params={"task_id": task}).json()
    second = client.get(
        "/api/publish/center/jobs", params={"task_id": task, "page": 2}
    ).json()
    assert first["pagination"]["total"] == 700
    assert len(first["jobs"]) == len(second["jobs"]) == 50
    assert not ({j["id"] for j in first["jobs"]} & {j["id"] for j in second["jobs"]})
    selected = client.get(
        "/api/publish/center/selection", params={"task_id": task}
    ).json()["jobs"]
    assert len(selected) == 700 and all(
        set(j) == {"id", "account_id", "platform", "status"} for j in selected
    )
    assert (
        client.get("/api/publish/center/summary", params={"task_id": task}).json()[
            "total"
        ]
        == 700
    )
    assert (
        client.get("/api/publish/center/jobs", params={"q": "分页视频0001"}).json()[
            "pagination"
        ]["total"]
        == 1
    )
    job = first["jobs"][0]["id"]
    editor = client.get("/api/publish/center/editor/" + job).json()
    assert editor["html"].count("<textarea") == 1
    assert client.get("/api/publish/center/jobs?page_size=51").status_code == 422
    located = client.get("/api/publish/center/locate/" + base).json()["page"]
    assert located == 14


def test_approval_reuse_is_read_request_scoped(large_queue, monkeypatch):
    task, _ = large_queue
    check = Mock(return_value=None)
    monkeypatch.setattr(production, "require_cut_review", check)
    publish_center_reads.query(task_id=task)
    assert check.call_count == 1
    check.side_effect = ValueError("审批版本已失效")
    assert production.readiness_issue({"task_id": task}) == "审批版本已失效"
    assert check.call_count == 2
    with production.readiness_batch():
        assert production.readiness_issue({"task_id": task}) == "审批版本已失效"
    assert check.call_count == 3


def test_compact_weekly_list_and_full_detail_keep_frozen_evidence(sample):
    from app.services import weekly_review_service as weekly

    report, _ = ready_report(sample)
    with get_connection() as c:
        evidence = json.loads(
            c.execute(
                "SELECT evidence_json FROM weekly_content_reports WHERE id=?", (report,)
            ).fetchone()[0]
        )
        evidence["large_test_evidence"] = "完整证据" * 100000
        c.execute(
            "UPDATE weekly_content_reports SET evidence_json=? WHERE id=?",
            (json.dumps(evidence, ensure_ascii=False), report),
        )
        original = dict(
            c.execute(
                "SELECT * FROM weekly_content_reports WHERE id=?", (report,)
            ).fetchone()
        )
        for number in range(2):
            copied = {
                **original,
                "id": f"{report}-history-{number}",
                "revision": original["revision"] + number + 1,
            }
            columns = list(copied)
            c.execute(
                "INSERT INTO weekly_content_reports ("
                + ",".join(columns)
                + ") VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                list(copied.values()),
            )
        c.commit()
    client = TestClient(app)
    query = {"account_id": sample["account"]}
    compact = client.get(
        "/api/content-review/weekly-reports", params={**query, "compact": "true"}
    )
    assert len(compact.content) < 30000 and "large_test_evidence" not in compact.text
    detail = client.get(
        "/api/content-review/weekly-reports/" + report, params=query
    ).json()
    assert (
        detail["result"]["suggestions"]
        and "large_test_evidence" not in detail["evidence"]
    )
    full = client.get(
        "/api/content-review/weekly-reports/" + report,
        params={**query, "include_evidence": "true"},
    ).json()
    assert full["evidence"] == evidence
    assert weekly.list_reports(sample["account"])["reports"][0]["evidence"] == evidence
    paged = client.get(
        "/api/content-review/works", params={**query, "limit": 50, "page": 1}
    ).json()
    assert len(paged["works"]) <= 50 and paged["pagination"]["total"] > 0


def test_browser_cross_page_selection_and_unsaved_editor(large_queue):
    runtime = pytest.importorskip("playwright.sync_api")
    chrome = (
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        / "Google/Chrome/Application/chrome.exe"
    )
    if not chrome.exists():
        pytest.skip("需要本机 Chrome")
    task, base = large_queue
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        with runtime.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=str(chrome))
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(
                f"http://127.0.0.1:{port}/publish?task_id={task}",
                wait_until="networkidle",
            )
            controls = page.locator('[data-center-pagination="content"]')
            controls.locator("[data-center-select-page]").click()
            runtime.expect(page.locator("[data-selected-count]")).to_have_text("50")
            controls.locator("[data-center-next]").click()
            runtime.expect(
                controls.locator("[data-center-page-summary]")
            ).to_contain_text("第 2 /")
            runtime.expect(page.locator("[data-selected-count]")).to_have_text("50")
            controls.locator("[data-center-select-all]").click()
            runtime.expect(page.locator("[data-selected-count]")).to_have_text("700")
            # Group collapse does not fetch or construct a single editor.
            assert page.locator("[data-publish-editor]").count() == 0
            page.locator("[data-load-editor]").first.click()
            title = page.locator('[data-publish-editor] [name="title"]')
            title.fill("尚未保存的新标题")
            page.once("dialog", lambda dialog: dialog.dismiss())
            controls.locator("[data-center-next]").click()
            assert title.input_value() == "尚未保存的新标题"
            page.once("dialog", lambda dialog: dialog.accept())
            controls.locator("[data-center-next]").click()
            runtime.expect(
                controls.locator("[data-center-page-summary]")
            ).to_contain_text("第 3 /")
            page.locator('[data-center-tab="schedule"]').click()
            runtime.expect(
                page.locator(
                    '[data-center-pagination="schedule"] [data-center-page-summary]'
                )
            ).to_contain_text("700")
            day = page.locator("[data-calendar-date]").first
            runtime.expect(day).to_have_attribute(
                "aria-label", __import__("re").compile("700 条排期")
            )
            day.click()
            page.locator(f'[data-calendar-detail-job="{base}"]').click()
            runtime.expect(
                page.locator(
                    '[data-center-pagination="schedule"] [data-center-page-summary]'
                )
            ).to_contain_text("第 14 /")
            runtime.expect(
                page.locator(f'[data-section="schedule"][data-job-id="{base}"]')
            ).to_be_visible()
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
