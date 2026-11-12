"""Small, read-only context for finding the right seller conversation."""
from datetime import datetime, timezone


REVIEWS = {
    'publish': '审核商品发布', 'sync': '审核多账号同步',
    'schedule_batch': '审核批量排期', 'schedule_adjustment': '审核排期调整',
    'repair': '审核重新准备', 'revision': '审核版本恢复',
    'optimization': '审核文案优化', 'delivery': '审核发货卡券换绑',
}


def summary(job):
    """Allowlist fields; never include snapshots, delivery text or model settings."""
    params = job.parameters or {}
    result = job.result or {}
    publication = result.get('publication') or {}
    locked = publication.get('status') in {'published', 'publishing', 'unknown'}
    snapshot = params.get('publish_snapshot') or {}
    account = ((publication.get('account_id') or snapshot.get('account_id')) if locked else None) or params.get('publish_account_id')
    variants = (result.get('draft') or {}).get('variants') or []
    index = params.get('publish_variant_index', 0)
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 0
    if not 0 <= index < len(variants):
        index = 0
    title = (snapshot.get('item_data') or {}).get('title') if locked else None
    if not title and variants and isinstance(variants[index], dict):
        title = variants[index].get('title')
    title = title if isinstance(title, str) else None

    status = job.status
    attention = False
    if status in {'queued', 'running'}:
        step = '正在提交发布' if params.get('publish_requested') else '正在准备商品'
        if status == 'queued':
            step = '等待提交发布' if params.get('publish_requested') else '等待执行任务'
    elif status == 'publish_unknown' or publication.get('status') == 'unknown':
        step, attention = '核对闲鱼商品与发布记录', True
    elif status == 'cancelled':
        step = '进度已保留，可继续任务'
    else:
        pending = params.get('chat_confirmation') or {}
        if pending.get('kind') in REVIEWS:
            try:
                expires = datetime.fromisoformat(pending['expires'])
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                expired = expires <= datetime.now(timezone.utc)
            except (KeyError, TypeError, ValueError):
                expired = True
            step = '审核单已过期，重新生成' if expired else REVIEWS[pending['kind']]
            attention = True
        elif status == 'published' or publication.get('status') == 'published':
            attention = (result.get('automation') or {}).get('status') == 'needs_setup'
            step = '完善AI回复与发货托管' if attention else '查看线上商品与经营反馈'
        elif status == 'scheduled':
            step = '已确认排期，等待自动发布'
        elif status == 'schedule_missed':
            step, attention = '重新设置并审核发布时间', True
        elif status == 'waiting_assets':
            step, attention = '发送素材文件夹路径', True
        elif status == 'failed':
            step, attention = '检查原因后从断点继续', True
        elif status in {'publish_blocked', 'publish_failed'}:
            step, attention = '检查发布条件后重新审核', True
        elif params.get('workflow_hold'):
            step = '查看整条任务进度'
        elif variants:
            step, attention = '审核商品内容与发布设置', True
        else:
            step = '继续交代商品要求'
    return {
        'account_id': str(account)[:80] if account else None,
        'title': title[:100] if title else None,
        'publish_mode': 'automatic' if params.get('publish_mode') == 'automatic' else 'review',
        'next_step': step,
        'needs_attention': attention,
    }
