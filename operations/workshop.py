"""Local cover composition and draft media ordering with stale-result protection."""
import asyncio
import copy
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy import select
from common.db.session import async_session_maker
from operations.models import Job
from operations import store

STYLES=[('商务深蓝','#101c35','#ffffff','#7dd3fc'),('简洁留白','#f5f2eb','#20252b','#326b58'),
        ('明亮醒目','#fff1bb','#29241d','#b84c28')]


def editable(job):
    if job.status in {'queued','running','published','publish_unknown'} or (job.result or {}).get('publication',{}).get('status') in {'publishing','published','unknown'}:
        raise ValueError('请在未发布商品准备完成后调整素材')


def inputs(job,headline='',fact_ids=None):
    result=job.result or {};variants=result.get('draft',{}).get('variants') or []
    if not variants:raise ValueError('请先生成商品文案，再制作封面')
    index=int(job.parameters.get('publish_variant_index',0));variant=variants[max(0,min(index,len(variants)-1))]
    headline=(headline or variant.get('title') or job.keyword).strip()
    if not 1<=len(headline)<=60:raise ValueError('封面标题请填写1至60字')
    facts=result.get('facts',{}).get('facts') or []
    selected=[f for f in facts if f.get('id') in fact_ids] if fact_ids is not None else facts[:3]
    if fact_ids is not None and (len(fact_ids)>3 or {f.get('id') for f in selected}!=set(fact_ids)):
        raise ValueError('请选择最多3条当前商品事实')
    claims=[]
    for fact in selected:
        statement=str(fact.get('statement') or '').strip()
        if re.search(r'https?://|提取码|解压密码|api.?key|sk-',statement,re.I):continue
        if statement:claims.append({'id':fact.get('id'),'text':statement[:48]})
    return {'headline':headline,'claims':claims}


def source_hash(job):
    return hashlib.sha256(json.dumps([store.draft_hash(job.result or {}),
        (job.result or {}).get('facts'),job.parameters.get('publish_variant_index',0)],sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def local_image(root,job_id,url):
    # Only assets belonging to this task's local image inventory are accepted.
    if not isinstance(url,str) or not url.startswith('/static/'):raise ValueError('请选择当前商品的本地图片')
    allowed=(Path(root)/'uploads'/'products'/'operations'/job_id).resolve()
    path=(Path(root)/url.removeprefix('/static/')).resolve()
    if not path.is_relative_to(allowed) or not path.is_file() or path.is_symlink():
        raise ValueError('当前图片来源或文件已变化，请重新添加素材')
    return path


def font(size):
    candidates=[os.environ.get('OPERATIONS_CJK_FONT',''),'/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/System/Library/Fonts/PingFang.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']
    for path in candidates:
        if path and Path(path).is_file():return ImageFont.truetype(path,size=size)
    raise ValueError('请安装中文字体或设置 OPERATIONS_CJK_FONT 后再生成封面')


def wrap(draw,text,face,width):
    lines=[];line=''
    for char in text.replace('\n',' '):
        if line and draw.textlength(line+char,font=face)>width:
            lines.append(line);line=char
        else:line+=char
    if line:lines.append(line)
    return lines


def compose(root,job,values):
    images=(job.result or {}).get('assets',{}).get('images',[])
    originals=[item for item in images if not item.get('workshop_generated')]
    image_path=local_image(root,job.id,originals[0]['url']) if originals else None
    if image_path and image_path.stat().st_size>25*1024*1024:raise ValueError('图片超过25MB，请先压缩素材')
    seed=hashlib.sha256(json.dumps([source_hash(job),values],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    destination=Path(root)/'uploads'/'products'/'operations'/job.id
    destination.mkdir(parents=True,exist_ok=True)
    candidates=[]
    for index,(name,bg,fg,accent) in enumerate(STYLES):
        output=destination/(seed[:24]+f'-cover-{index}.jpg')
        canvas=Image.new('RGB',(1200,1200),bg);draw=ImageDraw.Draw(canvas)
        draw.rounded_rectangle((72,72,80,150),radius=4,fill=accent)
        draw.text((106,80),job.keyword[:26],font=font(27),fill=accent)
        for size in (72,64,56,48,40):
            face=font(size);lines=wrap(draw,values['headline'],face,1040)
            if len(lines)<=3:break
        top=176
        for line in lines:
            draw.text((72,top),line,font=face,fill=fg);top+=size+16
        top+=20
        for claim in values['claims']:
            claim_face=font(29)
            for line in wrap(draw,claim['text'],claim_face,1000)[:2]:
                draw.text((86,top),line,font=claim_face,fill=fg);top+=40
            top+=10
        image_top=max(top+32,660)
        if image_path and image_top<1090:
            with Image.open(image_path) as raw:
                if raw.width*raw.height>40_000_000:raise ValueError('图片像素超过处理上限')
                picture=ImageOps.exif_transpose(raw).convert('RGB')
                picture=ImageOps.contain(picture,(1056,1128-image_top))
                canvas.paste(picture,((1200-picture.width)//2,image_top+(1128-image_top-picture.height)//2))
        elif not image_path:
            draw.rounded_rectangle((72,980,1128,1128),radius=24,outline=accent,width=3)
            draw.text((106,1027),job.keyword[:30],font=font(32),fill=accent)
        temp=output.with_name(output.stem+'-'+uuid.uuid4().hex+'.tmp')
        try:
            canvas.save(temp,'JPEG',quality=94);temp.replace(output)
        finally:
            temp.unlink(missing_ok=True)
        candidates.append({'id':seed[:24]+f'-{index}','name':name,'headline':values['headline'],
            'fact_ids':[c['id'] for c in values['claims']], 'claims':values['claims'],
            'url':'/static/'+str(output.relative_to(Path(root))), 'source_hash':source_hash(job)})
    return candidates


async def generate(job,root,headline='',fact_ids=None):
    editable(job);expected=source_hash(job)
    values=inputs(job,headline,fact_ids)
    candidates=await asyncio.to_thread(compose,root,job,values)
    async with async_session_maker() as session:
        row=(await session.execute(select(Job).where(Job.id==job.id,Job.owner_id==job.owner_id).with_for_update())).scalar_one()
        editable(row)
        if source_hash(row)!=expected:raise ValueError('商品内容已变化，请按最新文案重新生成封面')
        result=copy.deepcopy(row.result or {});result['workshop']={'candidates':candidates,'source_hash':expected}
        row.result=result
        await session.commit();await session.refresh(row)
    return row


async def use_cover(owner,job_id,candidate_id):
    async with async_session_maker() as session:
        job=await session.get(Job,job_id)
    if not job or job.owner_id!=owner:raise ValueError('商品任务不存在')
    editable(job)
    candidate=next((c for c in (job.result or {}).get('workshop',{}).get('candidates',[]) if c['id']==candidate_id),None)
    if not candidate:raise ValueError('请选择当前商品生成的封面')
    work=(job.result or {}).get('workshop',{})
    if source_hash(job) not in (candidate['source_hash'],work.get('applied_source_hash')):
        raise ValueError('文案或素材已变化，请重新生成封面')
    return await store.change_media(job_id,owner,{'kind':'images','new_cover':{
        'asset_id':'cover_'+candidate['id'],'url':candidate['url'],'name':candidate['name'],'workshop_generated':True}},
        store.draft_hash(job.result or {}))
