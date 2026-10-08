"""Exercise task filters and next-step layouts against an isolated fixture DB."""
import os
from pathlib import Path
import threading
import time

import pytest
import uvicorn

from app.db import database as db
from app.main import app
from tests.test_content_review_browser import _free_port
from tests.test_production_review import output_batch as _output_batch, batch_db as _batch_db, human_db as _human_db
from tests.test_production_workbench import legacy

output_batch, batch_db, human_db = _output_batch, _batch_db, _human_db


@pytest.mark.parametrize('width', [1440, 390])
def test_task_filters_paging_return_path_and_readonly_prompt(width, output_batch, tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    chrome = Path(os.environ.get('PROGRAMFILES', 'C:/Program Files'))/'Google/Chrome/Application/chrome.exe'
    if not chrome.exists():
        pytest.skip('Chrome unavailable')
    task, _, _ = output_batch
    for i in range(30):
        legacy(f'filter-task-{i:02d}', 'pending_processing', count=0)
    with db.get_connection() as c:
        c.execute("UPDATE tasks SET task_name='E1873 实测素材',created_at='2099-01-01T00:00:00' WHERE id=?", (task,))
        c.commit()
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
            page = browser.new_page(viewport={'width':width, 'height':1000})
            errors, writes = [], []
            page.on('pageerror', lambda e:errors.append(str(e)))
            page.on('request', lambda r:writes.append(r.url) if r.method not in ('GET', 'HEAD', 'OPTIONS') else None)
            page.goto(f'http://127.0.0.1:{port}/tasks', wait_until='networkidle')
            assert page.locator('[data-task-row]').count() == 25
            page.get_by_role('link', name='下一页', exact=True).click()
            assert page.locator('[data-task-row]').count() == 6
            form = page.locator('.task-filter-form')
            form.locator('[name=q]').fill('E1873')
            form.get_by_role('button', name='筛选', exact=True).click()
            assert page.locator('[data-task-row]').count() == 1
            page.get_by_role('link', name='E1873 实测素材', exact=True).click()
            assert 'return_to=' in page.url
            summary = page.locator('[data-task-live-overview]')
            assert summary.is_visible()
            assert summary.bounding_box()['width'] > (600 if width == 1440 else 280)
            assert page.locator('[data-task-live-progress-number]').inner_text() == '100%'
            assert page.locator('[data-task-live-step="4"]').get_attribute('class').endswith('current')
            page.get_by_text('分析设置与历史', exact=True).click()
            assert page.locator('[name^=preset_prompt_]').first.get_attribute('readonly') is not None
            assert page.locator('[name^=preset_name_]').first.get_attribute('readonly') is not None
            page.locator('[data-return-to-tasks]').click()
            assert 'q=E1873' in page.url and page.locator('[data-task-row]').count() == 1
            form = page.locator('.task-filter-form')
            form.locator('[name=q]').fill(task)
            form.locator('[name=stage]').select_option('review')
            form.get_by_role('button', name='筛选', exact=True).click()
            assert page.locator('[data-task-row]').count() == 1
            assert not errors and not writes
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(tmp_path/f'task-filters-{width}.png'), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
