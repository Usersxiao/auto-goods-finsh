import { useRef, useState, type FormEvent } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'
import { chooseOperationCategory, type OperationJob, type OperationProfile, type PublishOptions, type PublishPlan } from '@/api/operations'
import { AiConnectionPicker } from './AiConnectionPicker'
import type { PlatformMaterialAttribute } from '@/api/productPublish'
import { PlatformAttributesEditor } from '@/pages/product-publish/PlatformAttributesEditor'
import { buttonClass, Dialog, errorText, kinds, primaryClass } from './ui'
import { automationScope } from './publishReview'
import { useUnsavedChanges } from './useUnsavedChanges'
import { UnsavedChangesNotice } from './UnsavedChangesNotice'

const numericFields: [keyof OperationProfile, string, boolean?][] = [
  ['price_min','最低成交价（元）'],['price_max','最高售价（元）'],['unit_cost','每单货源成本'],
  ['support_cost','每单预期售后成本'],['refund_cost','每单预期退款损失'],['acquisition_cost','每单获客成本'],
  ['target_contribution','每单目标贡献'],['fee_rate','费率（小数）'],['sample_target','候选采样数量',true],
  ['max_pages','采集页数上限',true],['detail_limit','详情补全上限',true],
]
export function OperationsSettings({profile,settings:incomingSettings,options,job,plan,close,save,changed,onDelivery,returnToReview=false,reviewLabel='发布总览'}: {
  profile?:OperationProfile;settings:PublishPlan['settings'];options:PublishOptions;job?:OperationJob|null;plan?:PublishPlan|null
  close:()=>void;save:(value:OperationProfile|PublishPlan['settings'])=>Promise<void>;changed:()=>Promise<void>
  onDelivery:()=>void
  returnToReview?:boolean
  reviewLabel?:string
}) {
  const [settings]=useState(incomingSettings)
  const [busy,setBusy]=useState(false),[error,setError]=useState('')
  const saving=useRef(false)
  const form=useRef<HTMLFormElement|null>(null),initialFields=useRef<string|null>(null)
  const [fieldsDirty,setFieldsDirty]=useState(false),[modelDirty,setModelDirty]=useState(false)
  const readFields=(element:HTMLFormElement)=>JSON.stringify(Array.from(new FormData(element).entries()))
  const [category,setCategory]=useState(job?.result?.category || plan?.category)
  const [attributes,setAttributes]=useState<PlatformMaterialAttribute[]>(category?.patch?.platform_attributes || [])
  const [initialAttributes,setInitialAttributes]=useState(()=>JSON.stringify(attributes))
  const [categoryBusy,setCategoryBusy]=useState(false)
  const available=options.accounts.filter(value=>value.active)
  const [account,setAccount]=useState(settings.publish_account_id || (available.length===1?available[0].id:'') )
  const [card,setCard]=useState(String(settings.delivery_card_id || plan?.automation?.delivery_card_id || ''))
  const [deliveryText,setDeliveryText]=useState(settings.delivery_text||'')
  const [aiRef,setAiRef]=useState(settings.ai_config_ref || '')
  const [replyEnabled,setReplyEnabled]=useState(settings.auto_reply_enabled!==false)
  const [deliveryEnabled,setDeliveryEnabled]=useState(settings.auto_delivery_enabled!==false)
  const dirty=fieldsDirty||modelDirty||aiRef!==(settings.ai_config_ref||'')||replyEnabled!==(settings.auto_reply_enabled!==false)||deliveryEnabled!==(settings.auto_delivery_enabled!==false)||JSON.stringify(attributes)!==initialAttributes
  const guard=useUnsavedChanges(dirty,busy||categoryBusy)
  const distributionReview=returnToReview&&reviewLabel==='同步审核'
  const requestClose=()=>guard.attempt(close,returnToReview?`返回${reviewLabel}`:'关闭设置')
  async function classify(selected_index?:number,values?:PlatformMaterialAttribute[]) {
    if(!job)return
    setCategoryBusy(true);setError('')
    try {
      const response=await chooseOperationCategory(job.id,{selected_index,attributes:values})
      const next=response.job.result?.category;setCategory(next);setAttributes(next?.patch?.platform_attributes || []);setInitialAttributes(JSON.stringify(next?.patch?.platform_attributes || []));await changed()
    }catch(reason){setError(errorText(reason))}finally{setCategoryBusy(false)}
  }
  async function submit(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();if(saving.current||categoryBusy)return
    if(modelDirty&&replyEnabled){setError('新增接口还未保存，请先点击“保存并使用此接口”，再保存当前设置。');return}
    const fields=new FormData(event.currentTarget)
    const value:Record<string,unknown>=profile?{...profile}:{...settings}
    fields.forEach((entry,key)=>{
      if(key.startsWith('model_'))return
      if(key==='confirmed_facts')value[key]=String(entry).split(/[\n；;]/).map(s=>s.trim()).filter(Boolean)
      else if(['account_id','product_kind','publish_mode','publish_account_id','publish_address','delivery_text'].includes(key))value[key]=entry||null
      else value[key]=entry===''?null:Number(entry)
    })
    value.auto_reply_enabled=replyEnabled;value.auto_delivery_enabled=deliveryEnabled;value.publish_template_id=null
    value.ai_config_ref=aiRef||null
    saving.current=true;setBusy(true);setError('')
    try {
      await save(value as unknown as OperationProfile);close()
    }catch(reason){setError(errorText(reason))}finally{saving.current=false;setBusy(false)}
  }
  return <Dialog title={profile?'默认经营设置':'分类与托管设置'} close={requestClose}><form ref={element=>{form.current=element;if(element&&initialFields.current===null)initialFields.current=readFields(element)}} onChange={()=>{if(form.current)setFieldsDirty(readFields(form.current)!==initialFields.current)}} onSubmit={event=>void submit(event)} className="space-y-5 text-sm">
    {returnToReview&&<div className="rounded-lg bg-primary-50 p-3 text-xs leading-6 text-primary-700 dark:bg-primary-900/30 dark:text-primary-200"><p>保存后会自动返回{reviewLabel}，按新设置重新审核。{distributionReview&&'此处修改原商品的共用分类、所在地与托管选项；同步账号和 AI 接口仍在同步审核页选择。复用账号保留原记录。'}</p>{reviewLabel==='批量审核'&&<><p className="mt-1 break-words font-medium">当前商品：{plan?.preview?.title||job?.keyword}</p><p>仅修改这件商品，批次内其他商品和已选范围保留。</p></>}</div>}
    <fieldset disabled={busy||categoryBusy} className="space-y-5">
    <p className="text-xs leading-6 text-slate-500">分类按商品内容自动匹配。发布成功后按下方勾选配置回复和发货；审核模式会先让你确认，自动模式直接执行。</p>
    {!distributionReview&&<><label className="block text-xs">完成后的操作<select className="input-ios mt-2 w-full" name="publish_mode" defaultValue={settings.publish_mode}><option value="review">审核模式：我检查后点击发布</option><option value="automatic">自动模式：生成完成后按托管设置发布</option></select></label>
    <label className="block text-xs">发布账号<select className="input-ios mt-2 w-full" name="publish_account_id" value={account} onChange={event=>setAccount(event.target.value)}><option value="">请选择账号</option>{options.accounts.map(value=><option key={value.id} value={value.id} disabled={!value.active}>{value.label}（{value.id}）{!value.active?' · 未登录或未启用':''}</option>)}</select></label></>}
    <label className="block text-xs">商品所在地<input className="input-ios mt-2 w-full" name="publish_address" defaultValue={settings.publish_address||''} placeholder="留空自动使用账号地址" maxLength={200}/></label>
    {job && <section className="space-y-3 rounded-xl border border-slate-200 p-4 dark:border-slate-700"><div className="flex items-center justify-between gap-2"><p className="text-xs font-semibold">平台分类 · {category?.label || '待匹配'}</p><button className={buttonClass} type="button" disabled={busy||categoryBusy} onClick={()=>void classify()}>{categoryBusy?<Loader2 size={12} className="animate-spin"/>:<RefreshCw size={12}/>}自动匹配</button></div>
      {category?.status==='needs_input'&&<p className="text-xs text-amber-700">{category.message}</p>}
      {!!category?.candidates?.length&&<details className="text-xs"><summary className="cursor-pointer text-slate-400">调整推荐分类</summary><div className="mt-2 flex flex-wrap gap-2">{category.candidates.map((item,index)=><button key={index} type="button" className={buttonClass} disabled={categoryBusy||busy} onClick={()=>void classify(index)}>{item.cat_name||item.channel_cat_name}</button>)}</div></details>}
      {!!category?.properties?.length&&<details className="text-xs"><summary className="cursor-pointer text-slate-400">商品属性</summary><div className="mt-3 space-y-3"><PlatformAttributesEditor properties={category.properties} attributes={attributes} candidate={category.candidates?.find(item=>item.is_selected)} onChange={setAttributes}/><button type="button" className={buttonClass} disabled={busy||categoryBusy} onClick={()=>void classify(undefined,attributes)}>保存属性</button></div></details>}
    </section>}
    <section className="space-y-4 rounded-xl border border-slate-200 p-4 dark:border-slate-700"><label className="flex items-start gap-2 text-xs font-semibold"><input type="checkbox" checked={deliveryEnabled} disabled={busy} onChange={event=>setDeliveryEnabled(event.target.checked)}/>发布后配置自动发货</label>
      {!deliveryEnabled?<p className="text-xs leading-6 text-slate-500">本次跳过发货配置，保留账号原有开关和规则。</p>:job?<><p className="text-xs">当前卡券：{plan?.automation?.delivery_name||'待选择'}</p><button type="button" className={buttonClass} onClick={()=>guard.attempt(onDelivery,'打开卡券选择')} disabled={busy}>选择 / 新建发货卡券</button><p className="text-[11px] leading-5 text-slate-400">卡券按商品单独保存，也可以直接在对话里指定名称或新建。</p></>:<>
      <label className="block text-xs">默认发货卡券<select className="input-ios mt-2 w-full" name="delivery_card_id" value={card} onChange={event=>{setCard(event.target.value);if(event.target.value)setDeliveryText('')}}><option value="">仅自动匹配唯一同类卡券</option>{options.cards?.map(value=><option value={value.id} key={value.id}>{value.name}{!value.enabled?'（发布成功后启用）':''}</option>)}</select></label>
      <label className="block text-xs">或填写默认交付内容<textarea name="delivery_text" className="input-ios mt-2 min-h-[120px] w-full leading-6" value={deliveryText} onChange={event=>{setDeliveryText(event.target.value);if(event.target.value)setCard('')}} maxLength={8000} placeholder="网盘链接、提取码、解压密码、领取说明…"/></label><p className="text-[11px] leading-5 text-slate-400">默认内容用于之后的新任务；不同商品请在各自对话中选择或新建专属卡券。</p></>}
    </section>
    <section className="rounded-xl border border-slate-200 p-4 dark:border-slate-700"><label className="flex items-start gap-2 text-xs font-semibold"><input type="checkbox" checked={replyEnabled} disabled={busy} onChange={event=>{const next=event.target.checked;if(!next&&modelDirty)guard.attempt(()=>{setModelDirty(false);setReplyEnabled(false)},'跳过 AI 回复配置');else setReplyEnabled(next)}}/>发布后配置 AI 回复</label><p className="mt-2 text-xs leading-6 text-slate-500">{replyEnabled?'使用已配置的模型，结合当前商品事实和问答回复买家。未确认的功能、环境和售后先核实。':'本次跳过回复配置，保留账号原有开关和规则。'}</p>
      {replyEnabled&&(distributionReview?<p className="mt-3 text-xs leading-6 text-slate-500">模型沿用同步审核页选择的接口或各账号现有配置，返回后逐账号检查。</p>:<div className="mt-3"><AiConnectionPicker value={aiRef} onChange={setAiRef} onDraftChange={setModelDirty} disabled={busy}/></div>)}
    </section>
    <p className="text-[11px] leading-6 text-amber-700 dark:text-amber-300">{automationScope(replyEnabled,deliveryEnabled)}</p>
    {profile&&<details className="rounded-lg border border-slate-200 p-3 dark:border-slate-700"><summary className="cursor-pointer text-xs">商品事实、售价与采样设置</summary><div className="mt-4 space-y-4 text-xs"><div className="grid grid-cols-2 gap-3"><label>商品类型<select className="input-ios mt-1 w-full" name="product_kind" defaultValue={profile.product_kind}>{Object.entries(kinds).map(([key,title])=><option key={key} value={key}>{title}</option>)}</select></label><label>采集账号<input className="input-ios mt-1 w-full" name="account_id" defaultValue={profile.account_id||''} placeholder="自动选取"/></label></div><label className="block">已确认事实<textarea className="input-ios mt-1 min-h-[100px] w-full" name="confirmed_facts" defaultValue={profile.confirmed_facts.join('\n')}/></label><div className="grid grid-cols-2 gap-3">{numericFields.map(([key,title,integer])=><label key={key}>{title}<input className="input-ios mt-1 w-full" type="number" step={integer?1:0.01} name={key} defaultValue={String(profile[key]??'')}/></label>)}</div></div></details>}
    </fieldset>
    {error&&<p role="alert" className="text-xs text-amber-700">{error}</p>}<UnsavedChangesNotice guard={guard}/><div className="flex justify-end gap-2"><button className={buttonClass} type="button" onClick={requestClose} disabled={busy||categoryBusy}>{returnToReview?`返回${reviewLabel}`:'取消'}</button><button className={primaryClass} disabled={busy||categoryBusy}>{busy?'保存中…':returnToReview?'保存并返回审核':'保存设置'}</button></div>
  </form></Dialog>
}
