import { useEffect, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, Check, Copy, Loader, Sparkles, Wrench } from 'lucide-react'
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
  return events.filter((event,index) => {
    if(!event.data.outcome)return true
    for(const next of events.slice(index+1)) {
      if(next.kind==='user')break
      if(next.data.outcome)return false
    }
    return true
  })
}
export function Conversation({ events,query='',activeId=null }: { events: OperationEvent[];query?:string;activeId?:string|number|null }) {
  return <>{conversationMessages(events).map(event=><div key={event.id} id={`operation-event-${event.id}`} data-event-id={String(event.id)} data-search-active={activeId===event.id||undefined} className="conversation-entry">{event.kind==='user'
    ? <div className="message-user"><div>{highlight(String(event.data.text||event.name),query)}</div></div>
    : event.kind==='tool'
      ? <ToolEvent event={event}/>
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

const TOOL_LABELS: Record<string,string> = {
  plan:'理解需求与规划操作', research:'关键词研究', folder:'读取素材文件夹', facts:'确认商品事实',
  revise:'重新生成文案', auto_price:'自动定价', prepare:'准备发布草稿', publish:'生成发布确认单',
  sync:'生成同步发布确认单', retry_setup:'重试托管配置', stop:'停止任务', resume:'继续任务', export:'导出商品包',
  settings:'修改设置', variant:'切换文案版本', edit:'直接修改文案或价格', videos:'添加视频', images:'添加图片',
  remove_media:'删除媒体', cover:'设置封面', images_order:'排列图片顺序', categories:'查看分类', category:'选择分类',
  cards:'查看卡券', card_preview:'查看卡券内容', card_bind:'绑定发货卡券', card_create:'新建卡券',
  ai_list:'查看AI接口', ai_use:'复用AI配置', ai_models:'拉取模型列表', ai_model:'切换AI模型', ai_create:'新建AI接口',
  accounts:'查看账号', history:'查看历史任务', open_job:'打开历史任务', inspect:'查看当前状态', help:'查看操作说明',
  schedule_create:'创建批量排期', schedule_list:'查看排期', schedule_change:'修改发布时间', schedule_cancel:'取消排期',
  schedule_review:'生成排期审核单', calendar:'查看发布日历', calendar_adjust:'批量改期预览',
  quality_check:'发布前检查', quality_optimize:'生成文案优化', revisions:'查看版本记录', revision_save:'保存版本',
  revision_restore:'生成版本恢复单', products:'查看商品总控台', open_product:'接入已同步商品',
  templates:'查看经营模板', template_save:'保存经营模板', template_apply:'应用经营模板', workshop:'查看素材工坊',
  covers_generate:'生成三套封面', cover_apply:'使用封面', batch_review:'打开批量审核台',
  exceptions:'查看异常与恢复', publication_check:'核对发布结果', publication_audit:'上架验收',
  price_boundary:'查看成交底线', performance:'查看经营表现', optimize:'生成优化方案',
  optimization_history:'查看优化记录', optimization_check:'核对线上编辑', attention:'查看运营待办',
  repair_prepare:'生成重新准备单', account_check:'检查发布账号',
}

function eventDuration(event:OperationEvent):number|null {
  const start=Date.parse(String(event.created_at).replace(/Z$/,'')),end=event.finished_at?Date.parse(String(event.finished_at).replace(/Z$/,'')):NaN
  return Number.isNaN(start)||Number.isNaN(end)?null:Math.max(0,(end-start)/1000)
}
function formatDuration(seconds:number):string {
  if(seconds<60)return (seconds<10?Math.round(seconds*10)/10:Math.round(seconds))+' 秒'
  return Math.floor(seconds/60)+' 分 '+String(Math.round(seconds%60)).padStart(2,'0')+' 秒'
}
function ToolEvent({ event }: { event: OperationEvent }) {
  const data=event.data,operation=data.operation?String(data.operation):''
  const label=operation?(TOOL_LABELS[operation]??operation):event.name.replace(/^对话操作：/,'')
  const details=useRef<HTMLDetailsElement>(null),lastStatus=useRef<string|null>(null)
  useEffect(() => {
    if(!details.current)return
    const previous=lastStatus.current
    lastStatus.current=event.status
    if(previous===null)details.current.open=event.status!=='success'
    else if(previous!==event.status)details.current.open=event.status==='error'
  })
  const seconds=eventDuration(event)
  return <details className="tool-event" ref={details}>
    <summary className="tool-event-summary">
      <span className="tool-event-icon"><Wrench size={11} /></span>
      <span className="tool-event-label">{label}</span>
      {event.status==='running'
        ? <span className="tool-event-state is-running"><Loader size={11} className="animate-spin" />执行中</span>
        : event.status==='error'
          ? <span className="tool-event-state is-error"><AlertTriangle size={11} />失败</span>
          : <span className="tool-event-state is-done"><Check size={11} />完成</span>}
      {seconds!==null&&<span className="tool-event-duration">{formatDuration(seconds)}</span>}
    </summary>
    <div className="tool-event-body">
      {data.result&&<p className="tool-event-text">{data.result}</p>}
      {event.status==='error'&&<p className="tool-event-error">{data.error||(data.error_type?'执行中断（'+data.error_type+'）':'执行未完成')}</p>}
      {data.input!=null&&<pre className="tool-event-json">{JSON.stringify(data.input,null,2)}</pre>}
      {data.output!=null&&<pre className="tool-event-json">{JSON.stringify(data.output,null,2)}</pre>}
    </div>
  </details>
}
