"""Small immutable listing snapshots, captured within the draft mutation transaction."""
import copy
import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from common.db.session import async_session_maker
from common.models.product_material import ProductMaterial
from common.services.pricing_policy import POLICY_FIELDS
from operations.models import Job, ProductRevision
from operations.domain import recommendation

DRAFT_FIELDS = ('variants', 'titles', 'positioning', 'cover_plans', 'faq', 'pending')
MEDIA_FIELDS = ('url', 'name', 'asset_id', 'size', 'width', 'height', 'duration_ms', 'workshop_generated')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def snapshot(job):
    result = job.result or {}
    draft = result.get('draft') or {}
    variants = draft.get('variants') or []
    price = (result.get('price') or {}).get('suggested')
    if not variants or not price or not result.get('material_id'):
        return None
    index = int(job.parameters.get('publish_variant_index', 0))
    if not 0 <= index < len(variants):
        return None
    assets = result.get('assets') or {}
    return {'draft': copy.deepcopy({k: draft[k] for k in DRAFT_FIELDS if k in draft}),
            'price': price, 'variant_index': index,
            'assets': {kind: [{k: item[k] for k in MEDIA_FIELDS if k in item}
                              for item in assets.get(kind, [])] for kind in ('images', 'videos')}}


def context_hash(job):
    result = job.result or {}
    params = job.parameters
    # Store only the digest of supporting facts/documents, never the source bodies.
    return digest([result.get('facts'), (result.get('assets') or {}).get('documents'),
                   {k: params.get(k) for k in ('folder', 'confirmed_facts', 'product_kind')}])


def current_hash(job):
    from operations.store import draft_hash
    return digest([draft_hash(job.result or {}), context_hash(job),
                   {k: job.parameters.get(k) for k in (*POLICY_FIELDS, 'publish_variant_index',
                    'publish_account_id', 'publish_mode', 'publish_at', 'delivery_revision')}])


async def capture(session, job, label, *, force=False):
    """Caller holds the Job lock. No commit here: failures roll history back too."""
    value = snapshot(job)
    if value is None:
        return None
    content = digest(value)
    context = context_hash(job)
    latest = (await session.execute(select(ProductRevision.id, ProductRevision.content_hash,
        ProductRevision.context_hash).where(ProductRevision.owner_id == job.owner_id,
        ProductRevision.job_id == job.id).order_by(ProductRevision.id.desc()).limit(1))).first()
    if not force and latest and latest.content_hash == content and latest.context_hash == context:
        return latest.id
    revision = ProductRevision(owner_id=job.owner_id, job_id=job.id, label=label[:80],
                               content_hash=content, context_hash=context, snapshot=value)
    session.add(revision)
    await session.flush()
    return revision.id


async def owned(session, owner, job_id, *, lock=False):
    query = select(Job).where(Job.id == job_id, Job.owner_id == owner)
    job = (await session.execute(query.with_for_update() if lock else query)).scalar_one_or_none()
    if not job:
        raise ValueError('商品任务不存在或不属于当前用户')
    return job


def preview(value):
    if not value:
        return {'title': '', 'description': '', 'price': None, 'images': [], 'videos': []}
    variant = value['draft']['variants'][value['variant_index']]
    return {'title': variant['title'], 'description': variant['description'], 'price': value['price'],
            'images': [item['url'] for item in value['assets']['images']],
            'videos': value['assets']['videos']}


def changes(before, after):
    left, right = preview(before), preview(after)
    labels = {'title': '标题', 'description': '商品详情', 'price': '售价', 'images': '图片及封面', 'videos': '视频'}
    fields = [label for key, label in labels.items() if left[key] != right[key]]
    if before and after and before['draft'] != after['draft'] and not any(k in fields for k in ('标题', '商品详情')):
        fields.append('其他文案内容')
    return fields


def editable(job):
    if job.status in {'queued', 'running', 'published', 'publish_unknown'} or (job.result or {}).get('publication', {}).get('status') in {'publishing', 'published', 'unknown'}:
        raise ValueError('请在未发布商品准备完成后恢复版本；已发布商品可使用经营优化审核线上修改')
    if job.parameters.get('workflow_hold'):
        raise ValueError('该商品还有连续任务，请先取消剩余步骤再恢复版本')
    if not snapshot(job):
        raise ValueError('请先完成当前商品草稿，再恢复历史版本')


def validate_media(job_id, value, root):
    allowed = (Path(root) / 'uploads' / 'products' / 'operations' / job_id).resolve()
    for kind in ('images', 'videos'):
        for item in value['assets'][kind]:
            url = item.get('url', '')
            if not isinstance(url, str) or not url.startswith('/static/') or '?' in url or '#' in url:
                raise ValueError('历史媒体来源已变化，请重新添加素材')
            raw = Path(root) / url.removeprefix('/static/')
            path = raw.resolve()
            if raw.is_symlink() or not path.is_relative_to(allowed) or not path.is_file():
                raise ValueError('历史图片或视频已缺失，请重新添加素材后审核')


def restored_price(job, value):
    params = {**job.parameters, 'specified_price': value['price']}
    price = recommendation((job.result or {}).get('ranking', {}).get('pricing', {}), params,
                           (job.result or {}).get('ranking', {}).get('candidates', []))
    if not price.get('suggested') or price.get('status') == 'conflict':
        raise ValueError('历史售价与当前价格边界冲突，请先核对成本和最低售价')
    return price


