"""Explainable local listing checks, with reviewed copy improvements."""
import asyncio
import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, UnidentifiedImageError
from common.db.session import async_session_maker
from common.models.card import Card
from operations import optimization, publishing, revisions, store

PLACEHOLDERS = re.compile(r'\{\{[^{}\n]{1,80}\}\}|\b(?:TODO|TBD|XXX)\b|【(?:待填写|待补充|填写|商品名|价格|链接)[^】\n]{0,40}】|(?:待填写|待补充)(?:价格|链接|标题|内容)',re.I)
EDITABLE = {'ready', 'publish_blocked', 'publish_failed', 'cancelled', 'schedule_missed', 'scheduled'}


def clean(value):
    text=re.sub(r'https?://\S+', '[地址已省略]',str(value))
    text=re.sub(r'(?i)(api[_ -]?key|token|cookie|提取码|密码)\s*[:：=]\s*[^\s，,；;]+',r'\1：[已隐藏]',text)
    return re.sub(r'\bsk-[A-Za-z0-9_-]{8,}\b','[已隐藏]',text)[:400]


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def working(job,index=None):
    variants=(job.result or {}).get('draft',{}).get('variants') or []
    index=int(job.parameters.get('publish_variant_index',0)) if index is None else index
    if not isinstance(index,int) or isinstance(index,bool) or index<0 or (variants and index>=len(variants)) or index>2:
        raise ValueError('请选择当前商品的有效文案版本')
    return index,variants[index] if variants else {}


def private_overlap(text,private):
    links=re.findall(r'https?://[^\s<>"「」]+',private or '')
    if any(link.rstrip('，。；,;）)') in text for link in links):return True
    codes=re.findall(r'(?:提取码|解压密码|访问密码)\s*[:：=]?\s*([A-Za-z0-9_-]{4,40})',private or '')
    return any(re.search(r'(?:提取码|解压密码|访问密码)\s*[:：=]?\s*'+re.escape(code)+r'(?![A-Za-z0-9_-])',text) for code in codes)


def media_checks(job,images,videos,root):
    checks=[];stamps=[]
    def add(ident,state,title,detail,action=None):
        checks.append({'id':ident,'group':'媒体','state':state,'title':title,'detail':detail,'action':action})
    if not images:add('images','attention','商品图片','尚未选择图片，请添加封面和详情图。','workshop')
    elif len(images)>9:add('images','attention','商品图片',f'当前有 {len(images)} 张图片，发布需控制在9张以内。','workshop')
    else:add('images','pass','商品图片',f'已选择 {len(images)} 张图片，第一张作为封面。')
    if len(set(images))<len(images):add('duplicate_images','attention','重复图片','存在重复图片，可调整图片顺序并移除重复内容。','workshop')
    missing=remote=0
    for url in images+[v.get('url','') for v in videos]:
        if not isinstance(url,str):missing+=1;continue
        if url.startswith(('http://','https://')):remote+=1;continue
        if not url.startswith('/static/') or '?' in url or '#' in url:missing+=1;continue
        path=(Path(root)/url.removeprefix('/static/')).resolve()
        allowed=(Path(root)/'uploads').resolve()
        if not path.is_relative_to(allowed) or not path.is_file():missing+=1
        else:
            stat=path.stat();stamps.append([stat.st_size,stat.st_mtime_ns,stat.st_ino])
    if missing:add('media_files','attention','素材文件',f'{missing} 个本地媒体文件缺失或来源需核对，请重新添加素材。','workshop')
    elif images or videos:add('media_files','info' if remote else 'pass','素材文件',f'{remote} 个远程媒体地址未请求，提交时由发布器检查。' if remote else '所选本地媒体文件均存在。')
    if images and isinstance(images[0],str) and images[0].startswith('/static/'):
        path=(Path(root)/images[0].removeprefix('/static/')).resolve()
        if path.is_relative_to((Path(root)/'uploads').resolve()) and path.is_file():
            try:
                with Image.open(path) as cover:width,height=cover.size
                add('cover_size','attention' if min(width,height)<640 else 'pass','封面清晰度',f'封面为 {width}×{height} 像素。'+('建议换用短边至少640像素的原图。' if min(width,height)<640 else '尺寸达到本地建议值。'),'workshop' if min(width,height)<640 else None)
            except (OSError,UnidentifiedImageError,Image.DecompressionBombError):
                add('cover_size','attention','封面文件','封面图片暂未成功读取，请重新添加可用图片。','workshop')
    if videos:add('videos','attention' if len(videos)>3 else 'info','展示视频',f'已选 {len(videos)} 个视频。'+('请调整至3个以内。' if len(videos)>3 else '普通卖家发布图片；鱼小铺视频能力在提交时检查。'),'workshop' if len(videos)>3 else None)
    return checks,digest(stamps)


