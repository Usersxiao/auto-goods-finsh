import { useEffect, useLayoutEffect, useState } from 'react'
import { Loader2, Plus, RefreshCw } from 'lucide-react'
import { createAiConnection, fetchConnectionModels, getAiConnections, type AiConnection } from '@/api/operations'
import { buttonClass, errorText } from './ui'

export function AiConnectionPicker({value,onChange,disabled=false,onDraftChange,purpose='publication'}: {value:string;onChange:(value:string)=>void;disabled?:boolean;onDraftChange?:(dirty:boolean)=>void;purpose?:'publication'|'published-reply'}) {
  const [options,setOptions]=useState<AiConnection[]>([]),[adding,setAdding]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('')
  const [name,setName]=useState(''),[url,setUrl]=useState(''),[key,setKey]=useState(''),[model,setModel]=useState(''),[provider,setProvider]=useState('openai_compatible')
  const [models,setModels]=useState<{id:string;name:string}[]>([])
  // Publish/close controls must see edits before the next keyboard or pointer event.
  useLayoutEffect(()=>{onDraftChange?.(Boolean(name||url||key||model||provider!=='openai_compatible'))},[name,url,key,model,provider,onDraftChange])
  useEffect(()=>{let alive=true;void getAiConnections().then(result=>{if(alive)setOptions(result.connections||[])}).catch(reason=>{if(alive)setError(errorText(reason))});return()=>{alive=false}},[])
  const load=async()=>{
    setBusy(true);setError('')
    try { const result=await fetchConnectionModels({base_url:url,api_key:key,provider_type:provider});setModels(result.models);if(!result.models.length)setError('接口未返回模型，可手动填写模型名称。') }
    catch(reason){setError(errorText(reason))}finally{setBusy(false)}
  }
  const save=async()=>{
    setBusy(true);setError('')
    try { const result=await createAiConnection({name,base_url:url,api_key:key,model,provider_type:provider});setOptions(rows=>[...rows,result]);onChange(result.id);setAdding(false);setName('');setUrl('');setKey('');setModel('');setProvider('openai_compatible');setModels([]) }
    catch(reason){setError(errorText(reason))}finally{setBusy(false)}
  }
  return <div className="space-y-3 text-xs" data-testid="ai-connection-picker">
    <label className="block">AI接口配置<select aria-label="AI接口配置" value={value} disabled={disabled||busy} onChange={event=>onChange(event.target.value)} className="input-ios mt-2 w-full"><option value="">使用各账号现有配置或铺货默认配置</option>{value&&!options.some(row=>row.id===value)&&<option value={value}>已选择的配置</option>}{options.map(row=><option key={row.id} value={row.id}>{row.name} · {row.model}</option>)}</select></label>
    <div className="flex items-center justify-between gap-2"><p className="leading-5 text-slate-400">{purpose==='published-reply'?'复用已有接口或新增接口；核对变更并确认后应用到原发布账号。':'复用配置时由服务端读取密钥，发布后应用到目标账号。'}</p><button type="button" disabled={disabled||busy} className={buttonClass+' shrink-0'} onClick={()=>setAdding(!adding)}><Plus size={12}/>新增接口</button></div>
    {adding&&<div className="space-y-3 rounded-xl border border-slate-200 p-3 dark:border-slate-700">
      <label className="block">配置名称<input aria-label="配置名称" className="input-ios mt-1 w-full" value={name} onChange={e=>setName(e.target.value)} placeholder="例如：通用客服模型"/></label>
      <label className="block">接口类型<select aria-label="接口类型" className="input-ios mt-1 w-full" value={provider} onChange={e=>{setProvider(e.target.value);setModels([])}}><option value="openai_compatible">OpenAI 兼容接口</option><option value="anthropic">Anthropic</option><option value="gemini">Gemini</option></select></label>
      <label className="block">接口地址<input aria-label="接口地址" type="url" className="input-ios mt-1 w-full" value={url} onChange={e=>{setUrl(e.target.value);setModels([])}} placeholder="https://…/v1"/></label>
      <label className="block">API Key<input aria-label="API Key" type="password" autoComplete="off" className="input-ios mt-1 w-full" value={key} onChange={e=>{setKey(e.target.value);setModels([])}}/></label>
      <button type="button" disabled={busy||!url.trim()||!key.trim()} className={buttonClass} onClick={()=>void load()}>{busy?<Loader2 size={12} className="animate-spin"/>:<RefreshCw size={12}/>}拉取模型</button>
      {!!models.length&&<label className="block">接口模型列表<select aria-label="接口模型列表" className="input-ios mt-1 w-full" value={models.some(row=>row.id===model)?model:''} onChange={e=>setModel(e.target.value)}><option value="">请选择模型（{models.length}）</option>{models.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</select></label>}
      <label className="block">模型名称<input aria-label="模型名称" className="input-ios mt-1 w-full" value={model} onChange={e=>setModel(e.target.value)} placeholder="拉取后选择，也可手动输入"/></label>
      <button type="button" className={buttonClass} disabled={busy||disabled||!model.trim()||!url.trim()||!key.trim()} onClick={()=>void save()}>保存并使用此接口</button>
    </div>}
    {error&&<p role="alert" className="text-amber-700">{error}</p>}
  </div>
}
