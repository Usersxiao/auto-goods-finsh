import { useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react'
import type { OperationEvent } from '@/api/operations'
import { conversationMessages } from './Conversation'

type Position={anchor:string;offset:number;top:number;bottom:boolean;readTail:string;unread:string[];updated:number}
const TTL=24*60*60*1000
class Positions {
  values:Record<string,Position>={}
  key:string|null
  constructor(owner?:number){
    this.key=owner?`operations:reading-position:v1:${owner}`:null
    if(!this.key)return
    try {
      const values=JSON.parse(sessionStorage.getItem(this.key)||'{}')
      for(const [id,value] of Object.entries(values||{})){
        const row=value as Position
        if(row&&typeof row.anchor==='string'&&row.anchor.length<=128&&typeof row.readTail==='string'&&row.readTail.length<=128
          &&Number.isFinite(row.offset)&&Math.abs(row.offset)<100000&&Number.isFinite(row.top)&&row.top>=0&&row.top<10000000
          &&Array.isArray(row.unread)&&row.unread.length<=400&&row.unread.every(value=>typeof value==='string'&&value.length<=128)
          &&typeof row.bottom==='boolean'&&Number.isFinite(row.updated)&&Date.now()-row.updated>=0&&Date.now()-row.updated<TTL)this.values[id]={
            anchor:row.anchor,offset:row.offset,top:row.top,bottom:row.bottom,readTail:row.readTail,unread:row.unread,updated:row.updated,
          }
      }
    } catch {/* Reading positions still work in memory when storage is unavailable. */}
  }
  put(id:string,position:Position){
    this.values[id]=position
    this.values=Object.fromEntries(Object.entries(this.values).filter(([,row])=>Date.now()-row.updated<TTL)
      .sort((a,b)=>b[1].updated-a[1].updated).slice(0,50))
    try{if(this.key)sessionStorage.setItem(this.key,JSON.stringify(this.values))}catch{/* Keep the current page usable. */}
  }
}
type Reading={id:string|null;ready:boolean;following:boolean;position:Position|null;readTail:string;previous:Map<string,string>;unseen:Set<string>}
function entries(container:HTMLElement){return Array.from(container.querySelectorAll<HTMLElement>('.conversation-entry[data-event-id]'))}
function snapshot(container:HTMLElement,state:Reading):Position {
  const bounds=container.getBoundingClientRect()
  const anchor=entries(container).find(element=>element.getBoundingClientRect().bottom>bounds.top+1&&element.getBoundingClientRect().top<bounds.bottom)
  return {anchor:anchor?.dataset.eventId||'',offset:anchor?anchor.getBoundingClientRect().top-bounds.top:0,
    top:container.scrollTop,bottom:state.following,readTail:state.readTail,unread:[...state.unseen].slice(-400),updated:Date.now()}
}

/** Persist only message IDs and offsets, never conversation text or delivery content. */
export function useConversationReading({owner,currentId,feed,content,events,ready,searching}:{
  owner:number|undefined;currentId:string|null;feed:RefObject<HTMLDivElement>;content:RefObject<HTMLDivElement>
  events:OperationEvent[];ready:boolean;searching:boolean
}) {
  const positions=useMemo(()=>new Positions(owner),[owner])
  const state=useRef<Reading>({id:null,ready:false,following:true,position:null,readTail:'',previous:new Map(),unseen:new Set()})
  const [view,setView]=useState({id:currentId,away:false,unread:0})
  const visible=conversationMessages(events)
  const replies=visible.filter(event=>event.kind==='assistant'&&!event.data.historical)
  const version=JSON.stringify(replies.map(event=>[String(event.id),event.data.text||event.name]))
  const latest=useRef({currentId,ready,searching,replies});latest.current={currentId,ready,searching,replies}
  const scrollTarget=useRef<number|null>(null)
  const frame=useRef(0)
  const saveTimer=useRef(0)
  const publish=()=>{
    const reading=state.current
    setView(before=>before.id===reading.id&&before.away===!reading.following&&before.unread===reading.unseen.size
      ?before:{id:reading.id,away:!reading.following,unread:reading.unseen.size})
  }
  const markRead=()=>{
    const reading=state.current
    reading.unseen.clear();reading.readTail=String(latest.current.replies[latest.current.replies.length-1]?.id??'')
  }
  const save=()=>{
    const reading=state.current
    if(reading.id&&reading.ready&&reading.position)positions.put(reading.id,{...reading.position,readTail:reading.readTail,unread:[...reading.unseen].slice(-400),updated:Date.now()})
  }
  const capture=()=>{
    const reading=state.current,container=feed.current
    if(!container||reading.id!==latest.current.currentId||!reading.ready||!latest.current.ready)return
    reading.position=snapshot(container,reading);save()
  }
  const setTop=(container:HTMLElement,top:number)=>{
    const target=Math.max(0,Math.min(top,container.scrollHeight-container.clientHeight))
    scrollTarget.current=target;container.scrollTop=target
  }
  const align=()=>{
    const reading=state.current,container=feed.current
    if(!container||!reading.ready||!latest.current.ready||reading.id!==latest.current.currentId||latest.current.searching)return
    if(reading.following)setTop(container,container.scrollHeight)
    else if(reading.position){
      const position=reading.position,anchor=entries(container).find(element=>element.dataset.eventId===position.anchor)
      setTop(container,anchor?container.scrollTop+anchor.getBoundingClientRect().top-container.getBoundingClientRect().top-position.offset:position.top)
    }
    reading.position=snapshot(container,reading)
  }
  const schedule=()=>{
    cancelAnimationFrame(frame.current)
    frame.current=requestAnimationFrame(()=>{align();frame.current=0})
  }

  useLayoutEffect(()=>{
    const saved=currentId?positions.values[currentId]:null
    state.current={id:currentId,ready:false,following:saved?.bottom??true,position:saved||null,readTail:saved?.readTail||'',previous:new Map(),unseen:new Set(saved?.unread||[])}
    scrollTarget.current=null;publish()
    return()=>{cancelAnimationFrame(frame.current);window.clearTimeout(saveTimer.current);save()}
  },[currentId,positions])

  useLayoutEffect(()=>{
    const reading=state.current
    if(!ready||!currentId||reading.id!==currentId)return
    const messages=new Map(replies.map(event=>[String(event.id),event.data.text||event.name]))
    if(!reading.ready){
      reading.ready=true
      if(!reading.following&&reading.readTail){
        const index=replies.findIndex(event=>String(event.id)===reading.readTail)
        if(index>=0)replies.slice(index+1).forEach(event=>reading.unseen.add(String(event.id)))
      }
    } else if(!reading.following||searching){
      for(const [id,text] of messages)if(reading.previous.get(id)!==text)reading.unseen.add(id)
    }
    reading.previous=messages
    for(const id of reading.unseen)if(!messages.has(id))reading.unseen.delete(id)
    if(reading.following&&!searching)markRead()
    align();schedule();publish()
  },[currentId,ready,version,searching])

  useLayoutEffect(()=>{
    const container=feed.current,body=content.current
    if(!container||!body)return
    const observer=new ResizeObserver(()=>schedule())
    observer.observe(container);observer.observe(body)
    const flush=()=>capture()
    const hidden=()=>{if(document.hidden)flush()}
    window.addEventListener('pagehide',flush);document.addEventListener('visibilitychange',hidden)
    return()=>{observer.disconnect();window.removeEventListener('pagehide',flush);document.removeEventListener('visibilitychange',hidden)}
  },[currentId,positions])

  const onScroll=()=>{
    const reading=state.current,container=feed.current
    if(!container||!reading.ready||!latest.current.ready||reading.id!==latest.current.currentId)return
    if(scrollTarget.current!==null){
      const expected=scrollTarget.current;scrollTarget.current=null
      if(Math.abs(container.scrollTop-expected)<2)return
    }
    reading.following=container.scrollHeight-container.scrollTop-container.clientHeight<60
    if(reading.following&&!latest.current.searching)markRead()
    reading.position=snapshot(container,reading);publish()
    window.clearTimeout(saveTimer.current);saveTimer.current=window.setTimeout(save,180)
  }
  const pause=()=>{state.current.following=false;publish()}
  const locate=(id:string|number)=>{
    const container=feed.current,reading=state.current
    if(!container||reading.id!==latest.current.currentId)return
    const element=entries(container).find(element=>element.dataset.eventId===String(id))
    if(!element)return
    cancelAnimationFrame(frame.current);reading.following=false
    setTop(container,container.scrollTop+element.getBoundingClientRect().top-container.getBoundingClientRect().top-12)
    reading.position=snapshot(container,reading);publish();save()
  }
  const jumpLatest=()=>{
    const reading=state.current,container=feed.current
    if(!container||!reading.ready||reading.id!==latest.current.currentId)return
    reading.following=true;markRead();setTop(container,container.scrollHeight)
    reading.position=snapshot(container,reading);publish();save()
  }
  const jumpUnread=()=>{
    const first=latest.current.replies.find(event=>state.current.unseen.has(String(event.id)))
    if(!first)return
    locate(first.id)
    const container=feed.current
    if(container&&!latest.current.searching&&container.scrollHeight-container.scrollTop-container.clientHeight<60){
      state.current.following=true;markRead();state.current.position=snapshot(container,state.current);publish();save()
    }
  }
  return {away:view.id===currentId&&view.away,unread:view.id===currentId?view.unread:0,capture,onScroll,pause,locate,jumpLatest,jumpUnread}
}
