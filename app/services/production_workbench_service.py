"""Read-only projections of existing ledgers; never grants production permission."""
from datetime import datetime

from app.core.config import settings
from app.db.database import get_connection
from app.services.publish_time import app_zone, parse_datetime

CATEGORIES = {
    'start': ('待继续处理', '任务'),
    'clips': ('待审片', '片段'), 'subtitles': ('待字幕', '切片'),
    'prepare': ('待内容准备', '切片'), 'publish': ('发布需复核', '发布任务'),
    'errors': ('异常任务', '任务'),
}

TASK_SUMMARY = """
WITH candidate AS (
 SELECT task_id,count(*) n FROM clip_candidates WHERE is_deleted=0 GROUP BY task_id
), outputs AS (
 SELECT oc.task_id,count(*) n,
 sum(CASE WHEN EXISTS(SELECT 1 FROM subtitle_jobs sj
   JOIN subtitle_tracks st ON st.output_clip_id=oc.id AND st.task_id=oc.task_id
     AND st.is_active=1 AND st.active_revision_id=sj.revision_id
   JOIN subtitle_revisions sr ON sr.id=sj.revision_id AND sr.track_id=st.id AND sr.status='approved'
   WHERE sj.output_clip_id=oc.id AND sj.task_id=oc.task_id AND sj.is_active=1
     AND sj.status='completed' AND sj.validation_status='verified'
     AND COALESCE(sj.output_file_path,'')!='') THEN 0 ELSE 1 END) subtitle_pending,
 sum(CASE WHEN EXISTS(SELECT 1 FROM publish_jobs p WHERE p.output_clip_id=oc.id
     AND p.status!='CANCELLED') THEN 0 ELSE 1 END) unprepared
 FROM output_clip oc WHERE oc.is_active=1 AND oc.status='completed' GROUP BY oc.task_id
), latest_job AS (
 SELECT task_id,max(rowid) latest FROM workflow_jobs GROUP BY task_id
), latest_review AS (
 SELECT task_id,max(rowid) latest FROM production_reviews GROUP BY task_id
)
SELECT t.id,t.task_name title,t.status,t.updated_at,COALESCE(cc.n,0) candidates,
 COALESCE(o.n,0) outputs,COALESCE(o.subtitle_pending,0) subtitle_pending,
 COALESCE(o.unprepared,0) unprepared,b.id batch_item,
 j.status job_status,j.message job_message,j.error_message job_error,
 EXISTS(SELECT 1 FROM workflow_jobs w WHERE w.task_id=t.id AND w.status IN ('queued','running')) busy,
 EXISTS(SELECT 1 FROM workflow_jobs w WHERE w.task_id=t.id AND w.status='running') running,
 r.delivery_mode,CASE WHEN r.id IS NOT NULL AND r.revision=e.revision
   AND EXISTS(SELECT 1 FROM cut_runs cr WHERE cr.id=r.cut_run_id AND cr.task_id=t.id
     AND cr.is_active=1 AND cr.status='completed') THEN 1 ELSE 0 END approved
FROM tasks t LEFT JOIN candidate cc ON cc.task_id=t.id LEFT JOIN outputs o ON o.task_id=t.id
LEFT JOIN material_batch_items b ON b.task_id=t.id
LEFT JOIN latest_job lj ON lj.task_id=t.id LEFT JOIN workflow_jobs j ON j.rowid=lj.latest
LEFT JOIN latest_review lr ON lr.task_id=t.id LEFT JOIN production_reviews r ON r.rowid=lr.latest
LEFT JOIN production_review_epochs e ON e.task_id=t.id
WHERE COALESCE(t.is_deleted,0)=0
"""


