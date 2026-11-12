"""Review local account health and narrowly exclude this item from selected rules."""
import copy
import hashlib
import json
from sqlalchemy import or_,select
from common.db.session import async_session_maker
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from operations import ai_connections,automation,delivery_policy,publication_audit
from operations.models import Event
from operations.publication_context import load


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


async def state(session,owner,job_id,lock=False):
    job,account,publication=await load(session,owner,job_id,lock)
    item_id=str(publication['item_id']);rules=await delivery_policy.rules_for(session,account,lock)
    projections=delivery_policy.project(rules,item_id)
    relation_ids=select(CardItemRelation.card_id).where(CardItemRelation.user_id==owner,CardItemRelation.item_id==item_id)
    query=select(Card).where(Card.user_id==owner,or_(Card.id.in_(relation_ids),Card.item_id==item_id)).order_by(Card.id)
    if lock:query=query.with_for_update()
    cards=(await session.execute(query)).scalars().all()
    settings=(account.metadata_json or {}).get('ai_reply_settings') or {}
    snapshot=job.parameters['publish_snapshot'];managed=snapshot.get('automation') or {}
    expected_id=managed.get('delivery_card_id') or (job.result or {}).get('automation',{}).get('delivery_card_id')
    card=cards[0] if len(cards)==1 else None
    version_matches=bool(card and ((managed.get('delivery_text') and card.type=='text' and card.text_content==managed['delivery_text'])
        or (managed.get('card_digest') and (automation.card_digest(card)==managed['card_digest']
            or (managed.get('enable_delivery_card') and automation.card_digest(card,enabled_override=False)==managed['card_digest'])))))
    binding_ready=bool(card and card.id==expected_id and automation.usable(card) and version_matches and account.auto_confirm and not account.only_send_card)
    blockers=[]
    if not publication_audit.account_available(account):blockers.append('请先在原账号管理中恢复启用状态与登录凭据，再重新检查')
    if managed.get('auto_delivery_enabled') is not True:blockers.append('当前商品尚未确认自动发货，请先检查发货卡券')
    if not binding_ready:blockers.append('请先核对唯一可用卡券及账号自动确认发货开关')
    editable=any(row['enabled'] and not row['excluded'] and row['known'] for row in projections)
    guard=digest([publication_audit.identity(job),account.id,account.status,account.cookie,account.auto_confirm,account.only_send_card,
        account.delivery_disabled,account.delivery_disabled_excluded_items,settings,
        [[r.id,r.rule_code,r.enabled,r.priority,r.block_reason,r.auto_close_order,r.only_card_after_close,r.excluded_item_ids,r.config] for r in rules],
        [automation.card_digest(card) for card in cards]])
    return job,account,rules,{'job_id':job.id,'account_id':account.account_id,'account_name':account.display_name or account.account_id,
        'item_id':item_id,'fingerprint':guard,'ready':not blockers and editable,'blockers':blockers,
        'account':{'enabled':account.status=='active','credential_present':bool((account.cookie or '').strip())},
        'reply':{'enabled':settings.get('enabled') is True and settings.get('ai_enabled') is True,
                 'configured':bool(ai_connections.account_config(account)),'model':settings.get('model_name') or ''},
        'delivery':{'binding_ready':binding_ready,'card_count':len(cards),'auto_confirm':bool(account.auto_confirm),
                    'only_send_card':bool(account.only_send_card),'legacy_warning':bool(account.delivery_disabled and not rules)},
        'rules':projections,
        'note':'只将此商品加入所选规则的排除名单。规则开关、参数、其他商品与账号设置保留；其他未排除规则仍按订单条件判断。确认不会补发历史订单，后续订单由现有发货流程处理。',
        'login_note':'这里核对本地启用状态和凭据是否存在；实际登录有效性、连接状态请在账号管理核对。'}


async def preview(owner,job_id):
    async with async_session_maker() as session:
        *_,plan=await state(session,owner,job_id)
        return plan


async def apply(owner,job_id,codes,expected):
    codes=sorted(set(codes))
    if not 1<=len(codes)<=20:raise ValueError('请选择要为此商品排除的具体规则')
    async with async_session_maker() as session:
        job,account,rules,plan=await state(session,owner,job_id,True)
        receipt=(job.result or {}).get('account_rule_receipt') or {}
        if receipt.get('request')==expected and receipt.get('codes')==codes and receipt.get('after')==plan['fingerprint']:
            return {'success':True,'reused':True,'reply':'此商品的所选发货规则已调整，请返回报告重新验收。'}
        if expected!=plan['fingerprint']:raise ValueError('账号、商品、卡券或发货规则已有变化，请重新检查后确认')
        allowed={row['code'] for row in plan['rules'] if row['enabled'] and not row['excluded'] and row['known']}
        if not plan['ready'] or not set(codes)<=allowed:raise ValueError('；'.join(plan['blockers']) or '规则选择已变化，请重新检查')
        for rule in rules:
            if rule.rule_code in codes:
                values=delivery_policy.excluded(rule.excluded_item_ids)
                if len(values)>=500:raise ValueError('该规则的排除商品已达500件，请在账号管理整理后重试')
                rule.excluded_item_ids=values+[plan['item_id']]
        # Legacy compatibility mirrors the credit rule only; never disable a global flag.
        if 'buyer_credit_zero' in codes:
            values=delivery_policy.excluded(account.delivery_disabled_excluded_items)
            if plan['item_id'] not in values:account.delivery_disabled_excluded_items=values+[plan['item_id']]
        await publication_audit.refresh_local_setup(session,job,account)
        *_,after=await state(session,owner,job_id)
        job.result={**job.result,'account_rule_receipt':{'request':expected,'codes':codes,'after':after['fingerprint']}}
        names=[row['name'] for row in plan['rules'] if row['code'] in codes]
        session.add(Event(job_id=job.id,owner_id=owner,kind='assistant',name='已更新此商品发货规则',status='success',
            data={'text':'仅为此商品排除：'+'、'.join(names)+'。其他规则保留，等待重新验收。','outcome':True}))
        await session.commit()
    return {'success':True,'reused':False,'reply':'已仅为此商品排除所选发货规则，请返回报告重新验收。'}
