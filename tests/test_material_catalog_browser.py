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
from tests.test_human_review import human_db as _human_db_fixture
from tests.test_material_catalog import source_folder

human_db = _human_db_fixture


@pytest.mark.parametrize('width', [1440,390])
def test_material_preview_confirmation_response_loss_and_refresh(width, human_db, tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    chrome = Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe'
    if not chrome.exists():
        pytest.skip('本机未安装 Chrome')
    folder = source_folder(tmp_path)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', lifespan='off'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic()+10
    while not server.started and time.monotonic()<deadline:
        time.sleep(.05)
    try:
        assert server.started
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=str(chrome), headless=True)
            page = browser.new_page(viewport={'width':width,'height':1000})
            errors, submitted, writes = [], [], []
            page.on('pageerror', lambda err:errors.append(str(err)))
            page.on('request', lambda req:writes.append(req.url) if req.method not in ('GET','HEAD') else None)
            def response_loss(route):
                submitted.append(json.loads(route.request.post_data))
                response = route.fetch()
                if len(submitted) == 1:
                    assert response.status == 200
                    route.abort('failed')  # Commit happened; browser must reuse the request id.
                else:
                    route.fulfill(response=response)
            page.route('**/api/materials/register', response_loss)
            page.goto(f'http://127.0.0.1:{port}/materials',wait_until='networkidle')
            page.get_by_role('button',name='选择文件夹',exact=True).click()
            page.get_by_role('dialog',name='选择本机素材文件夹').wait_for()
            page.get_by_text('本机磁盘',exact=True).wait_for()
            assert page.locator('#material-directory-use').is_disabled()
            page.get_by_label('要打开的文件夹路径',exact=True).fill(str(tmp_path))
            page.get_by_role('button',name='打开路径',exact=True).click()
            page.get_by_role('button',name='打开 local-originals',exact=True).click()
            page.get_by_text('此处没有子文件夹，可以直接选择当前文件夹。',exact=True).wait_for()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(tmp_path/f'material-picker-{width}.png'), full_page=True)
            page.get_by_role('button',name='选择此文件夹',exact=True).click()
            assert not page.locator('#material-directory-dialog').is_visible()
            assert page.locator('#material-directory').input_value() == str(folder)
            page.get_by_role('button',name='扫描预览',exact=True).click()
            page.get_by_text('10 个可登记视频',exact=True).wait_for()
            assert page.locator('#material-register').is_disabled()
            with db.get_connection() as c:
                assert c.execute('SELECT count(*) FROM source_materials').fetchone()[0] == 0
            page.get_by_label('选择全部可登记视频',exact=True).uncheck()
            page.locator('[data-source]').nth(0).check()
            page.locator('[data-source]').nth(1).check()
            page.locator('#material-confirm').check()
            page.locator('#material-register').click()
            page.wait_for_function("document.getElementById('material-status').textContent.includes('重试会核对')")
            page.locator('#material-register').click()
            page.get_by_text('已登记 2 个素材，重复项已复用。外部原片保持原样。',exact=True).wait_for()
            assert len(submitted) == 2 and submitted[0] == submitted[1]
            assert page.locator('[data-material-id]').count() == 2
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(tmp_path/f'material-library-{width}.png'), full_page=True)
            page.reload(wait_until='networkidle')
            assert page.locator('[data-material-id]').count() == 2
            assert not errors
            assert len(writes) == 6 and all('/api/materials/' in url for url in writes)
            with db.get_connection() as c:
                assert c.execute('SELECT count(*) FROM source_materials').fetchone()[0] == 2
                assert c.execute('SELECT count(*) FROM material_registrations').fetchone()[0] == 1
                for table in ('tasks','workflow_jobs','publish_jobs'):
                    assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
            assert len(list(folder.glob('*.mp4'))) == 10
            year = tmp_path/'2011'
            for month in ('01月', '02月'):
                (year/month).mkdir(parents=True)
                (year/month/'同名节目.mp4').write_bytes(b'original')
            page.get_by_label('本机文件夹完整路径',exact=True).fill(str(year))
            page.get_by_role('button',name='扫描预览',exact=True).click()
            page.get_by_text('2 个可登记视频',exact=True).wait_for()
            assert page.locator('#material-preview-list').inner_text().count('同名节目.mp4') == 2
            assert '01月' in page.locator('#material-preview-list').inner_text()
            assert '02月' in page.locator('#material-preview-list').inner_text()
            page.locator('#material-recursive').uncheck()
            assert not page.locator('#material-preview').is_visible()
            assert page.locator('#material-register').is_disabled()
            page.get_by_role('button',name='扫描预览',exact=True).click()
            page.get_by_text('0 个可登记视频',exact=True).wait_for()
            assert '请勾选“包含子文件夹”' in page.locator('#material-status').inner_text()
            page.get_by_role('button',name='选择文件夹',exact=True).click()
            page.get_by_role('button',name='打开 01月',exact=True).wait_for()
            page.get_by_label('要打开的文件夹路径',exact=True).fill(str(year/'missing'))
            page.get_by_role('button',name='打开路径',exact=True).click()
            page.wait_for_function("document.getElementById('material-directory-status').textContent.includes('不可访问')")
            assert page.locator('#material-directory-use').is_disabled()
            page.get_by_role('button',name='关闭文件夹选择',exact=True).click()
            assert page.locator('#material-directory').input_value() == str(year)
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
