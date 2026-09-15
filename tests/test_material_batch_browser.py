import json
import os
from pathlib import Path
import threading
import time

import pytest
import uvicorn

from app.db import database as db
from app.main import app
from tests.test_content_review_browser import _free_port
from tests.test_material_batch import batch_db as _batch_db_fixture, human_db as _human_db_fixture, batch_input

batch_db, human_db = _batch_db_fixture, _human_db_fixture


@pytest.mark.parametrize('width',[1440,390])
def test_batch_confirmation_survives_lost_response_and_reload(width, batch_db, tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    chrome = Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe'
    if not chrome.exists():
        pytest.skip('Chrome unavailable')
    batch_input(tmp_path)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1',port=port,log_level='warning',lifespan='off'))
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
            errors, submitted = [], []
            page.on('pageerror',lambda e:errors.append(str(e)))
            def lose_response(route):
                if route.request.method != 'POST':
                    return route.continue_()
                submitted.append(json.loads(route.request.post_data))
                response = route.fetch()
                assert response.status == 200
                route.abort('failed') if len(submitted) == 1 else route.fulfill(response=response)
            page.route('**/api/material-batches',lose_response)
            page.goto(f'http://127.0.0.1:{port}/materials',wait_until='networkidle')
            assert page.locator('[data-batch-material]').count() == 10
            for checkbox in page.locator('[data-batch-material]').all():
                checkbox.check()
            form = page.locator('#material-batch-form')
            form.locator('[name=selection_profile]').select_option('variety_comedy')
            assert form.locator('[name=ai_prompt_preset_id]').input_value() == 'preset_001'
            assert page.locator('#batch-create').is_disabled()
            form.locator('[name=auto_production]').set_checked(width == 390)
            form.locator('[name=confirmed]').check()
            with db.get_connection() as c:
                assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0
            page.locator('#batch-create').click()
            page.wait_for_function("document.getElementById('batch-status').textContent.includes('请求已保留')")
            saved = page.evaluate("localStorage.getItem('niuma-material-batch-pending-v1')")
            assert json.loads(saved) == submitted[0]
            assert submitted[0]['settings']['auto_production'] == (width == 390)
            page.reload(wait_until='networkidle')
            assert page.locator('#batch-pending').is_visible()
            assert form.locator('[name=selection_profile]').is_disabled()
            assert form.locator('[name=selection_profile]').input_value() == submitted[0]['settings']['selection_profile']
            page.locator('#batch-retry').click()
            page.get_by_text('已创建 10 个任务，等待逐个导入。重复请求已安全核对。',exact=True).wait_for()
            assert len(submitted) == 2 and submitted[0] == submitted[1]
            assert page.locator('[data-batch-id]').count() == 1
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            assert page.evaluate("localStorage.getItem('niuma-material-batch-pending-v1')") is None
            page.locator('#batch-panel').screenshot(path=str(tmp_path/f'batch-panel-{width}.png'))
            assert not errors
            with db.get_connection() as c:
                assert c.execute('SELECT count(*) FROM material_batches').fetchone()[0] == 1
                assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == 10
                assert c.execute('SELECT count(*) FROM workflow_jobs').fetchone()[0] == 10
                assert c.execute('SELECT count(*) FROM publish_jobs').fetchone()[0] == 0
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
