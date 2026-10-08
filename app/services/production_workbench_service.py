"""Read-only projections of existing ledgers; never grants production permission."""
from datetime import datetime

from app.core.config import settings
from app.db.database import get_connection
from app.services.publish_time import app_zone, parse_datetime
from app.services.ui_projection_service import load_task_rows, task_projections

CATEGORIES = {
    'start': ('待继续处理', '任务'),
    'clips': ('待审片', '片段'), 'subtitles': ('待字幕', '切片'),
    'prepare': ('待内容准备', '切片'), 'publish': ('发布需复核', '发布任务'),
    'errors': ('异常任务', '任务'),
}

def _projection(c):
    rows = load_task_rows(c)
    projections = task_projections(connection=c, rows=rows)
    items = []
    for row in rows:
        ui = projections[row['id']]
        row.update(ui=ui, running=ui['running'], busy=ui['busy'])
        if ui['category']:
            category, count, message, url = ui['category'], ui['count'], ui['message'], ui['primary_action']['url']
            items.append(dict(id='task:'+row['id'], task_id=row['id'], title=row['title'],
                              category=category, count=count, unit=CATEGORIES[category][1],
                              message=message, url=url, updated_at=row['updated_at']))
    for row in c.execute("""SELECT p.id,p.task_id,p.title,p.updated_at,p.status,t.task_name task_title
        FROM publish_jobs p JOIN tasks t ON t.id=p.task_id
        WHERE p.status IN ('NEED_REVIEW','FAILED') AND p.platform='douyin' AND COALESCE(t.is_deleted,0)=0"""):
        items.append(dict(id='publish:'+row['id'], task_id=row['task_id'], title=row['title'] or row['task_title'],
                          category='publish', count=1, message=('发送失败，请查看失败原因后决定是否重试' if row['status']=='FAILED'
                              else '发送结果或条件需要人工复核，请查看执行记录'),
                          unit='发布任务', url=f"/publish?task_id={row['task_id']}&job_id={row['id']}&tab=history", updated_at=row['updated_at']))
    counts = {key: dict(label=label, unit=unit, count=0, tasks=0) for key,(label,unit) in CATEGORIES.items()}
    for key in counts:
        subset = [item for item in items if item['category'] == key]
        counts[key].update(count=sum(i['count'] for i in subset), tasks=len({i['task_id'] for i in subset}))
    priority = {'publish': 0, 'errors': 1, 'clips': 2, 'subtitles': 3, 'prepare': 4, 'start': 5}
    items.sort(key=lambda i:(i['updated_at'] or '',i['id']), reverse=True)
    items.sort(key=lambda i:priority[i['category']])
    return rows, items, counts


def inbox(category='all', page=1, page_size=20):
    if category not in {'all', *CATEGORIES} or page < 1 or not 1 <= page_size <= 100:
        raise ValueError('待办分类或分页参数无效')
    with get_connection() as c:
        c.execute('BEGIN')
        _, items, counts = _projection(c)
    subset = [i for i in items if category == 'all' or i['category'] == category]
    pages = max(1,(len(subset)+page_size-1)//page_size)
    page = min(page,pages)
    return dict(items=subset[(page-1)*page_size:page*page_size], counts=counts,
                category=category,page=page,pages=pages,total=len(subset))


def _reviewable(c):
    from app.services import content_review_service as review
    count, has_data = 0, False
    for account in c.execute("SELECT id FROM publish_accounts WHERE platform='douyin'").fetchall():
        rows = review._latest_diagnosis_rows(c,account['id'])
        has_data = has_data or bool(rows)
        count += sum(bool(w.get('publish_job_id')) and w.get('match_status') in review.MATCHED_STATUSES
                     and all(w.get(k) is not None for k in review.DIAGNOSIS_CORE_METRICS) for w in rows)
    note = '按账号去重 · 已归因且核心指标齐全' if count else (
        '暂无同时满足归因与指标条件的作品' if has_data else '暂无已确认官方作品数据')
    return count,note


def dashboard(*, now=None):
    zone = app_zone(settings.app_timezone)
    now = now or datetime.now(zone)
    today = (now.replace(tzinfo=zone) if now.tzinfo is None else now.astimezone(zone)).date()
    with get_connection() as c:
        c.execute('BEGIN')
        rows,items,counts = _projection(c)
        materials = c.execute('SELECT count(*) FROM source_materials').fetchone()[0]
        scheduled, pending, all_scheduled = 0,0,0
        for job in c.execute("""SELECT p.status,p.scheduled_at FROM publish_jobs p JOIN tasks t ON t.id=p.task_id
            JOIN output_clip o ON o.id=p.output_clip_id AND o.task_id=p.task_id
            WHERE COALESCE(t.is_deleted,0)=0 AND o.is_active=1 AND p.platform='douyin'
            AND p.status IN ('DRAFT','WAITING','SCHEDULED','PUBLISHING','PUBLISHED')"""):
            pending += job['status'] in {'DRAFT','WAITING'}
            all_scheduled += job['status'] == 'SCHEDULED'
            if job['scheduled_at'] and job['status'] in {'SCHEDULED','PUBLISHING','PUBLISHED'}:
                try:
                    scheduled += parse_datetime(job['scheduled_at'],settings.app_timezone).astimezone(zone).date() == today
                except ValueError:
                    pass  # Invalid historical time is not a fabricated schedule.
        works,note = _reviewable(c)
    cards = [dict(key='total_tasks',label='制作任务',value=len(rows),note='个有效制作任务',url='/tasks'),
             dict(key='materials',label='素材池',value=materials,note='已登记素材',url='/materials'),
             dict(key='processing',label='制作中',value=sum(r['busy'] for r in rows),
                  note=f"另 {sum(bool(r['busy']) and not r['running'] for r in rows)} 个任务排队中 · {counts['start']['count']} 个待继续处理",url='/tasks')]
    for key in ('clips','subtitles'):
        value = counts[key]
        cards.append(dict(key='clip_review' if key == 'clips' else 'subtitle_review',label=value['label'],value=value['count'],note=f"{value['unit']} · {value['tasks']} 个任务",url='/materials?view=inbox&category='+key))
    cards.extend([
        dict(key='publish_pending',label='待安排发布',value=pending,note=f"条发布记录 · 另 {counts['prepare']['count']} 条切片待内容准备",url='/publish?tab=content'),
        dict(key='scheduled',label='已排期',value=all_scheduled,note='条发布记录 · 抖音',url='/publish?tab=schedule'),
        dict(key='today_schedule',label='今日排期',value=scheduled,note=f'{today} · 上海时间',url='/publish'),
        dict(key='reviewable',label='可复盘作品',value=works,note=note,url='/content-review'),
        dict(key='production_error',label='制作异常',value=counts['errors']['count'],note='个制作任务',url='/materials?view=inbox&category=errors'),
        dict(key='publish_attention',label='发布需处理',value=counts['publish']['count'],note='条发布记录 · 失败或需复核',url='/materials?view=inbox&category=publish'),
    ])
    return dict(cards=cards,counts=counts,
                needs_attention=dict(total=len(items), items=items[:8], counts=counts))
