"""Batch review: draft edits, queued recuts, human consent and preparation stay connected."""
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import httpx
import pytest
import uvicorn

from app.db import database as db
from app.main import app
from app.services import job_worker, production_review_service as review, publish_service
from app.services import video_cut_workflow_service as cuts
from app.services.video_cut_service import CutResult
from tests.test_content_review_browser import _free_port
from tests.test_cut_atomicity import _insert_candidate, _create_task
from tests.test_production_review import output_batch as _output_batch, batch_db as _batch_db, human_db as _human_db, consent

output_batch, batch_db, human_db = _output_batch, _batch_db, _human_db


@pytest.fixture
def browser_flow(output_batch, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    chrome = Path(os.environ.get('PROGRAMFILES', 'C:/Program Files'))/'Google/Chrome/Application/chrome.exe'
    if not chrome.exists():
        pytest.skip('Chrome unavailable')
    monkeypatch.setattr(publish_service, '_generate_default_publish_cover', lambda *_: {})
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', lifespan='off'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.05)
    try:
        assert server.started
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=str(chrome), headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            yield page, playwright.expect, f'http://127.0.0.1:{port}'
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize('width', [1440, 390])
def test_batch_add_selection_save_recut_review_and_prepare(width, browser_flow, output_batch, monkeypatch, tmp_path):
    page, expect, base = browser_flow
    page.set_viewport_size({'width': width, 'height': 1000})
    task, original, output = output_batch
    extra = uuid4().hex
    _insert_candidate(task, extra)
    with db.get_connection() as c:
        c.execute('UPDATE clip_candidates SET enabled=0 WHERE id=?', (extra,))
        c.commit()
    review.confirm(task, consent(task))
    assert publish_service.sync_task_publish_jobs(task)['created_count'] == 1
    from app.services import ai_analysis_workflow_service as analysis
    monkeypatch.setattr(analysis, 'get_task_ai_analysis_meta', lambda *_: {'coverage_percent': 100})
    monkeypatch.setattr(analysis, 'validate_ai_analysis_meta_for_cut', lambda value, *_: value)

    def cut(**kwargs):
        folder = kwargs['output_dir']
        folder.mkdir(parents=True, exist_ok=True)
        results = []
        for clip in kwargs['clips']:
            path = folder/f"{clip['id']}.mp4"
            path.write_bytes(b'isolated-new-cut')
            results.append(CutResult(clip['id'], str(path), path.name, 'completed', source_start_ms=1000, source_end_ms=3000))
        return results

    monkeypatch.setattr(cuts, 'cut_clips', cut)
    page.goto(f'{base}/tasks/{task}/clips/review', wait_until='networkidle')
    expect(page.locator('#production-review-prepare')).to_be_enabled()
    page.locator(f'[data-clip-id="{original}"] [name="enabled"]').uncheck()
    expect(page.locator('#production-review-status')).to_contain_text('共选中 0 条')
    expect(page.locator('#production-review-generate')).to_be_disabled()
    expect(page.locator('#production-review-prepare')).to_be_hidden()
    page.locator(f'[data-clip-id="{original}"] [name="enabled"]').check()
    expect(page.locator('#production-review-prepare')).to_be_enabled()
    page.locator(f'[data-clip-id="{extra}"] [name="enabled"]').check()
    expect(page.locator('#production-review-status')).to_contain_text('共选中 2 条')
    expect(page.locator('#production-review-prepare')).to_be_hidden()
    expect(page.locator('#production-review-choice')).to_be_hidden()
    expect(page.locator('#production-review-generate')).to_be_enabled()
    page.locator('#save-clips-button').click()
    expect(page.locator('#production-review-status')).to_contain_text('请重新生成成片')
    expect(page.locator('#production-review-generate')).to_be_enabled()
    page.locator('#production-review-generate').click()
    expect(page.locator('#cut-job-message')).to_contain_text('独立切片通道')
    expect(page.locator('#production-review-prepare')).to_be_hidden()
    expect(page.locator(f'[data-clip-id="{extra}"] [name="enabled"]')).to_be_disabled()
    with db.get_connection() as c:
        jobs = c.execute("SELECT id FROM workflow_jobs WHERE task_id=? AND job_type='video_cut'", (task,)).fetchall()
    assert len(jobs) == 1
    result = job_worker.execute_job(jobs[0]['id'])
    assert result['status'] == 'completed', result
    expect(page.locator('#production-review-outputs video')).to_have_count(2, timeout=15000)
    expect(page.locator('#production-review-confirm')).to_be_enabled()
    expect(page.locator('#production-review-prepare')).to_be_hidden()
    with db.get_connection() as c:
        assert c.execute('SELECT count(*) FROM publish_jobs WHERE task_id=?', (task,)).fetchone()[0] == 1
    assert not review.state(task)['approved']
    page.locator('#production-review-checked').check()
    page.locator('#production-review-confirm').click()
    page.wait_for_url(f'**/publish?task_id={task}&tab=content')
    expect(page.locator('[data-publish-task-group]:visible')).to_have_count(1)
    expect(page.locator('[data-section="content"]:visible')).to_have_count(2)
    expect(page.locator('[data-content-task-focus]')).to_contain_text('仅查看本任务')
    with db.get_connection() as c:
        jobs = c.execute('SELECT status, scheduled_at, attempt_count FROM publish_jobs WHERE task_id=?', (task,)).fetchall()
        assert sorted(row['status'] for row in jobs) == ['CANCELLED', 'WAITING', 'WAITING']
        assert all(not row['scheduled_at'] and not row['attempt_count'] for row in jobs)
        assert c.execute('SELECT count(*) FROM production_reviews WHERE task_id=?', (task,)).fetchone()[0] == 2
    assert Path(output['output_file_path']).exists()
    assert publish_service.sync_task_publish_jobs(task)['created_count'] == 0
    page.screenshot(path=str(tmp_path/f'batch-handoff-{width}.png'))
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')


def test_preparation_busy_does_not_block_web_and_failure_retries_without_reconfirm(browser_flow, output_batch, monkeypatch):
    page, expect, base = browser_flow
    task, _, _ = output_batch
    entered, release = threading.Event(), threading.Event()
    original_sync = publish_service.sync_task_publish_jobs

    def blocked_sync(*args, **kwargs):
        entered.set()
        release.wait(10)
        raise ValueError('模拟同步中断')

    monkeypatch.setattr(publish_service, 'sync_task_publish_jobs', blocked_sync)
    page.goto(f'{base}/tasks/{task}/clips/review', wait_until='networkidle')
    page.locator('#production-review-checked').check()
    page.locator('#production-review-confirm').click()
    try:
        assert entered.wait(5)
        expect(page.locator('#production-review-status')).to_contain_text('提取封面')
        expect(page.locator('#production-review-prepare')).to_be_disabled()
        expect(page.locator('#save-clips-button')).to_be_disabled()
        assert httpx.get(f'{base}/health', timeout=2).status_code == 200
    finally:
        release.set()
    expect(page.locator('#production-review-status')).to_contain_text('模拟同步中断')
    assert review.state(task)['approved']
    expect(page.locator('#production-review-prepare')).to_be_enabled()
    monkeypatch.setattr(publish_service, 'sync_task_publish_jobs', original_sync)
    page.locator('#production-review-prepare').click()
    page.wait_for_url(f'**/publish?task_id={task}&tab=content')
    with db.get_connection() as c:
        assert c.execute('SELECT count(*) FROM production_reviews WHERE task_id=?', (task,)).fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM publish_jobs WHERE task_id=? AND status='WAITING'", (task,)).fetchone()[0] == 1


def test_task_focus_can_return_to_all_content(browser_flow, output_batch):
    page, expect, base = browser_flow
    task, _, _ = output_batch
    review.confirm(task, consent(task))
    publish_service.sync_task_publish_jobs(task)
    other = 'test-atomic-' + uuid4().hex
    _create_task(other)
    with db.get_connection() as c:
        output = c.execute('SELECT * FROM output_clip WHERE task_id=?', (task,)).fetchone()
        c.execute("INSERT INTO output_clip(id,task_id,output_file_path,output_file_name,status,is_active,created_at,updated_at) VALUES(?,?,?,?,'completed',1,'test','test')",
                  (other, other, output['output_file_path'], 'other.mp4'))
        c.execute("INSERT INTO publish_jobs(id,task_id,output_clip_id,platform,status,created_at,updated_at) VALUES(?,?,?,'douyin','WAITING','test','test')", (other, other, other))
        c.commit()
    page.goto(f'{base}/publish?task_id={task}&tab=content', wait_until='networkidle')
    expect(page.locator('[data-publish-task-group]:visible')).to_have_count(1)
    expect(page.locator(f'[data-publish-task-group][data-task-id="{task}"]')).to_be_visible()
    page.locator('[data-clear-content-task]').click()
    expect(page.locator('[data-publish-task-group]:visible')).to_have_count(2)
    assert 'task_id=' not in page.url
