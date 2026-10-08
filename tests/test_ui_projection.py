"""User stages stay consistent without changing production ledgers."""
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.db import database as db
from app.main import app
from app.services import production_review_service as review, production_workbench_service as work
from app.services.task_query_service import get_tasks_page_context, get_subtitle_workflow_context, get_dashboard_context
from app.services.task_service import get_task_live_status
from app.services import job_service
from app.services.ui_projection_service import task_projections
from tests.test_production_review import (
    output_batch as _output_batch, batch_db as _batch_db, human_db as _human_db,
    consent, insert_publish,
)
from tests.test_production_workbench import legacy

output_batch, batch_db, human_db = _output_batch, _batch_db, _human_db


def _subtitle(task, output, tmp_path, *, status='approved', verified='verified'):
    track, revision, job = (uuid4().hex for _ in range(3))
    media = tmp_path/'subtitle-output.mp4'
    media.write_bytes(b'isolated-subtitle-artifact')
    with db.get_connection() as c:
        c.execute("""INSERT INTO subtitle_tracks(id,task_id,track_type,output_clip_id,name,active_revision_id,created_at,updated_at)
            VALUES(?,?,'clip',?,'test',?,'test','test')""", (track,task,output['id'],revision))
        c.execute("""INSERT INTO subtitle_revisions(id,track_id,revision_number,origin,status,checksum,created_at)
            VALUES(?,?,1,'test',?,'test','test')""", (revision,track,status))
        c.execute("""INSERT INTO subtitle_jobs(id,task_id,output_clip_id,style_preset_id,status,revision_id,
            output_file_path,validation_status,created_at,updated_at)
            VALUES(?,?,?,'default','completed',?,?,?,'test','test')""", (job,task,output['id'],revision,str(media),verified))
        c.commit()
    return track, revision, job, media


@pytest.mark.parametrize('output_batch', ['original', 'single-original'], indirect=True)
def test_skip_subtitles_is_excluded_from_every_pending_projection(output_batch):
    task, _, _ = output_batch
    assert task_projections([task])[task]['stage'] == 'review'
    review.confirm(task, consent(task))
    ui = task_projections([task])[task]
    assert ui['subtitle_mode'] == 'original'
    assert ui['subtitle_pending_count'] == 0
    assert ui['stage'] == 'prepare'
    assert work.inbox()['counts']['subtitles']['count'] == 0
    context = get_subtitle_workflow_context()
    assert context['tasks'] == []
    assert {s['label']:s['value'] for s in context['stats']}['待加字幕切片'] == 0


@pytest.mark.parametrize('output_batch', ['review', 'single-review'], indirect=True)
def test_subtitles_only_become_pending_after_current_output_confirmation(output_batch):
    task, _, _ = output_batch
    assert task_projections([task])[task]['subtitle_pending_count'] == 0
    assert get_subtitle_workflow_context()['tasks'] == []
    review.confirm(task, consent(task, 'subtitled'))
    ui = task_projections([task])[task]
    assert ui['stage'] == 'subtitles' and ui['subtitle_pending_count'] == 1
    assert work.inbox()['counts']['subtitles']['count'] == 1
    assert len(get_subtitle_workflow_context()['tasks']) == 1


@pytest.mark.parametrize('output_batch', ['review'], indirect=True)
@pytest.mark.parametrize('status,verified,missing', [('draft','verified',False),('approved','pending',False),('approved','verified',True)])
def test_subtitle_job_completion_is_insufficient_without_current_approval_verification_and_file(
    output_batch, tmp_path, status, verified, missing,
):
    task, _, output = output_batch
    review.confirm(task, consent(task, 'subtitled'))
    _, _, _, media = _subtitle(task, output, tmp_path, status=status, verified=verified)
    if missing:
        media.unlink()
    ui = task_projections([task])[task]
    assert ui['subtitle_pending_count'] == 1 and ui['subtitle_done_count'] == 0
    assert work.inbox()['counts']['subtitles']['count'] == 1


@pytest.mark.parametrize('output_batch', ['review'], indirect=True)
def test_only_current_active_subtitle_revision_counts_as_complete(output_batch, tmp_path):
    task, _, output = output_batch
    review.confirm(task, consent(task, 'subtitled'))
    track, _, _, _ = _subtitle(task, output, tmp_path)
    ui = task_projections([task])[task]
    assert ui['subtitle_pending_count'] == 0 and ui['subtitle_done_count'] == 1
    assert get_subtitle_workflow_context()['tasks'] == []
    new_revision = uuid4().hex
    with db.get_connection() as c:
        c.execute("INSERT INTO subtitle_revisions(id,track_id,revision_number,origin,status,checksum,created_at) VALUES(?,?,2,'test','draft','test','test')", (new_revision,track))
        c.execute('UPDATE subtitle_tracks SET active_revision_id=? WHERE id=?', (new_revision,track))
        c.commit()
    assert task_projections([task])[task]['subtitle_pending_count'] == 1
    assert work.inbox()['counts']['subtitles']['count'] == 1
    assert get_subtitle_workflow_context()['tasks'][0]['subtitle_done_count'] == 0


def test_production_percent_is_separate_from_human_confirmation_and_historical_percent(output_batch):
    task, _, _ = output_batch
    with db.get_connection() as c:
        c.execute("UPDATE tasks SET status='completed',progress=72 WHERE id=?", (task,))
        c.commit()
    ui = get_task_live_status(task)['ui']
    assert ui['production_progress'] == 100 and ui['stage'] == 'review'
    assert ui['workflow_steps'][-1]['state'] == 'current'
    assert not all(step['state'] == 'done' for step in ui['workflow_steps'])
    with db.get_connection() as c:
        assert c.execute('SELECT progress FROM tasks WHERE id=?', (task,)).fetchone()[0] == 72


