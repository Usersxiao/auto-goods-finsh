import {useEffect,useRef,useState} from 'react'
import {ExternalLink,Loader2,RefreshCw} from 'lucide-react'
import {applyPublishedAccountRules,getPublishedAccountPlan,type OperationJob,type PublishedAccountPlan} from '@/api/operations'
import {buttonClass,Dialog,errorText,primaryClass} from './ui'
import {useUnsavedChanges} from './useUnsavedChanges'
import {UnsavedChangesNotice} from './UnsavedChangesNotice'

export function PublishedAccountDialog({job,close,saved,edit}:{job:OperationJob;close:()=>void;saved:()=>Promise<void>;edit:(kind:'reply'|'delivery')=>void}){
 const [plan,setPlan]=useState<PublishedAccountPlan|null>(null),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[revision,setRevision]=useState(0)
 const [selected,setSelected]=useState<string[]>([]),[confirmed,setConfirmed]=useState(false)
 const generation=useRef(0),saving=useRef(false),alive=useRef(true)
 const guard=useUnsavedChanges(selected.length>0,busy)
 const publication=job.result?.publication||job.publication
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[])
 useEffect(()=>{
  const stamp=++generation.current;setLoading(true);setError('');setPlan(null);setSelected([]);setConfirmed(false)
  void getPublishedAccountPlan(job.id).then(value=>{
   if(stamp!==generation.current)return
   if(value.job_id!==job.id||value.account_id!==publication?.account_id||value.item_id!==publication?.item_id||!value.fingerprint)throw Error('账号检查与原发布商品不一致，请重新读取')
   setPlan(value)
  }).catch(reason=>{if(stamp===generation.current)setError(errorText(reason))}).finally(()=>{if(stamp===generation.current)setLoading(false)})
  return()=>{generation.current++}
 },[job.id,publication?.account_id,publication?.item_id,revision])
 const ready=Boolean(plan?.ready&&!loading&&!busy&&!error&&selected.length)
 const apply=async()=>{
  if(!ready||!plan||!confirmed||saving.current)return
  saving.current=true;setBusy(true);setError('')
  try{await applyPublishedAccountRules(job.id,selected,plan.fingerprint);if(!alive.current)return;try{await saved()}catch{/* The accepted change remains saved if background refresh fails. */}if(alive.current)close()}
  catch(reason){if(alive.current){setError(errorText(reason));setConfirmed(false)}}finally{saving.current=false;if(alive.current)setBusy(false)}
 }
 const leave=(run:()=>void,label:string)=>guard.attempt(run,label)
 return <Dialog title="已上架商品 · 账号检查" close={()=>leave(close,'返回上架验收')}><div className="space-y-4 text-xs leading-6">
  <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-900/30"><p className="break-words font-medium">{job.keyword}</p><p className="break-all text-slate-500 dark:text-slate-400">原发布账号：{plan?.account_name||publication?.account_id} · 商品 ID：{publication?.item_id}</p><p className="text-slate-500 dark:text-slate-400">完成后返回原报告核对。</p></div>
  <div className="flex flex-wrap gap-2"><button className={buttonClass} disabled={loading||busy} onClick={()=>leave(()=>setRevision(value=>value+1),'重新检查账号')}><RefreshCw size={13}/>重新检查账号</button>{publication?.account_id&&<a className={buttonClass} href={'/accounts?'+new URLSearchParams({account_id:publication.account_id})} target="_blank" rel="noopener noreferrer">打开此账号管理<ExternalLink size={13}/></a>}</div>
  {loading&&<p role="status" className="flex items-center gap-2 text-slate-500"><Loader2 size={13} className="animate-spin"/>读取原账号与此商品的发货规则…</p>}
  {error&&<p role="alert" className="rounded-lg bg-amber-50 p-3 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{error}。请重新检查后核对；已保存操作可返回报告验收。</p>}
  {plan&&<>
   <section aria-label="账号本地状态" className="space-y-2 rounded-xl border border-slate-200 p-3 dark:border-slate-700"><p>账号状态：{plan.account.enabled?'已启用':'待启用'} · 登录凭据：{plan.account.credential_present?'已保存':'待重新登录'}</p><p className="text-slate-500 dark:text-slate-400">{plan.login_note}</p><div className="flex flex-wrap items-center justify-between gap-2"><p>AI 回复：{plan.reply.enabled?'已开启':'待开启'} · 接口：{plan.reply.configured?'已填写':'待补充'}{plan.reply.model&&<span className="break-all"> · {plan.reply.model}</span>}</p><button className={buttonClass} disabled={busy} onClick={()=>leave(()=>edit('reply'),'配置 AI 回复')}>配置 AI 回复</button></div><div className="flex flex-wrap items-center justify-between gap-2"><p>发货关联：{plan.delivery.binding_ready?'已准备':'待完善'} · 已绑定 {plan.delivery.card_count} 张卡券</p><button className={buttonClass} disabled={busy} onClick={()=>leave(()=>edit('delivery'),'检查发货卡券')}>检查发货卡券</button></div><p className="text-slate-500 dark:text-slate-400">自动确认发货：{plan.delivery.auto_confirm?'开启':'关闭'} · 仅发卡券：{plan.delivery.only_send_card?'开启':'关闭'}</p></section>
   <section aria-label="此商品的条件发货规则" className="rounded-xl border border-slate-200 p-3 dark:border-slate-700"><h3 className="font-semibold">条件发货规则</h3><p className="mt-1 text-slate-500 dark:text-slate-400">规则启用表示订单满足条件时才拦截，并非所有订单都会被拦截。勾选要仅为此商品排除的具体规则。</p><div className="mt-3 space-y-2">{plan.rules.map(rule=><label key={rule.code} className="flex items-start gap-2 rounded-lg bg-slate-50 p-3 dark:bg-slate-900/30"><input aria-label={'仅此商品排除：'+rule.name} type="checkbox" className="mt-1.5 shrink-0" checked={selected.includes(rule.code)} disabled={busy||!plan.ready||!rule.enabled||rule.excluded||!rule.known} onChange={event=>{setSelected(values=>event.target.checked?[...values,rule.code]:values.filter(code=>code!==rule.code));setConfirmed(false)}}/><span className="min-w-0"><strong className="break-words">{rule.name}</strong><span className="block text-slate-500 dark:text-slate-400">{!rule.known?'规则类型待核对，请在账号管理检查':!rule.enabled?'未启用':rule.excluded?'已排除此商品':'适用于此商品，命中条件时拦截'}{rule.enabled&&` · 命中后${rule.auto_close?'主动关闭订单':'保留订单'}`}{rule.enabled&&rule.auto_close&&rule.only_card_after_close?' · 关闭后仅发卡券':''}</span></span></label>)}{!plan.rules.length&&<p className="text-slate-500 dark:text-slate-400">尚未保存条件发货规则。</p>}</div>{plan.delivery.legacy_warning&&<p className="mt-2 text-amber-700 dark:text-amber-300">旧配置含限制标记，实际规则尚待核对，请打开此账号管理检查。</p>}</section>
   <p className="rounded-lg bg-amber-50 p-3 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{plan.note}</p>
   {!!plan.blockers.length&&<div role="alert" className="text-amber-700 dark:text-amber-300">{plan.blockers.map(text=><p key={text}>{text}</p>)}</div>}
  </>}
  <label className="flex items-start gap-2"><input aria-label="确认此商品发货规则调整" type="checkbox" className="mt-1.5 shrink-0" checked={confirmed&&ready} disabled={!ready} onChange={event=>setConfirmed(event.target.checked)}/><span>我已核对原账号与商品，仅为此商品排除所选 {selected.length} 条规则，后续订单按调整后的规则处理。</span></label>
  <UnsavedChangesNotice guard={guard}/><div className="flex flex-wrap justify-end gap-2"><button className={buttonClass} disabled={busy} onClick={()=>leave(close,'返回上架验收')}>返回上架验收</button><button className={primaryClass} disabled={!ready||!confirmed} onClick={()=>void apply()}>{busy&&<Loader2 size={13} className="animate-spin"/>}确认仅调整此商品</button></div>
 </div></Dialog>
}
