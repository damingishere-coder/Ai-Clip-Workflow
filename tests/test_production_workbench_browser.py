import os
from pathlib import Path
import threading
import time

import pytest
import uvicorn

from app.main import app
from app.services import production_review_service as review
from tests.test_content_review_browser import _free_port
from tests.test_production_review import output_batch as _output_batch, batch_db as _batch_db, human_db as _human_db, consent

output_batch, batch_db, human_db = _output_batch, _batch_db, _human_db


@pytest.mark.parametrize('width',[1440,390])
def test_inbox_navigation_refresh_and_dashboard(width,output_batch,tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    chrome = Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe'
    if not chrome.exists():
        pytest.skip('Chrome unavailable')
    task,_,_ = output_batch
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
            errors,writes = [],[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:writes.append(r.url) if r.method not in ('GET','HEAD','OPTIONS') else None)
            page.goto(f'http://127.0.0.1:{port}/',wait_until='networkidle')
            assert page.locator('.production-card').count() == 8
            page.get_by_role('link',name='打开统一待办',exact=True).click()
            assert page.locator('.production-item').count() == 1
            page.locator('.production-item').get_by_role('link',name='去处理').click()
            page.wait_for_url(f'**/tasks/{task}/clips')
            page.go_back(wait_until='networkidle')
            review.confirm(task,consent(task))  # Isolated service action, not a page side effect.
            page.get_by_role('link',name='刷新待办').click()
            assert page.locator('.production-item').count() == 1
            assert '待内容准备' in page.locator('.production-item').inner_text()
            page.locator('nav[aria-label="待办分类"]').get_by_role('link',name='待审片 0 片段').click()
            assert page.locator('.production-item').count() == 0
            assert page.locator('.production-empty').is_visible()
            assert not errors and not writes
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(tmp_path/f'inbox-{width}.png'),full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
