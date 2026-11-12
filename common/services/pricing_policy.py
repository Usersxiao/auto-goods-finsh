"""Shared sale and negotiation boundaries, calculated with decimal cents."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from sqlalchemy import BigInteger, Column, JSON, MetaData, String, Table, select
from common.models.xy_catalog_item import XYCatalogItem

COST_FIELDS = ('unit_cost', 'support_cost', 'refund_cost', 'acquisition_cost', 'target_contribution')
POLICY_FIELDS = (*COST_FIELDS, 'fee_rate', 'price_min', 'price_max')
# A read-only projection avoids importing the operations application into websocket.
_jobs = Table('xy_operation_jobs', MetaData(), Column('id', String(32)),
              Column('owner_id', BigInteger), Column('parameters', JSON))


def amount(value):
    try:
        if isinstance(value, bool):
            raise ValueError('价格边界格式异常')
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError('价格边界应为有效非负金额')
        return result
    except (InvalidOperation, TypeError):
        raise ValueError('价格边界格式异常') from None


def cost_floor(settings, *, require_complete=True):
    fee = amount(settings.get('fee_rate') or 0)
    if fee >= 1:
        raise ValueError('费率应在0到1之间')
    values = [settings.get(key) for key in COST_FIELDS]
    for value in values:
        if value is not None:
            amount(value)
    if all(value is None for value in values) or (require_complete and any(value is None for value in values)):
        return None
    return (sum(amount(v) for v in values if v is not None) / (1 - fee)).quantize(Decimal('.01'), rounding=ROUND_CEILING)


def minimum_cents(settings):
    floor = cost_floor(settings, require_complete=False)
    low = amount(settings['price_min']) if settings.get('price_min') is not None else Decimal(0)
    high = amount(settings['price_max']) if settings.get('price_max') is not None else None
    minimum = max(low, floor or Decimal(0)).quantize(Decimal('.01'), rounding=ROUND_CEILING)
    if high is not None and minimum > high:
        raise ValueError('成本或最低售价超过最高售价，请核对价格设置')
    return int(minimum * 100)


async def item_policy(session, owner_id, account_pk, item_id):
    item = (await session.execute(select(XYCatalogItem).where(
        XYCatalogItem.owner_id == owner_id, XYCatalogItem.account_pk == account_pk,
        XYCatalogItem.item_id == str(item_id)).limit(1))).scalar_one_or_none()
    if not item:
        return {}
    metadata = item.metadata_json or {}
    saved = metadata.get('pricing_policy') or {}
    if not isinstance(saved, dict):
        raise ValueError('商品价格边界格式异常')
    task_id = metadata.get('operations_task_id')
    if task_id:
        row = (await session.execute(select(_jobs.c.parameters).where(
            _jobs.c.id == str(task_id), _jobs.c.owner_id == owner_id))).first()
        if row:
            return {key: row[0].get(key) for key in POLICY_FIELDS if key in row[0]}
    return saved
