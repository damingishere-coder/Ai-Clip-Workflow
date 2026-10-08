from __future__ import annotations

from pathlib import Path

import pytest

from app.db.database import get_connection
from tests.test_publish_center_browser import _seed_job
from tests.test_publish_workspace_browser import raw_job, workspace_page  # noqa: F401

playwright = pytest.importorskip('playwright.sync_api')


def assert_no_horizontal_scroll(page):
    dimensions = page.evaluate("""() => ({
        viewport: innerWidth, document: document.documentElement.scrollWidth,
        body: document.body.scrollWidth, x: scrollX,
        overflow: Array.from(document.querySelectorAll('[data-center-panel]:not([hidden]) *'))
          .filter(el => el.getBoundingClientRect().width && el.getBoundingClientRect().right + scrollX > innerWidth + 1)
          .slice(0, 8).map(el => [el.className, el.getBoundingClientRect().right + scrollX])
    })""")
    assert dimensions['document'] <= dimensions['viewport'], dimensions
    assert dimensions['body'] <= dimensions['viewport'], dimensions
    assert dimensions['x'] == 0, dimensions


@pytest.mark.parametrize('width', [390, 768, 1024, 1280, 1440])
def test_dense_task_plan_fits_viewport_after_auto_focus(workspace_page, tmp_path, width):  # noqa: F811
    page, ids, errors = workspace_page
    task_id = raw_job(ids[0])['task_id']
    ids += [_seed_job(tmp_path, index, task_key='workspace') for index in range(2, 12)]
    with get_connection() as connection:
        connection.execute("UPDATE tasks SET task_name=? WHERE id=?", ('康熙来了：同一任务十二条片段，核对排期与标题的长名称', task_id))
        connection.executemany(
            "UPDATE publish_jobs SET status='SCHEDULED', title=?, scheduled_at='2099-01-02T08:00:00+00:00' WHERE id=?",
            [('长标题里的嘉宾回答与主持人互动需要保留完整信息和日期排期关系', id) for id in ids],
        )
        connection.commit()
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto(f"{page.url.split('?')[0]}?task_id={task_id}&tab=schedule", wait_until='networkidle')
    panel = page.locator('[data-center-panel="schedule"]')
    playwright.expect(panel.locator('.is-task-focus')).to_have_count(12)
    first = panel.locator('.is-task-focus').first
    playwright.expect(first).to_be_in_viewport()
    assert_no_horizontal_scroll(page)
    if width <= 760:
        title = first.locator('[data-row-title]')
        title_layout = title.evaluate("""el => ({
            wrap: getComputedStyle(el).whiteSpace,
            width: el.getBoundingClientRect().width,
            parentWidth: el.parentElement.getBoundingClientRect().width,
            height: el.getBoundingClientRect().height,
            clipped: el.scrollHeight > el.clientHeight || el.scrollWidth > el.clientWidth
        })""")
        assert title_layout['wrap'] != 'nowrap', title_layout
        assert abs(title_layout['width'] - title_layout['parentWidth']) <= 1, title_layout
        assert title_layout['height'] > 24, title_layout
        assert not title_layout['clipped'], title_layout
    cancel = first.locator('[data-cancel-job]')
    assert 'secondary-button' in cancel.get_attribute('class')
    assert cancel.evaluate('el => getComputedStyle(el).borderRadius') != '0px'
    screenshots = Path(__file__).resolve().parents[1] / 'data' / 'acceptance' / 'ui-refactor'
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / f'publish-task-plan-{width}.png'))
    first.locator('[data-publish-select]').check()
    playwright.expect(page.locator('[data-selection-bar]')).to_be_visible()
    assert_no_horizontal_scroll(page)
    page.locator('[data-clear-selection]').click()
    page.locator('[data-schedule-view="calendar"]').click()
    assert_no_horizontal_scroll(page)
    page.locator('[data-center-tab="history"]').click()
    assert_no_horizontal_scroll(page)
    page.locator('[data-center-tab="content"]').click()
    assert_no_horizontal_scroll(page)
    assert not errors