def restore_reason(job, revision, root):
    try:
        editable(job)
        if revision.context_hash != context_hash(job):
            raise ValueError('商品事实或素材来源已变化，请基于当前材料重新生成文案')
        restored_price(job, revision.snapshot)
        validate_media(job.id, revision.snapshot, root)
        if revision.content_hash == digest(snapshot(job)):
            raise ValueError('此版本与当前商品内容一致')
    except ValueError as exc:
        return str(exc)
    return ''


async def records(owner, job_id):
    async with async_session_maker() as session:
        job = await owned(session, owner, job_id)
        # Order IDs first; large JSON columns must stay out of the MySQL filesort.
        ids = (await session.execute(select(ProductRevision.id).where(ProductRevision.owner_id == owner,
            ProductRevision.job_id == job_id).order_by(ProductRevision.id.desc()).limit(100))).scalars().all()
        rows = (await session.execute(select(ProductRevision).where(ProductRevision.id.in_(ids)))).scalars().all() if ids else []
        rows.sort(key=lambda row: row.id, reverse=True)
        current = snapshot(job)
        content, context = digest(current), context_hash(job)
        return {'revisions': [{'id': row.id, 'label': row.label, 'created_at': row.created_at.isoformat()+'Z',
            'title': preview(row.snapshot)['title'], 'price': row.snapshot['price'],
            'cover': (preview(row.snapshot)['images'] or [None])[0],
            'is_current': row.content_hash == content and row.context_hash == context,
            'changes': changes(row.snapshot, current)} for row in rows],
            'current_hash': current_hash(job), 'has_draft': current is not None,
            'note': '最近100条本地商品版本；恢复后重新审核，账号、卡券和排期时间沿用当前设置。'}


async def detail(owner, job_id, ident, root):
    async with async_session_maker() as session:
        job = await owned(session, owner, job_id)
        row = await session.get(ProductRevision, ident)
        if not row or row.owner_id != owner or row.job_id != job_id:
            raise ValueError('商品版本不存在或不属于当前任务')
        reason = restore_reason(job, row, root)
        if not reason:
            material=await session.get(ProductMaterial,(job.result or {}).get('material_id'))
            if not material or material.user_id!=owner or material.is_deleted:
                reason='当前素材库草稿已变化，请重新准备草稿'
        return {'id': row.id, 'label': row.label, 'current_hash': current_hash(job),
                'before': preview(snapshot(job)), 'after': preview(row.snapshot),
                'changes': changes(snapshot(job), row.snapshot), 'can_restore': not reason, 'reason': reason}


async def save(owner, job_id, label='手动保存', expected_hash=None):
    label = str(label).strip() or '手动保存'
    if len(label) > 80:
        raise ValueError('版本名称最多80字')
    async with async_session_maker() as session:
        job = await owned(session, owner, job_id, lock=True)
        if job.status in {'queued', 'running'} or not snapshot(job):
            raise ValueError('请等商品草稿准备完成后保存版本')
        if expected_hash and expected_hash != current_hash(job):
            raise ValueError('商品内容或设置已变化，请刷新版本记录')
        ident = await capture(session, job, label, force=True)
        await session.commit()
        return ident


async def restore(owner, job_id, ident, expected_hash, root):
    from operations import store
    async with async_session_maker() as session:
        job = await owned(session, owner, job_id, lock=True)
        if current_hash(job) != expected_hash:
            raise ValueError('商品内容或设置已变化，请重新查看版本对比后恢复')
        row = await session.get(ProductRevision, ident)
        if not row or row.owner_id != owner or row.job_id != job_id:
            raise ValueError('商品版本不存在或不属于当前任务')
        reason = restore_reason(job, row, root)
        if reason:
            raise ValueError(reason)
        material = await session.get(ProductMaterial, (job.result or {}).get('material_id'), with_for_update=True)
        if not material or material.user_id != owner or material.is_deleted:
            raise ValueError('当前素材库草稿已变化，请重新准备草稿')
        value = copy.deepcopy(row.snapshot)
        price = restored_price(job, value)
        await capture(session, job, '恢复前保留')
        result = copy.deepcopy(job.result)
        result['draft'], result['price'] = value['draft'], price
        result.setdefault('assets', {}).update(value['assets'])
        for key in ('workshop', 'draft_work', 'publication', 'workflow_ready_hash'):
            result.pop(key, None)
        params = copy.deepcopy(job.parameters)
        params.update(specified_price=value['price'], publish_variant_index=value['variant_index'], publish_mode='review')
        for key in ('publish_snapshot', 'publish_requested', 'chat_confirmation', 'prepare_only'):
            params.pop(key, None)
        variant = value['draft']['variants'][value['variant_index']]
        material.title, material.description, material.price = variant['title'], variant['description'], price['suggested']
        material.images, material.videos = [i['url'] for i in value['assets']['images']], value['assets']['videos']
        job.parameters, job.result = params, result
        job.status, job.stage, job.error = 'ready', f'已恢复版本 #{ident}，请重新审核发布', None
        job.lease_token, job.lease_until = None, None
        await capture(session, job, f'恢复版本 #{ident}')
        await session.commit()
        await session.refresh(job)
        return job