def inspect(job,plan,index,variant,root,private=''):
    result=job.result or {};checks=[]
    def add(ident,group,state,title,detail,action=None):
        checks.append({'id':ident,'group':group,'state':state,'title':title,'detail':detail,'action':action})
    blockers=list(plan.get('blockers') or [])
    publication=result.get('publication',{}).get('status')
    if job.status not in EDITABLE or publication in {'published','publishing','unknown'}:
        blockers.insert(0,'当前商品'+('已有发布记录，请核对原记录。' if publication in {'published','publishing','unknown'} else '尚未准备完成，请等当前步骤结束。'))
    from operations.workflows import current_flow
    own_flow=bool(job.parameters.get('workflow_hold') and current_flow.get()==job.parameters['workflow_hold'])
    if job.parameters.get('workflow_hold') and not own_flow:blockers.insert(0,'连续任务尚有后续步骤，完成后再审核发布。')
    if not plan.get('ready') and not blockers:blockers.append('发布条件尚待补齐，请检查当前设置。')
    for i,blocker in enumerate(dict.fromkeys(blockers)):
        action='settings'
        if any(word in blocker for word in ('卡券','网盘交付')):action='delivery'
        elif any(word in blocker for word in ('分类','草稿')):action='prepare'
        elif '图片' in blocker or '封面' in blocker:action='workshop'
        elif any(word in blocker for word in ('售价','成本')):action='pricing'
        elif '发布记录' in blocker or '核对' in blocker:action='publication'
        elif '步骤' in blocker or '连续任务' in blocker:action=None
        add('publish_'+str(i),'发布','blocked','发布条件',clean(blocker),action)
    if not blockers:add('publish_ready','发布','pass','发布必填信息','账号、分类、所在地、售价及已启用的托管配置通过当前发布预检。')
    title=str(variant.get('title') or '');description=str(variant.get('description') or '')
    text=title+'\n'+description
    if title:add('title','文案','attention' if len(title)>30 else 'pass','商品标题',f'标题 {len(title)} 字。'+('建议精简到30字内，突出商品类型和交付内容。' if len(title)>30 else '长度在编辑建议范围内。'),'optimize' if len(title)>30 else None)
    if description:add('description','文案','attention' if len(description.strip())<180 else 'pass','商品详情',f'详情 {len(description.strip())} 字。'+('可补充适用人群、交付清单、使用方式和已确认的服务边界。' if len(description.strip())<180 else '已有完整篇幅，仍请审核每项描述是否符合实际交付。'),'optimize' if len(description.strip())<180 else None)
    if not title or not description:add('copy_missing','文案','attention','文案完整性','标题或详情尚未齐备，请先完成商品草稿。','prepare')
    if PLACEHOLDERS.search(text):add('placeholders','文案','attention','模板占位内容','文案包含尚未替换的占位文字，请按已确认的商品信息完善。','optimize')
    kind=job.parameters.get('product_kind','source')
    patterns={'source':('源码|源代码','源码'),'software':('软件|程序|安装包','软件'),'tutorial':('教程|资料|文档|课程','教程资料'),'service':('服务|代做|定制','服务')}
    if kind in patterns and text.strip() and not re.search(patterns[kind][0],text):add('product_kind','文案','attention','交付类型','当前文案尚未明确说明交付'+patterns[kind][1]+'，建议写清买到什么。','optimize')
    overlap=private_overlap(text,private)
    if overlap:add('private_delivery','文案','attention','私有交付内容','公开文案出现了当前发货内容中的链接或口令，请先移除再生成优化方案。','edit')
    facts=result.get('facts',{}).get('facts') or []
    ids={str(f.get('id')) for f in facts if isinstance(f,dict) and f.get('id')}
    references=variant.get('fact_ids') or []
    invalid=bool(references and not set(map(str,references))<=ids)
    if not facts:add('facts','事实','attention','商品事实','尚未保存可核对的商品事实。请先补充实际交付、功能与售后范围。','facts')
    elif invalid:add('facts','事实','attention','事实引用','选中文案引用了当前事实列表中已缺失的编号，建议重新整理文案。','optimize')
    else:add('facts','事实','pass' if references else 'info','商品事实',f'当前保存 {len(facts)} 条事实。'+('文案引用的编号均存在；内容真实性仍需卖家核对。' if references else '这版文案未附事实编号，检查暂只统计材料完整度。'))
    pending=result.get('draft',{}).get('pending') or []
    if pending:add('pending','事实','attention','待核实信息',f'仍有 {len(pending)} 项商品信息待核实，确认后再加入成交承诺。','facts')
    price=result.get('price') or {}
    if price.get('review_recommended'):add('trial_price','定价','attention','建议试售价','当前价格属于试售建议，尚需结合实际成本和成交反馈审核。','pricing')
    from common.services.pricing_policy import COST_FIELDS,cost_floor
    incomplete=sum(job.parameters.get(key) is None for key in COST_FIELDS)
    if incomplete:add('cost','定价','attention','成本完整度',f'还有 {incomplete} 项成本或目标贡献未填写；当前底线仅覆盖已填写部分。','pricing')
    else:
        try:floor=cost_floor(job.parameters);add('cost','定价','pass','成本完整度',f'成本与目标贡献已填齐，成本底线 ¥{floor}。')
        except ValueError:add('cost','定价','attention','成本规则','成本规则需核对，请检查金额与费率。','pricing')
    managed=plan.get('automation') or {};settings=plan.get('settings') or job.parameters
    for key,label,ready in [('auto_reply_enabled','AI回复','reply_ready'),('auto_delivery_enabled','自动发货','delivery_ready')]:
        enabled=settings.get(key,True) is not False
        state='info' if not enabled or not managed else 'pass' if managed.get(ready) else 'attention'
        add(key,'托管',state,label,('当前已关闭，沿用你的选择。' if not enabled else '配置已就绪，上架成功后关联当前商品。' if managed.get(ready) else '配置尚待发布预检确认。'),'settings' if key=='auto_reply_enabled' else 'delivery')
    coverage=result.get('ranking',{}).get('coverage') or {};ranked=coverage.get('ranked',len(result.get('ranking',{}).get('hot100') or []))
    add('research','研究','info','竞品研究',f'当前研究包含 {ranked} 件进入榜单的商品。热度与标价用于参考，平台可见热度不等于实际销量。')
    counts={key:sum(c['state']==key for c in checks) for key in ('blocked','attention','pass','info')}
    reason=''
    if job.status not in EDITABLE or publication in {'published','publishing','unknown'}:reason='请在未发布商品准备完成后生成文案优化方案。'
    elif job.parameters.get('workflow_hold'):reason='请等连续任务完成后再生成文案优化方案。'
    elif not title or not description or not (result.get('price') or {}).get('suggested'):reason='请先完成标题、详情和售价，再生成优化方案。'
    elif not facts:reason='请先补充可核对的商品事实，再生成优化方案。'
    elif overlap:reason='请先移除公开文案中的私有交付内容，再生成优化方案。'
    elif not settings.get('publish_account_id'):reason='请先选择本次发布账号，再生成文案优化方案。'
    planned=(plan.get('preview') or {}).get('price')
    if planned is not None and price.get('suggested') is not None and float(planned)!=float(price['suggested']):
        add('price_sync','定价','attention','售价一致性','预览售价与方案建议价不同，请先在商品编辑中确认本次实际售价。','edit')
        counts['attention']+=1
        reason=reason or '请先确认预览中的实际售价，再生成文案优化方案。'
    return {'job_id':job.id,'variant_index':index,'checks':checks,'counts':counts,'publication_ready':not blockers,
            'can_optimize':not reason,'optimize_reason':reason,'generated_at':datetime.utcnow().isoformat()+'Z',
            'fingerprint':digest([revisions.current_hash(job),job.status,publication,index,plan.get('fingerprint'),checks]),
            'note':'按本地商品材料和发布配置检查；建议项供审核参考。检查结果不代表销量预测或平台审核结果。'}