def _classify(row):
    """One task issue at a time. Automatic reviewed flags are intentionally ignored."""
    if row['busy']:
        return None
    status = row['status'].lower()
    if status in {'failed', 'completed_with_errors'} or status.startswith('failed_') or row['job_status'] == 'failed':
        return 'errors', 1, row['job_error'] or '处理未完成，请查看任务中的失败步骤和恢复提示', f"/tasks/{row['id']}"
    if row['batch_item']:
        if row['outputs'] and not row['approved']:
            return 'clips', row['outputs'], '查看实际成片并确认；版本变化后需要重新核对', f"/tasks/{row['id']}/clips"
        if row['approved'] and row['delivery_mode'] == 'subtitled' and row['subtitle_pending']:
            return 'subtitles', row['subtitle_pending'], '完成字幕审核与渲染验证', f"/subtitles/{row['id']}"
        if row['approved'] and row['unprepared']:
            return 'prepare', row['unprepared'], '已确认交付方式，请明确进入内容准备', f"/tasks/{row['id']}/clips"
    elif status == 'pending_review' and row['candidates']:
        return 'clips', row['candidates'], '核对 AI 候选和选择；默认保留不代表人工认可', f"/tasks/{row['id']}/clips"
    elif status == 'pending_subtitle_review' and row['outputs']:
        return 'subtitles', row['subtitle_pending'] or row['outputs'], '核对字幕版本和交付选择', f"/subtitles/{row['id']}"
    if status in {'pending_processing', 'pending_ai'}:
        return 'start', 1, '素材已就绪，请在任务详情继续转写或 AI 分析', f"/tasks/{row['id']}"
    return None


def _projection(c):
    rows = [dict(r) for r in c.execute(TASK_SUMMARY)]
    items = []
    for row in rows:
        issue = _classify(row)
        if issue:
            category, count, message, url = issue
            items.append(dict(id='task:'+row['id'], task_id=row['id'], title=row['title'],
                              category=category, count=count, message=message, url=url, updated_at=row['updated_at']))
    for row in c.execute("""SELECT p.id,p.task_id,p.title,p.updated_at,p.status,t.task_name task_title
        FROM publish_jobs p JOIN tasks t ON t.id=p.task_id
        WHERE p.status IN ('NEED_REVIEW','FAILED') AND COALESCE(t.is_deleted,0)=0"""):
        items.append(dict(id='publish:'+row['id'], task_id=row['task_id'], title=row['title'] or row['task_title'],
                          category='publish', count=1, message=('发送失败，请查看失败原因后决定是否重试' if row['status']=='FAILED'
                              else '发送结果或条件需要人工复核，请查看执行记录'),
                          url='/publish', updated_at=row['updated_at']))
    counts = {key: dict(label=label, unit=unit, count=0, tasks=0) for key,(label,unit) in CATEGORIES.items()}
    for key in counts:
        subset = [item for item in items if item['category'] == key]
        counts[key].update(count=sum(i['count'] for i in subset), tasks=len({i['task_id'] for i in subset}))
    items.sort(key=lambda i:(i['updated_at'] or '',i['id']), reverse=True)
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
        rows,_,counts = _projection(c)
        materials = c.execute('SELECT count(*) FROM source_materials').fetchone()[0]
        scheduled, pending = 0,0
        for job in c.execute("""SELECT p.status,p.scheduled_at FROM publish_jobs p JOIN tasks t ON t.id=p.task_id
            WHERE COALESCE(t.is_deleted,0)=0 AND p.status IN ('DRAFT','WAITING','SCHEDULED','PUBLISHING','PUBLISHED')"""):
            pending += job['status'] in {'DRAFT','WAITING'}
            if job['scheduled_at'] and job['status'] in {'SCHEDULED','PUBLISHING','PUBLISHED'}:
                try:
                    scheduled += parse_datetime(job['scheduled_at'],settings.app_timezone).astimezone(zone).date() == today
                except ValueError:
                    pass  # Invalid historical time is not a fabricated schedule.
        works,note = _reviewable(c)
    cards = [dict(label='素材池',value=materials,note='已登记素材',url='/materials'),
             dict(label='处理中',value=sum(r['running'] for r in rows),
                  note=f"另 {sum(bool(r['busy']) and not r['running'] for r in rows)} 个任务排队中 · {counts['start']['count']} 个待继续处理",url='/tasks')]
    for key in ('clips','subtitles'):
        value = counts[key]
        cards.append(dict(label=value['label'],value=value['count'],note=f"{value['unit']} · {value['tasks']} 个任务",url='/review-inbox?category='+key))
    cards.extend([
        dict(label='待发布',value=pending,note=f"发布任务 · 另 {counts['prepare']['count']} 条切片待内容准备",url='/publish'),
        dict(label='今日排期',value=scheduled,note=f'{today} · 上海时间',url='/publish'),
        dict(label='可复盘作品',value=works,note=note,url='/content-review'),
        dict(label='异常任务',value=counts['errors']['count'],note=f"另 {counts['publish']['count']} 个发布任务需复核",url='/review-inbox?category=errors'),
    ])
    return dict(cards=cards,counts=counts)
