import { useState, type ReactNode } from 'react'
import { Check, Copy, Sparkles } from 'lucide-react'
import type { OperationEvent } from '@/api/operations'

function highlight(text:string,query=''):ReactNode {
  if(!query)return text
  const lower=text.toLocaleLowerCase(),term=query.toLocaleLowerCase(),parts:ReactNode[]=[]
  let cursor=0,index=lower.indexOf(term)
  while(index>=0){parts.push(text.slice(cursor,index));parts.push(<mark key={index} className="rounded bg-amber-200 px-0.5 text-slate-900">{text.slice(index,index+term.length)}</mark>);cursor=index+term.length;index=lower.indexOf(term,cursor)}
  parts.push(text.slice(cursor));return parts
}
function inline(text: string,query=''): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => part.startsWith('**')
    ? <strong key={index} className="font-semibold text-slate-900 dark:text-slate-100">{highlight(part.slice(2,-2),query)}</strong>
    : part.startsWith('`') ? <code key={index} className="rounded bg-slate-100 px-1 py-0.5 text-[12px] dark:bg-slate-700">{highlight(part.slice(1,-1),query)}</code> : highlight(part,query))
}
export function Answer({ text,query='' }: { text: string;query?:string }) {
  return <div className="space-y-4 text-[14px] leading-7">{text.split(/\n\s*\n/).filter(Boolean).map((block,index) => {
    const lines=block.split('\n')
    if (lines.every(line => /^\s*[-*] /.test(line))) return <ul key={index} className="list-disc space-y-2 pl-5">{lines.map((line,i) => <li key={i}>{inline(line.replace(/^\s*[-*] /,''),query)}</li>)}</ul>
    if (lines.every(line => /^\s*\d+[.)、] /.test(line))) return <ol key={index} className="list-decimal space-y-2 pl-5">{lines.map((line,i) => <li key={i}>{inline(line.replace(/^\s*\d+[.)、] /,''),query)}</li>)}</ol>
    if (/^#{1,3} /.test(block)) return <p key={index} className="font-semibold">{inline(block.replace(/^#{1,3} /,''),query)}</p>
    return <p key={index} className="whitespace-pre-wrap break-words">{inline(block,query)}</p>
  })}</div>
}
export function conversationMessages(events:OperationEvent[]) {
  const visible=events.filter(event => event.kind !== 'tool')
  return visible.filter((event,index) => {
    if(!event.data.outcome)return true
    for(const next of visible.slice(index+1)) {
      if(next.kind==='user')break
      if(next.data.outcome)return false
    }
    return true
  })
}
export function Conversation({ events,query='',activeId=null }: { events: OperationEvent[];query?:string;activeId?:string|number|null }) {
  return <>{conversationMessages(events).map(event=><div key={event.id} id={`operation-event-${event.id}`} data-event-id={String(event.id)} data-search-active={activeId===event.id||undefined} className="conversation-entry">{event.kind==='user'
    ? <div className="message-user"><div>{highlight(String(event.data.text||event.name),query)}</div></div>
    : <AssistantMessage event={event} query={query}/>}</div>)}</>
}

function AssistantMessage({ event,query }: { event: OperationEvent;query:string }) {
  const [copied, setCopied] = useState(false)
  const [copyFailed, setCopyFailed] = useState(false)
  const text = event.data.text || event.name
  return <div className="conversation-message" data-testid="assistant-answer">
    <div className="message-byline"><span className="message-avatar"><Sparkles size={12} /></span><span className="font-medium">运营助手</span><time className="ml-1 text-[9px] text-slate-400" dateTime={event.created_at}>{Number.isNaN(Date.parse(event.created_at)) ? '' : new Date(event.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })}</time></div>
    <Answer text={text} query={query}/>
    <button type="button" className="message-copy" aria-label="复制助手回复" onClick={async () => {
      try { await navigator.clipboard.writeText(text); setCopied(true); setCopyFailed(false) }
      catch { setCopyFailed(true) }
    }}>{copied ? <Check size={12} /> : <Copy size={12} />}{copyFailed ? '复制失败，请选择文字复制' : copied ? '已复制' : '复制内容'}</button>
  </div>
}