async def report(job,root,variant_index=None,plan=None):
    index,variant=working(job,variant_index)
    if plan is None:
        try:plan=await publishing.prepare(job,index)
        except (ValueError,LookupError):plan={'ready':False,'blockers':['发布配置暂未完成检查，请核对当前设置。']}
    managed=(plan.get('snapshot') or {}).get('automation') or {}
    private=managed.get('delivery_text') or ''
    if managed.get('delivery_card_id'):
        async with async_session_maker() as session:
            card=await session.get(Card,managed['delivery_card_id'])
            if card and card.user_id==job.owner_id and card.type=='text':private+='\n'+(card.text_content or '')
    # Inspect the same media that will be published, including manual material changes.
    preview=plan.get('preview') or {};assets=(job.result or {}).get('assets') or {}
    images=preview.get('images') if preview.get('images') is not None else [i['url'] for i in assets.get('images',[])]
    videos=preview.get('videos') if preview.get('videos') is not None else assets.get('videos',[])
    value=inspect(job,plan,index,variant,root,private)
    media,media_stamp=await asyncio.to_thread(media_checks,job,images,videos,root)
    value['checks'].extend(media)
    value['counts']={key:sum(c['state']==key for c in value['checks']) for key in ('blocked','attention','pass','info')}
    value['fingerprint']=digest([value['fingerprint'],media,media_stamp])
    return value


