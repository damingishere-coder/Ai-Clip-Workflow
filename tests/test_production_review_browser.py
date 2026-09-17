import os
from pathlib import Path
import threading
import time

import pytest
import uvicorn

from app.db import database as db
from app.main import app
from app.services import job_service
from tests.test_content_review_browser import _free_port
from tests.test_production_review import output_batch as _output_batch, batch_db as _batch_db, human_db as _human_db

output_batch, batch_db, human_db = _output_batch, _batch_db, _human_db


@pytest.mark.parametrize('width',[1440,390])
@pytest.mark.parametrize('output_batch,recut', [
    ('original', False), ('review', False), ('single-original', False), ('single-review', False),
    ('original', True),
], indirect=['output_batch'])
def test_actual_output_confirmation_then_stale_version(width, output_batch, recut, tmp_path, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    chrome = Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe'
    if not chrome.exists():
        pytest.skip('Chrome unavailable')
    task,candidate,_ = output_batch
    if recut:
        from tests.test_production_review import recut_with_old_draft
        recut_with_old_draft(output_batch, tmp_path)
    from tests.test_workflow_capacity import queued_jobs
    other = job_service.claim_job(queued_jobs(1, 'transcript')[0]['id'], 'other-task')
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning',lifespan='off'))
    thread = threading.Thread(target=server.run,daemon=True)
    thread.start()
    deadline = time.monotonic()+10
    while not server.started and time.monotonic()<deadline:
        time.sleep(.05)
    try:
        assert server.started
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=str(chrome),headless=True)
            page = browser.new_page(viewport={'width':width,'height':1000})
            errors, writes = [],[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:writes.append(r.url) if r.method=='POST' else None)
            page.goto(f'http://127.0.0.1:{port}/tasks/{task}/clips',wait_until='networkidle')
            page.locator('#production-review-outputs video').wait_for(state='attached')
            playwright.expect(page.locator('#generate-clips-button')).to_be_enabled()
            assert job_service.get_job(other['id'])['status'] == 'running'
            assert page.locator('input[name="production-delivery"]').count() == 0
            assert page.locator('#production-review-prepare').is_hidden()
            if recut:
                assert '1 条未排期、未执行的旧版草稿将转入历史' in page.locator('#production-review-status').inner_text()
                assert page.locator('#production-review-confirm').is_enabled()
            page.locator('#production-review-confirm').click()
            page.get_by_text('请先逐条检查实际成片，再勾选确认',exact=True).wait_for()
            assert not writes
            page.locator('#production-review-checked').check()
            page.locator('#production-review-confirm').click()
            from app.services.production_review_service import state
            mode = state(task)['configured_delivery_mode']
            page.locator('#production-review-prepare' if mode == 'original' else '#production-review-subtitles').wait_for()
            assert len(writes) == 1 and writes[0].endswith('/confirm')
            with db.get_connection() as c:
                assert c.execute('SELECT count(*) FROM production_reviews').fetchone()[0] == 1 + int(recut)
                assert c.execute('SELECT delivery_mode FROM production_reviews').fetchone()[0] == mode
                assert c.execute('SELECT count(*) FROM publish_jobs').fetchone()[0] == int(recut)
                if recut:
                    assert c.execute('SELECT status FROM publish_jobs').fetchone()[0] == 'CANCELLED'
            if mode == 'original':
                from app.services import publish_service
                monkeypatch.setattr(publish_service, '_generate_default_publish_cover', lambda *_: {})
                page.locator('#production-review-prepare').click()
                page.wait_for_url(f'**/publish?task_id={task}&tab=content')
                with db.get_connection() as c:
                    jobs = c.execute("SELECT status FROM publish_jobs WHERE task_id=? AND status!='CANCELLED'", (task,)).fetchall()
                    assert len(jobs) == 1 and jobs[0][0] == 'WAITING'
                page.goto(f'http://127.0.0.1:{port}/tasks/{task}/clips', wait_until='networkidle')
            with db.get_connection() as c:
                c.execute("UPDATE clip_candidates SET end_time='00:00:04' WHERE id=?",(candidate,))
                c.commit()
            page.locator('#production-review-refresh').click()
            page.get_by_text('分析版本或候选选择已变化，请重新生成成片',exact=True).wait_for()
            assert page.locator('#production-review-confirm').is_disabled()
            assert page.locator('#production-review-prepare').is_hidden()
            assert page.locator('#production-review-subtitles').is_hidden()
            assert page.locator('#production-review-empty').is_visible()
            assert '创建任务时确定' in page.locator('#production-review-policy-note').inner_text()
            page.locator('#production-review').screenshot(path=str(tmp_path/f'production-review-{width}.png'))
            assert not errors
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize('width', [1440, 390])
def test_returning_to_review_restores_cut_without_resubmitting(width, output_batch):
    playwright = pytest.importorskip('playwright.sync_api')
    chrome = Path(os.environ.get('PROGRAMFILES', 'C:/Program Files'))/'Google/Chrome/Application/chrome.exe'
    if not chrome.exists():
        pytest.skip('Chrome unavailable')
    task, _, _ = output_batch
    job = job_service.create_job(task, 'video_cut')
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', lifespan='off'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic()+10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.05)
    try:
        assert server.started
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=str(chrome), headless=True)
            page = browser.new_page(viewport={'width': width, 'height': 1000})
            errors, writes = [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('request', lambda r: writes.append(r.url) if r.method == 'POST' else None)
            url = f'http://127.0.0.1:{port}/tasks/{task}/clips'
            for _ in range(2):
                page.goto(url, wait_until='domcontentloaded')
                playwright.expect(page.locator('#cut-job-message')).to_contain_text('独立切片通道')
                assert page.locator('#generate-clips-button').is_disabled()
                assert page.locator('#cut-job-progress').is_visible()
                page.goto(f'http://127.0.0.1:{port}/tasks', wait_until='domcontentloaded')
            page.goto(url, wait_until='domcontentloaded')
            playwright.expect(page.locator('#cut-job-message')).to_contain_text('独立切片通道')
            job_service.request_job_cancel(job['id'])
            playwright.expect(page.locator('#generate-clips-button')).to_be_enabled(timeout=5000)
            assert not writes and not errors
            with db.get_connection() as c:
                assert c.execute("SELECT count(*) FROM workflow_jobs WHERE task_id=? AND job_type='video_cut'", (task,)).fetchone()[0] == 1
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
