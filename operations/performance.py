"""Read-only business feedback. Shop windows and lifetime item counters stay separate."""
import asyncio
import math
import re
import uuid
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, case
from common.db.session import async_session_maker
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from common.models.xy_order import XYOrder
from common.models.auto_reply_message_log import XYAutoReplyMessageLog as Log
from operations.models import Job, MetricSnapshot

ZONE = timezone(timedelta(hours=8))
SHOP_FIELDS = {'showPv':'曝光次数','ipv':'商品访问次数','chatUv':'咨询人数',
               'payOrdCnt':'支付笔数','payAmt':'支付金额','rfdOrdCnt':'退款笔数','rfdAmt':'退款金额'}
PAID = {'paid','pending_ship','shipped','completed','待发货','已发货','已完成','交易成功'}
REFUND = {'refunded','退款成功','已退款'}
EXCLUDED = {'self_message','system_message','duplicate_message','auto_delivery_trigger','item_not_belong','manual_reply_ai_paused'}
COVERAGE = ['订单和咨询来自本地已同步记录，零条记录不代表平台零成交或零咨询。',
            '本地订单按下单时间筛选，以当前状态计数；退款按原订单下单区间统计，并非期间退款流水。',
            '商品浏览、想要为详情接口返回的累计数；店铺概览按平台统计区间展示，店铺总量不归入单品。']


def number(value):
    if isinstance(value,bool) or value is None:return None
    value=str(value).strip().replace(',','')
    if not re.fullmatch(r'\d+(?:\.\d+)?',value):return None
    result=float(value)
    return result if math.isfinite(result) and result>=0 else None


def iso(dt):return dt.replace(tzinfo=timezone.utc).isoformat() if dt else None


def shop_fields(response):
    """Use the platform's period totals, never add ratios or daily cumulative values."""
    outer=response.get('data') or {}
    inner=outer.get('data') or {}
    block=inner.get('graphBannerBenchData') or {}
    values={k:None for k in SHOP_FIELDS}
    for entry in block.get('bannerDataList') or []:
        if isinstance(entry,dict) and entry.get('name') in values:
            values[entry['name']]=number(entry.get('dataStr'))
            if values[entry['name']] is None and ('dataStr' not in entry or entry.get('dataStr') not in {'-', '--', '', None}):
                values[entry['name']]=number(entry.get('data'))
    date_range=(outer.get('extendInfo') or {}).get('realDateRange')
    dates=[str(v)[:20] for v in date_range[:2]] if isinstance(date_range,list) else []
    return {'metrics':values,'dates':dates,'available':any(v is not None for v in values.values())}


async def accounts(owner, account_id=None):
    async with async_session_maker() as s:
        query=select(XYAccount).where(XYAccount.owner_id==owner)
        if account_id:query=query.where(XYAccount.account_id==account_id)
        rows=(await s.execute(query.order_by(XYAccount.id))).scalars().all()
    if account_id and not rows:raise ValueError('账号不属于当前用户或已删除')
    return rows


async def catalog(owner, account):
    async with async_session_maker() as s:
        rows=(await s.execute(select(XYCatalogItem.item_id,XYCatalogItem.title,XYCatalogItem.price).where(
            XYCatalogItem.owner_id==owner,XYCatalogItem.account_pk==account.id).order_by(XYCatalogItem.id.desc()).limit(100))).all()
        # Research JSON can be megabytes; sort IDs first to avoid MySQL filesort buffers copying it.
        ids=(await s.execute(select(Job.id).where(Job.owner_id==owner,Job.status=='published').order_by(Job.updated_at.desc()).limit(200))).scalars().all()
        loaded=(await s.execute(select(Job).where(Job.owner_id==owner,Job.id.in_(ids)))).scalars().all() if ids else []
        mapping={j.id:j for j in loaded};jobs=[mapping[i] for i in ids if i in mapping]
    items={r.item_id:{'item_id':r.item_id,'title':r.title or r.item_id,'price':number(r.price)} for r in reversed(rows)}
    for job in reversed(jobs):
        pub=(job.result or {}).get('publication') or {}
        if pub.get('account_id')!=account.account_id or not pub.get('item_id'):continue
        item_id=str(pub['item_id']);variants=(job.result or {}).get('draft',{}).get('variants') or []
        index=int(job.parameters.get('publish_variant_index',0));variant=variants[min(index,len(variants)-1)] if variants else {}
        items[item_id]={**items.get(item_id,{}),'item_id':item_id,'title':variant.get('title') or items.get(item_id,{}).get('title') or job.keyword,
                        'price':(job.result or {}).get('price',{}).get('suggested'),'job_id':job.id}
    return list(reversed(list(items.values())))[:100]


