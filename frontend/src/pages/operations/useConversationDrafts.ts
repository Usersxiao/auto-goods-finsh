import { useMemo, useState } from 'react'

type Draft={text:string;updated:number}
const NEW='new'
const TTL=24*60*60*1000
const privateContent=/(?:api[\s_-]*key|密钥|cookie|\btoken\b|password|密码|提取码|解压码|发货内容|交付内容|https?:\/\/|\bsk-[\w-]{8,})/i

/** Keep drafts within this tab and login identity. Private content stays in memory. */
class Drafts {
  entries:Record<string,Draft>={}
  available=true
  key:string|null
  constructor(owner?:number){
    this.key=owner?`operations:composer-drafts:v1:${owner}`:null
    if(!this.key)return
    try {
      const data=JSON.parse(sessionStorage.getItem(this.key)||'{}') as Record<string,Draft>
      for(const [id,value] of Object.entries(data)){
        if(value&&typeof value.text==='string'&&value.text.length<=10000&&Number.isFinite(value.updated)&&Date.now()-value.updated<TTL&&!privateContent.test(value.text))this.entries[id]=value
      }
    } catch {this.available=false}
  }
  get(id:string|null){return this.entries[id||NEW]?.text||''}
  set(id:string|null,text:string){
    if(text)this.entries[id||NEW]={text:text.slice(0,10000),updated:Date.now()}
    else delete this.entries[id||NEW]
    this.persist()
  }
  persist(){
    if(!this.key)return
    try {
      const entries=Object.entries(this.entries).filter(([,value])=>Date.now()-value.updated<TTL&&!privateContent.test(value.text))
        .sort((a,b)=>b[1].updated-a[1].updated).slice(0,50)
      if(entries.length)sessionStorage.setItem(this.key,JSON.stringify(Object.fromEntries(entries)))
      else sessionStorage.removeItem(this.key)
      this.available=true
    } catch {this.available=false}
  }
}

export function useConversationDrafts(owner:number|undefined,currentId:string|null){
  const drafts=useMemo(()=>new Drafts(owner),[owner])
  const [,render]=useState(0)
  const update=()=>render(value=>value+1)
  return {
    text:drafts.get(currentId),
    setText:(text:string)=>{drafts.set(currentId,text);update()},
    clear:(id:string|null)=>{drafts.set(id,'');update()},
    restore:(id:string|null,text:string)=>{if(!drafts.get(id)){drafts.set(id,text);update();return true}return false},
    transfer:(from:string|null,to:string)=>{
      const text=drafts.get(from)
      if(!text||drafts.get(to))return
      drafts.set(to,text);drafts.set(from,'');update()
    },
    draftIds:new Set(Object.keys(drafts.entries).filter(id=>id!==NEW)),
    hint:!drafts.get(currentId)?'':privateContent.test(drafts.get(currentId))?'含私密信息或链接，仅在当前页面保留':drafts.key&&drafts.available?'草稿已保存到当前标签页':'草稿在当前页面保留',
  }
}
