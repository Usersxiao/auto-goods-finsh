"""Local policy projection, sharing the runtime's per-rule item exclusion semantics."""
import json
from sqlalchemy import select
from common.models.xy_delivery_block_rule import XYDeliveryBlockRule
from common.services.delivery_block_rule_meta import get_all_rule_metadata


def excluded(value):
    if not value:return []
    if isinstance(value,str):
        try:value=json.loads(value)
        except (ValueError,TypeError):raise ValueError('发货规则排除商品格式异常，请到账号管理核对')
    if not isinstance(value,list):raise ValueError('发货规则排除商品应为列表，请到账号管理核对')
    return [str(item).strip() for item in value if item is not None and str(item).strip()]


async def rules_for(session,account,lock=False):
    query=select(XYDeliveryBlockRule).where(XYDeliveryBlockRule.account_id==account.account_id).order_by(XYDeliveryBlockRule.id)
    if lock:query=query.with_for_update()
    return (await session.execute(query)).scalars().all()


def project(rules,item_id):
    names={meta['rule_code']:meta['rule_name'] for meta in get_all_rule_metadata()}
    return [{'code':r.rule_code,'name':names.get(r.rule_code,'待核对的发货规则'),'enabled':bool(r.enabled),
             'excluded':str(item_id) in excluded(r.excluded_item_ids),'known':r.rule_code in names,
             'auto_close':bool(r.auto_close_order),'only_card_after_close':bool(r.only_card_after_close)}
            for r in sorted(rules,key=lambda r:(r.priority,r.id))]