async def local_metrics(owner, account, days):
    end=datetime.now(ZONE).replace(tzinfo=None);start=end-timedelta(days=days)
    async with async_session_maker() as s:
        # Only the newest copy of the same order contributes, even if its status changed.
        latest=select(func.max(XYOrder.id).label('id')).where(XYOrder.owner_id==owner,XYOrder.account_id==account.account_id).group_by(XYOrder.order_no).subquery()
        paid=XYOrder.status.in_(PAID);refund=XYOrder.status.in_(REFUND)
        rows=(await s.execute(select(XYOrder.item_id,func.count().label('recorded_orders'),
            func.sum(case((paid,1),else_=0)).label('paid_orders'),
            func.sum(case((paid & (XYOrder.currency=='CNY'),XYOrder.amount),else_=0)).label('paid_amount'),
            func.sum(case((paid & ((XYOrder.amount.is_(None)) | (func.coalesce(XYOrder.currency,'')!='CNY')),1),else_=0)).label('amount_missing'),
            func.sum(case((refund,1),else_=0)).label('refund_orders'))
            .join(latest,XYOrder.id==latest.c.id).where(XYOrder.owner_id==owner,XYOrder.account_id==account.account_id,
            func.coalesce(XYOrder.placed_at,XYOrder.created_at)>=start,func.coalesce(XYOrder.placed_at,XYOrder.created_at)<end)
            .group_by(XYOrder.item_id))).mappings().all()
        logs=(await s.execute(select(Log.item_id,
            func.count(func.distinct(case((Log.reply_strategy!='auto_delivery',Log.chat_id)))).label('consultations'),
            func.sum(case(((Log.reply_strategy!='auto_delivery') & Log.send_status.in_(['failed','timeout','unknown']) & (Log.reply_strategy!='none'),1),else_=0)).label('reply_failures'),
            func.sum(case(((Log.reply_strategy=='auto_delivery') & Log.send_status.in_(['failed','timeout','unknown']),1),else_=0)).label('delivery_failures'))
            .where(Log.owner_id==owner,Log.account_id==account.account_id,Log.account_pk==account.id,
            Log.created_at>=start,Log.created_at<end,func.coalesce(Log.decision_reason,'').not_in(EXCLUDED)).group_by(Log.item_id))).mappings().all()
        sync=(await s.execute(select(func.max(XYOrder.synced_at)).where(XYOrder.owner_id==owner,XYOrder.account_id==account.account_id))).scalar()
        conversations=await s.scalar(select(func.count(func.distinct(Log.chat_id))).where(Log.owner_id==owner,
            Log.account_id==account.account_id,Log.account_pk==account.id,Log.created_at>=start,Log.created_at<end,
            func.coalesce(Log.decision_reason,'').not_in(EXCLUDED),Log.reply_strategy!='auto_delivery'))
    values={}
    for row in [*rows,*logs]:
        key=str(row['item_id'] or '')
        values.setdefault(key,{})
        for k,v in row.items():
            if k!='item_id':values[key][k]=float(v or 0) if k=='paid_amount' else int(v or 0)
    empty={'recorded_orders':0,'paid_orders':0,'paid_amount':0,'amount_missing':0,'refund_orders':0,'consultations':0,'reply_failures':0,'delivery_failures':0}
    totals={k:round(sum(v.get(k,0) for v in values.values()),2) for k in empty};totals['consultations']=int(conversations or 0)
    return {'start':start.replace(tzinfo=ZONE).isoformat(),'end':end.replace(tzinfo=ZONE).isoformat(),
            'last_order_sync':sync.replace(tzinfo=ZONE).isoformat() if sync else None,
            'items':{k:{**empty,**v} for k,v in values.items()},'totals':totals}


