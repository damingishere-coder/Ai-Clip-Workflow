from __future__ import annotations

import pytest

from app.db.database import get_connection
from tests.test_publish_center_browser import _seed_job
from tests.test_publish_workspace_browser import raw_job, workspace_page  # noqa: F401

playwright = pytest.importorskip('playwright.sync_api')


@pytest.mark.parametrize('width', [390, 1440])
def test_task_schedule_deeplink_marks_its_rows_and_has_no_preparation_warning(workspace_page, tmp_path, width):  # noqa: F811
    page, ids, errors = workspace_page
    task_id = raw_job(ids[0])['task_id']
    other = _seed_job(tmp_path, 90, task_key='other-focus')
    with get_connection() as connection:
        connection.executemany("UPDATE publish_jobs SET status='SCHEDULED', scheduled_at='2099-01-02T08:00:00+00:00' WHERE id=?", [(id,) for id in ids])
        connection.commit()
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto(f"{page.url.split('?')[0]}?task_id={task_id}&tab=schedule", wait_until='networkidle')
    panel = page.locator('[data-center-panel="schedule"]')
    playwright.expect(panel).to_be_visible()
    banner = panel.locator('[data-schedule-task-focus]')
    playwright.expect(banner).to_contain_text('2 条已排期，0 条待排期')
    assert page.locator('#send-center-message').is_hidden()
    focused = panel.locator('.is-task-focus')
    playwright.expect(focused).to_have_count(2)
    for id in ids:
        row = panel.locator(f'[data-job-id="{id}"]')
        playwright.expect(row.locator('[data-schedule-task-marker]')).to_be_visible()
    other_row = panel.locator(f'[data-job-id="{other}"]')
    assert other_row.is_visible()
    assert 'is-task-focus' not in other_row.get_attribute('class')
    assert page.locator(':focus').get_attribute('data-task-id') == task_id
    assert page.locator('[data-selection-bar]').is_hidden()
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    playwright.expect(focused).to_have_count(2)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    banner.locator('[data-clear-schedule-task-focus]').click()
    playwright.expect(focused).to_have_count(0)
    playwright.expect(banner).to_be_hidden()
    assert 'task_id=' not in page.url
    assert not errors


def test_content_task_with_only_schedules_gets_an_accurate_next_step(workspace_page):  # noqa: F811
    page, ids, errors = workspace_page
    task_id = raw_job(ids[0])['task_id']
    with get_connection() as connection:
        connection.executemany("UPDATE publish_jobs SET status='SCHEDULED', scheduled_at='2099-01-02T08:00:00+00:00' WHERE id=?", [(id,) for id in ids])
        connection.commit()
    page.goto(f"{page.url.split('?')[0]}?task_id={task_id}&tab=content", wait_until='networkidle')
    message = page.locator('#send-center-message')
    playwright.expect(message).to_contain_text('本任务有 2 条已排期记录')
    assert '重新同步' not in message.inner_text()
    page.locator('[data-center-tab="history"]').click()
    assert not errors
