import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronUp, Search, X } from 'lucide-react'
import type { OperationEvent } from '@/api/operations'
import { conversationMessages } from './Conversation'

export function ConversationFind({events,change,locate,close}:{events:OperationEvent[];change:(query:string,id:string|number|null)=>void;locate:(id:string|number)=>void;close:()=>void}){
  const [query,setQuery]=useState(''),[active,setActive]=useState<string|number|null>(null)
  const input=useRef<HTMLInputElement>(null)
  const callbacks=useRef({change,locate});callbacks.current={change,locate}
  const term=query.trim().toLocaleLowerCase()
  const matches=term?conversationMessages(events).filter(event=>String(event.data.text||event.name).toLocaleLowerCase().includes(term)):[]
  const ids=matches.map(event=>String(event.id)).join(',')
  const index=matches.findIndex(event=>event.id===active)
  useEffect(()=>{input.current?.focus()},[])
  useEffect(()=>{
    const id=matches.some(event=>event.id===active)?active:matches[0]?.id??null
    setActive(id);callbacks.current.change(query.trim(),id)
    if(id!==null)callbacks.current.locate(id)
  },[query,ids,active])
  const step=(direction:number)=>{if(matches.length)setActive(matches[(Math.max(index,0)+direction+matches.length)%matches.length].id)}
  return <div className="conversation-find" role="search" aria-label="查找当前对话" onKeyDown={event=>{
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close()}
    if(event.key==='Enter'&&!event.nativeEvent.isComposing){event.preventDefault();step(event.shiftKey?-1:1)}
  }}><Search size={14} className="shrink-0 text-slate-400"/><input id="operation-conversation-find" ref={input} aria-label="对话查找关键词" maxLength={200} placeholder="查找当前已加载的对话" value={query} onChange={event=>{setActive(null);setQuery(event.target.value)}}/><span className="shrink-0 text-[10px] text-slate-400" role="status">{term?matches.length?`${Math.max(index,0)+1}/${matches.length} 条`:'没有匹配':'查找消息'}</span><button aria-label="上一条匹配" title="上一条 · Shift+Enter" disabled={!matches.length} onClick={()=>step(-1)}><ChevronUp size={15}/></button><button aria-label="下一条匹配" title="下一条 · Enter" disabled={!matches.length} onClick={()=>step(1)}><ChevronDown size={15}/></button><button aria-label="关闭对话查找" onClick={close}><X size={14}/></button></div>
}