async def save_snapshot(owner,account,item_id,days,data):
    async with async_session_maker() as s:
        row=MetricSnapshot(id=f'{time.time_ns():016x}'+uuid.uuid4().hex[:16],owner_id=owner,account_id=account,item_id=item_id,days=days,data=data)
        s.add(row);await s.commit();await s.refresh(row)
    return row


async def refresh(owner, account, days, items, item_id=None):
    from app.services.data_analysis_service import fetch_seller_summary
    from common.services.xianyu_detail_client import XianyuItemDetailClient
    from operations.collector import detail_fields
    if account.status!='active' or not account.cookie:raise ValueError('账号需要重新登录后刷新；已保存数据仍可查看')
    if item_id and item_id not in {i['item_id'] for i in items}:raise ValueError('请指定该账号下已同步或已发布的商品ID')
    async def shop():
        try:
            res=await asyncio.wait_for(fetch_seller_summary(account.cookie,f'recent{days}d',retry_count=2),timeout=23)
            data=shop_fields(res) if res.get('success') else {'available':False,'metrics':{k:None for k in SHOP_FIELDS},'dates':[]}
            if not data['available']:data['message']='店铺概览本次未取得有效字段，请检查账号登录或平台数据权限。'
        except Exception:
            data={'available':False,'metrics':{k:None for k in SHOP_FIELDS},'dates':[],'message':'店铺概览请求超时或接口暂未返回数据，可稍后刷新。'}
        await save_snapshot(owner,account.account_id,'',days,data)
    async def details():
        client=XianyuItemDetailClient(account.account_id,account.cookie,owner_id=owner)
        selected=[i for i in items if i['item_id']==item_id] if item_id else items[:3]
        for pos,item in enumerate(selected):
            if pos:await asyncio.sleep(1)
            try:
                res=await asyncio.wait_for(client.get_detail(item['item_id']),timeout=8)
                if not res.get('success'):break
                fields=detail_fields(res.get('detail') or {})
                await save_snapshot(owner,account.account_id,item['item_id'],0,{k:fields.get(k) for k in ['view_count','want_count']})
            except Exception:break
    await asyncio.gather(shop(),details())


async def snapshots(owner, account, days):
    async with async_session_maker() as s:
        latest=select(func.max(MetricSnapshot.id).label('id'),MetricSnapshot.item_id).where(
            MetricSnapshot.owner_id==owner,MetricSnapshot.account_id==account,
            (MetricSnapshot.item_id!='') | (MetricSnapshot.days==days)).group_by(MetricSnapshot.item_id).subquery()
        rows=(await s.execute(select(MetricSnapshot).join(latest,MetricSnapshot.id==latest.c.id).where(
            MetricSnapshot.owner_id==owner,MetricSnapshot.account_id==account,
            (MetricSnapshot.item_id!='') | (MetricSnapshot.days==days)))).scalars().all()
    return {r.item_id:r for r in rows}


async def baseline(owner,account,item,latest,days):
    async with async_session_maker() as s:
        row=(await s.execute(select(MetricSnapshot).where(MetricSnapshot.owner_id==owner,MetricSnapshot.account_id==account,
            MetricSnapshot.item_id==item,MetricSnapshot.created_at<=latest.created_at-timedelta(days=days)).order_by(MetricSnapshot.created_at.desc()).limit(1))).scalar_one_or_none()
    if not row:return None
    delta={}
    for field in ['view_count','want_count']:
        a=number(row.data.get(field));b=number(latest.data.get(field))
        delta[field]=b-a if a is not None and b is not None and b>=a else None
    return {'start':iso(row.created_at),'end':iso(latest.created_at),'values':delta,
            'note':'实际两次采样之差，时间跨度见起止日期；计数回退时增量留空。'}