def test_publish_review_links_target_exact_record(output_batch):
    task, _, output = output_batch
    review.confirm(task, consent(task))
    job = insert_publish(task, output, 'NEED_REVIEW')
    item = work.inbox('publish')['items'][0]
    assert item['url'] == f'/publish?task_id={task}&job_id={job}&tab=history'
    assert task_projections([task])[task]['primary_action']['url'] == item['url']
    assert work.dashboard()['needs_attention']['items'][0]['id'] == 'publish:'+job


def test_recut_and_publish_problems_have_independent_progress(output_batch):
    task, _, output = output_batch
    review.confirm(task, consent(task))
    insert_publish(task, output, 'NEED_REVIEW')
    job = job_service.create_job(task, job_service.JOB_TYPE_VIDEO_CUT)
    with db.get_connection() as c:
        c.execute("UPDATE workflow_jobs SET status='running',progress=41 WHERE id=?", (job['id'],))
        c.execute("UPDATE tasks SET status='completed',progress=72 WHERE id=?", (task,))
        c.commit()
    ui = task_projections([task])[task]
    assert ui['stage'] == 'processing' and ui['production_progress'] == 41
    assert ui['publish_attention_count'] == 1
    assert ui['primary_action']['action'] == 'detail'
    assert ui['workflow_steps'][2]['state'] == 'current'
    assert work.inbox('publish')['total'] == 1


def test_current_week_output_is_not_counted_by_task_creation_date(output_batch):
    task, _, output = output_batch
    review.confirm(task, consent(task))
    job = insert_publish(task, output, 'PUBLISHED')
    old_job = insert_publish(task, output, 'NEED_REVIEW')
    with db.get_connection() as c:
        c.execute("UPDATE tasks SET created_at='2026-09-01T00:00:00+00:00' WHERE id=?", (task,))
        c.execute("UPDATE output_clip SET created_at='2026-10-07T00:00:00+00:00' WHERE id=?", (output['id'],))
        c.execute("UPDATE publish_jobs SET published_at='2026-10-07T01:00:00+00:00' WHERE id=?", (job,))
        c.execute("UPDATE publish_jobs SET platform='bilibili' WHERE id=?", (old_job,))
        c.commit()
    context = get_dashboard_context(now=datetime(2026,10,8,tzinfo=timezone.utc))
    assert context['weekly_summary']['total'] == 0
    assert context['weekly_summary']['produced_clips'] == 1
    assert context['weekly_summary']['published_clips'] == 1
    assert old_job not in str(work.inbox('publish'))


def test_name_id_stage_sort_pagination_and_api_are_real_and_readonly(human_db):
    for i in range(31):
        legacy(f'episode-{i:02d}', 'pending_processing', count=0)
    with db.get_connection() as c:
        c.execute("UPDATE tasks SET task_name='E1873 测试素材',platform='douyin' WHERE id='episode-30'")
        c.commit()
    with sqlite3.connect(human_db) as c:
        before = '\n'.join(c.iterdump())
    assert get_tasks_page_context()['pagination']['total'] == 31
    assert len(get_tasks_page_context(page=1)['tasks']) == 25
    assert len(get_tasks_page_context(page=2)['tasks']) == 6
    assert get_tasks_page_context(page=999)['pagination']['page'] == 2
    assert get_tasks_page_context(q='e1873')['tasks'][0]['id'] == 'episode-30'
    assert len(get_tasks_page_context(q='episode-30', platform='douyin', stage='waiting')['tasks']) == 1
    assert get_tasks_page_context(sort='created_asc')['tasks'][0]['id'] != get_tasks_page_context()['tasks'][0]['id']
    client = TestClient(app)
    headers = {'Authorization':f'Bearer {settings.local_admin_token}'} if settings.local_admin_token else {}
    assert client.get('/api/ui/tasks?q=E1873', headers=headers).json()['pagination']['total'] == 1
    page = client.get('/tasks?q=E1873')
    assert page.status_code == 200 and page.text.count('data-task-row ') == 1
    assert 'return_to=%2Ftasks%3Fq%3DE1873' in page.text
    detail = client.get('/tasks/episode-30?return_to=%2Ftasks%3Fq%3DE1873%26page%3D2')
    assert detail.status_code == 200 and 'href="/tasks?q=E1873&amp;page=2"' in detail.text
    assert 'name="preset_prompt_' in detail.text and 'rows="5" readonly' in detail.text
    for route in ('transcript', 'visual-evidence'):
        auxiliary = client.get(f'/tasks/episode-30/{route}?return_to=%2Ftasks%3Fq%3DE1873%26page%3D2')
        assert auxiliary.status_code == 200
        assert 'href="/tasks/episode-30/clips/review?return_to=%2Ftasks%3Fq%3DE1873%26page%3D2"' in auxiliary.text
    malicious = client.get('/tasks/episode-30?return_to=https%3A%2F%2Fevil.example%2Ftasks')
    assert 'data-return-to-tasks href="/tasks"' in malicious.text
    old = client.get('/review-inbox?category=start&page=2', follow_redirects=False)
    assert old.status_code == 303 and old.headers['location'].endswith('view=inbox&category=start&page=2')
    assert client.get('/tasks?stage=invalid').status_code == 400
    with sqlite3.connect(human_db) as c:
        assert '\n'.join(c.iterdump()) == before
