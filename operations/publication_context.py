"""Exact owner, account and successful-publication identity for reviewed repairs."""
from sqlalchemy import select
from common.models.publish_log import PublishLog
from common.models.xy_account import XYAccount
from operations.models import Job


async def load(session,owner,job_id,lock=False):
    query=select(Job).where(Job.id==job_id,Job.owner_id==owner)
    if lock:query=query.with_for_update()
    job=(await session.execute(query)).scalar_one_or_none()
    if not job:raise ValueError('商品任务不存在或不属于当前用户')
    snapshot=(job.parameters or {}).get('publish_snapshot') or {}
    publication=(job.result or {}).get('publication') or {}
    account_id=snapshot.get('account_id');item_id=str(publication.get('item_id') or '')
    if job.status!='published' or publication.get('status')!='published' or not item_id or not account_id or publication.get('account_id')!=account_id:
        raise ValueError('请先核对原发布账号与成功商品 ID，再处理配置')
    query=select(PublishLog).where(PublishLog.user_id==owner,PublishLog.publish_request_id=='ops:'+job.id,
        PublishLog.account_id==account_id,PublishLog.item_id==item_id,PublishLog.status=='success')
    if lock:query=query.with_for_update()
    log=(await session.execute(query)).scalar_one_or_none()
    if not log:raise ValueError('原发布成功记录尚未核对，请先核对发布结果')
    query=select(XYAccount).where(XYAccount.owner_id==owner,XYAccount.account_id==account_id)
    if lock:query=query.with_for_update()
    account=(await session.execute(query)).scalar_one_or_none()
    if not account:raise ValueError('原发布账号已移除，请先核对账号')
    return job,account,publication