def diagnose(report):
    findings=[];shop=report['shop'].get('metrics') or {}
    views=shop.get('ipv');exposures=shop.get('showPv')
    if exposures is not None and views is not None and exposures>0:
        findings.append({'scope':'店铺','observation':f'平台曝光 {exposures:g} 次，商品访问 {views:g} 次，访问/曝光比 {views/exposures:.1%}。',
                         'suggestion':'可测试标题关键词和封面，单次只调整一个因素，再比较后续同长度区间。此比值不是成交率。'})
    for item in report['items']:
        local=item['local']
        if local.get('reply_failures') or local.get('delivery_failures'):
            findings.append({'scope':item['title'],'item_id':item['item_id'],'observation':f'本地回复异常 {local.get("reply_failures",0)} 条，发货异常 {local.get("delivery_failures",0)} 条。',
                             'suggestion':'先检查账号在线状态、AI接口和发货配置；消息记录可能包含重复尝试，请按记录核对。'})
        elif local.get('consultations',0)>0 and local.get('paid_orders',0)==0:
            findings.append({'scope':item['title'],'item_id':item['item_id'],'observation':f'本地记录 {local["consultations"]} 个咨询会话、0 笔已付款订单。',
                             'suggestion':'可先把交付内容、适用场景和领取步骤写清楚；订单同步覆盖和成交原因仍待核实。'})
    if not findings:findings.append({'scope':'当前数据','observation':'当前证据不足以判断销售瓶颈。','suggestion':'先同步订单、刷新经营数据并积累同口径快照，再比较调整前后表现。'})
    return findings[:8]


async def report(owner,account_id,days=7,do_refresh=False,item_id=None):
    if days not in {1,7,30}:raise ValueError('统计区间支持近1天、7天或30天')
    account=(await accounts(owner,account_id))[0]
    items=await catalog(owner,account)
    if do_refresh:await refresh(owner,account,days,items,item_id)
    saved=await snapshots(owner,account.account_id,days);local=await local_metrics(owner,account,days)
    shop=saved.get('');empty={'recorded_orders':0,'paid_orders':0,'paid_amount':0,'amount_missing':0,'refund_orders':0,'consultations':0,'reply_failures':0,'delivery_failures':0}
    for item in items:
        snap=saved.get(item['item_id']);item['local']={**empty,**local['items'].get(item['item_id'],{})}
        item['counters']={**({'view_count':None,'want_count':None} if not snap else snap.data),'sampled_at':iso(snap.created_at) if snap else None}
        item['delta']=await baseline(owner,account.account_id,item['item_id'],snap,days) if snap else None
    result={'account_id':account.account_id,'account_name':account.display_name or account.account_id,'days':days,
            'generated_at':datetime.now(timezone.utc).isoformat(),'local':{k:v for k,v in local.items() if k!='items'},
            'shop':{**(shop.data if shop else {'available':False,'metrics':{k:None for k in SHOP_FIELDS},'dates':[],'message':'尚无此区间快照，说“刷新经营数据”读取平台。'}),
                    'sampled_at':iso(shop.created_at) if shop else None,'snapshot_id':shop.id if shop else None},
            'items':items,'coverage':COVERAGE+['商品目录最多展示100件；一次刷新更新最新3件商品计数，也可指定商品ID刷新。目录之外的订单仍计入账号本地总计。']}
    result['findings']=diagnose(result)
    return result


def summary(reports):
    lines=[]
    for r in reports:
        totals=r['local']['totals'];metrics=r['shop']['metrics']
        fmt=lambda k:'未取得' if metrics.get(k) is None else f'{metrics[k]:g}'
        lines.append(f'「{r["account_name"]}」近{r["days"]}天\n店铺：曝光 {fmt("showPv")} · 访问 {fmt("ipv")} · 支付 {fmt("payOrdCnt")} 笔\n本地已同步：付款 {totals["paid_orders"]:g} 笔 · 已知金额 ¥{totals["paid_amount"]:g} · 咨询记录 {totals["consultations"]:g} 个会话。')
        lines.extend(v['observation']+' '+v['suggestion'] for v in r['findings'][:2])
    return '\n\n'.join(lines)+'\n\n数据来源、采样时间和单品表现已整理在经营反馈中。可以继续说“优化当前商品”或指定商品ID。'
