"""Bound work to visible records and keep synchronous I/O off the ASGI loop."""
import asyncio
import threading
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db.database import get_connection
from app.main import app
from app.routers import pages, publish
from app.services import publish_scheduler as scheduler, publish_service as service
from tests.test_publish_task_grouping import (
    _insert_clip_job, _insert_task, _time, clean_publish_group_data as _clean,
)

clean_publish_group_data = _clean


@pytest.fixture
def archive_with_old_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, 'scheduler_health', lambda: {'worker_available': True})
    task, _ = _insert_task(tmp_path, '大量历史记录之前的排期', created_at=_time(-100))
    scheduled, _, _ = _insert_clip_job(tmp_path, task, platform='douyin', status='SCHEDULED', created_at=_time(-90))
    waiting, _, _ = _insert_clip_job(tmp_path, task, platform='douyin', status='WAITING', created_at=_time(-80))
    with get_connection() as c:
        c.executemany("""INSERT INTO publish_jobs
            (id,task_id,output_clip_id,platform,publish_mode,video_source,video_file_path,title,
             status,created_at,updated_at,provider_response)
            SELECT ?,task_id,output_clip_id,platform,publish_mode,video_source,video_file_path,title,
             'CANCELLED',?,?,? FROM publish_jobs WHERE id=?""",
            [(f'{waiting}-archive-{i}', _time(-i/1000), _time(), '{"access_token":"private-token"}', waiting) for i in range(225)])
        c.commit()
    return task, scheduled, waiting


def test_center_renders_all_active_jobs_without_historical_editors(archive_with_old_schedule, monkeypatch):
    task, scheduled, waiting = archive_with_old_schedule
    normalized = Mock(wraps=service._normalize_job)
    monkeypatch.setattr(service, '_normalize_job', normalized)
    for focus in ('', task):
        normalized.reset_mock()
        context = service.get_publish_center_context(focus_task_id=focus)
        assert {job['id'] for job in context['publish_jobs']} == {scheduled, waiting}
        assert normalized.call_count == 2
    response = TestClient(app).get('/publish', params={'task_id': task})
    assert response.status_code == 200
    assert scheduled in response.text and waiting in response.text
    assert f'{waiting}-archive-' not in response.text
    assert 'preload="metadata"' not in response.text
    assert 'preload="none"' in response.text


def test_history_normalizes_only_requested_page_and_keeps_secrets_private(archive_with_old_schedule, monkeypatch):
    normalized = Mock(wraps=service._normalize_job)
    monkeypatch.setattr(service, '_normalize_job', normalized)
    result = service.list_publish_history_records(platform='douyin', status='CANCELLED', page=2, page_size=10)
    assert result['pagination']['total'] == 225
    assert result['pagination']['page'] == 2
    assert len(result['jobs']) == normalized.call_count == 10
    assert 'private-token' not in str(result)
    assert all(job['history_date'] for job in result['jobs'])
    normalized.reset_mock()
    calendar = service.get_publish_history_calendar('douyin', _time()[:7])
    assert sum(day['counts']['CANCELLED'] for day in calendar['days']) == 225
    assert normalized.call_count == 0


def test_polling_refreshes_old_rows_even_after_terminal_transition(archive_with_old_schedule):
    _, scheduled, _ = archive_with_old_schedule
    client = TestClient(app)
    response = client.get('/api/publish/jobs', params={'job_ids': scheduled})
    assert [job['id'] for job in response.json()['jobs']] == [scheduled]
    with get_connection() as c:
        c.execute("UPDATE publish_jobs SET status='CANCELLED' WHERE id=?", (scheduled,))
        c.commit()
    assert client.get('/api/publish/jobs', params={'job_ids': scheduled}).json()['jobs'][0]['status'] == 'CANCELLED'
    assert client.get('/api/publish/jobs?job_ids=').json()['jobs'] == []
    assert client.get('/api/publish/jobs', params={'job_ids': ','.join(str(i) for i in range(201))}).status_code == 400


@pytest.mark.parametrize('path,target,name', [
    ('/publish', pages, 'get_publish_center_context'),
    ('/api/publish/scheduler/health', publish, 'scheduler_health'),
    ('/api/publish/history/calendar?month=2026-09', service, 'get_publish_history_calendar'),
])
def test_slow_publish_reads_do_not_block_health(path, target, name, monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = getattr(target, name)

    def slow(*args, **kwargs):
        started.set()
        release.wait(3)
        finished.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, slow)

    async def probe():
        transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 1))
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as client:
            request = asyncio.create_task(client.get(path))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                response = await asyncio.wait_for(client.get('/health'), timeout=1)
                assert response.status_code == 200
                assert not finished.is_set(), 'A synchronous publish read blocked the ASGI loop'
            finally:
                release.set()
                await request

    asyncio.run(probe())


def test_scheduler_initializes_once_but_retries_failed_initialization(monkeypatch):
    from app.services import adaptive_schedule
    initialize = Mock(side_effect=[RuntimeError('temporary initialization failure'), None])
    monkeypatch.setattr(scheduler, 'init_db', initialize)
    monkeypatch.setattr(adaptive_schedule, 'process_pending', lambda: None)
    runner = scheduler.PublishScheduler()
    monkeypatch.setattr(runner, 'recover_interrupted_jobs', lambda: 0)
    monkeypatch.setattr(runner, 'list_due_jobs', lambda: [])
    with pytest.raises(RuntimeError, match='initialization failure'):
        runner.run_once()
    for _ in range(3):
        assert runner.run_once()['status'] == 'ok'
    assert initialize.call_count == 2