def summary(value):
    counts=value['counts']
    lines=[f'商品检查：{counts["blocked"]} 项发布待补充，{counts["attention"]} 项审核建议，{counts["pass"]} 项检查通过。']
    lines.extend('- '+c['title']+'：'+c['detail'] for c in value['checks'] if c['state'] in {'blocked','attention'})
    if value['can_optimize']:lines.append('可以说“按检查建议优化文案”，先生成前后对比供你审核；账号、售价和卡券沿用当前设置。')
    return '\n'.join(lines)


async def improve(job,root,expected,index=None):
    value=await report(job,root,index)
    if value['fingerprint']!=expected:raise ValueError('商品内容、配置或素材文件已变化，请刷新商品检查后再优化')
    if not value['can_optimize']:raise ValueError(value['optimize_reason'])
    findings=[c['title']+'：'+c['detail'] for c in value['checks'] if c['group'] in {'文案','事实'} and c['state']=='attention']
    instruction='按商品检查建议优化标题和完整详情，保留售价与实际交付范围。仅使用已确认事实；未知信息保留在待核实项，公开文案排除模板占位和内部备注。\n'+'\n'.join(findings)
    # Provisional proposals stay inapplicable if post-generation inspection is interrupted.
    proposal=await optimization.create(job,instruction=instruction,variant_index=value['variant_index'],review_after_check=True)
    try:
        proposed_result=copy.deepcopy(job.result)
        proposed_result['draft']['variants'][value['variant_index']].update({k:proposal['after'][k] for k in ('title','description')})
        proposed_job=SimpleNamespace(id=job.id,owner_id=job.owner_id,keyword=job.keyword,status=job.status,
                                    parameters=job.parameters,result=proposed_result)
        checked=await report(proposed_job,root,value['variant_index'])
        if any(c['id'] in {'private_delivery','placeholders'} for c in checked['checks']):
            raise ValueError('生成内容仍含占位文字或私有交付信息，请重新生成或编辑')
        current=await store.get(job.id,job.owner_id)
        if not current or (await report(current,root,value['variant_index']))['fingerprint']!=expected:
            raise ValueError('生成期间商品内容或配置已变化，请刷新检查后重新生成')
    except Exception:
        await optimization.finish(proposal['id'],job.owner_id,'stale','生成后的商品检查未通过，请刷新检查后重新生成。')
        raise
    await optimization.finish(proposal['id'],job.owner_id,'pending')
    return optimization.public(await optimization.get(job.owner_id,proposal['id']))
