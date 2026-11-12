import { useEffect, useRef, useState, type MutableRefObject } from 'react'
import { getOperationEvents, getOperationJob, listOperationJobs, type OperationEvent, type OperationJob } from '@/api/operations'
import { useAuthStore } from '@/store/authStore'
import { errorText } from './ui'

type ReadIssue = { message:string; unavailable:boolean }
function issue(reason:unknown):ReadIssue {
  const status=(reason as {response?:{status?:number}})?.response?.status
  return {message:errorText(reason),unavailable:status===403||status===404}
}
function version(job:OperationJob) {
  return JSON.stringify([job.id,job.updated_at,job.stage,job.status,job.material_id,job.publication,job.conversation])
}

/** Lists and the selected conversation load independently, with separate retries. */
export function useConversationData(owner:number|undefined,currentId:string|null,selectedId:MutableRefObject<string|null>) {
  const [jobs,setJobs]=useState<OperationJob[]>([])
  const [job,setJob]=useState<OperationJob|null>(null)
  const [events,setEvents]=useState<OperationEvent[]>([])
  const [listReady,setListReady]=useState(false)
  const [listBusy,setListBusy]=useState(false)
  const [listError,setListError]=useState('')
  const [sessionError,setSessionError]=useState<ReadIssue|null>(null)
  const [historyError,setHistoryError]=useState('')
  const [historyId,setHistoryId]=useState<string|null>(null)
  const [sessionBusy,setSessionBusy]=useState(false)
  const signature=useRef('')
  const rows=useRef(jobs);rows.current=jobs
  const current=useRef(job);current.current=job
  const refreshList=useRef<()=>Promise<void>>(async()=>{})
  const refreshSession=useRef<()=>Promise<void>>(async()=>{})

  useEffect(()=>{
    let disposed=false
    let pending:Promise<void>|null=null
    const valid=()=>!disposed&&useAuthStore.getState().user?.user_id===owner
    const load=()=>{
      if(pending)return pending
      if(!valid())return Promise.resolve()
      setListBusy(true)
      pending=(async()=>{
        try {
          const value=await listOperationJobs()
          if(!valid())return
          rows.current=value.jobs;setJobs(value.jobs);setListReady(true);setListError('')
        } catch(reason) {if(valid())setListError(errorText(reason))}
        finally {if(valid())setListBusy(false);pending=null}
      })()
      return pending
    }
    refreshList.current=load
    void load()
    const timer=window.setInterval(()=>{if(!document.hidden)void load()},3500)
    return()=>{disposed=true;window.clearInterval(timer)}
  },[owner])

  useEffect(()=>{
    let disposed=false
    let pending:Promise<void>|null=null
    let missing=false
    let failed=false
    setHistoryId(null)
    const valid=()=>!disposed&&selectedId.current===currentId&&useAuthStore.getState().user?.user_id===owner
    const load=(force=false):Promise<void>=>{
      if(pending)return pending.then(()=>force&&valid()?load(true):undefined)
      if(!currentId||!valid()||missing&&!force)return Promise.resolve()
      const row=rows.current.find(value=>value.id===currentId)
      const next=row?version(row):''
      const readDetail=force||failed||!current.current||current.current.id!==currentId||!next||signature.current!==next
      setSessionBusy(true)
      // Detail and history requests never wait for the sidebar request or each other.
      pending=(async()=>{
        const detail=async()=>{
          if(!readDetail)return
          try {
            const value=await getOperationJob(currentId)
            if(!valid())return
            if(value.id!==currentId)throw new Error('读取的商品与当前会话不一致，请重新读取')
            current.current=value;setJob(value);signature.current=next||version(value)
            failed=false;missing=false;setSessionError(null)
          } catch(reason) {
            if(!valid())return
            const error=issue(reason);failed=true;missing=error.unavailable
            setSessionError(error)
            if(missing){current.current=null;setJob(null);setEvents([]);setHistoryId(null);setHistoryError('')}
          }
        }
        const history=async()=>{
          try {
            const value=await getOperationEvents(currentId)
            if(valid()&&!missing){setEvents(value.events);setHistoryId(currentId);setHistoryError('')}
          } catch(reason) {if(valid()&&!missing)setHistoryError(errorText(reason))}
        }
        await Promise.all([detail(),history()])
        if(valid())setSessionBusy(false)
      })().finally(()=>{pending=null})
      return pending
    }
    refreshSession.current=()=>load(true)
    void load()
    const timer=window.setInterval(()=>{if(!document.hidden)void load()},3500)
    return()=>{disposed=true;window.clearInterval(timer)}
  },[currentId,owner,selectedId])

  return {
    jobs,job,setJob,events,setEvents,listReady,listBusy,listError,sessionError,historyError,historyReady:historyId===currentId,sessionBusy,signature,
    retryList:()=>refreshList.current(),
    retrySession:()=>refreshSession.current(),
    reload:async()=>{void refreshList.current();await refreshSession.current()},
    clearIssues:()=>{setSessionError(null);setHistoryError('');setSessionBusy(false)},
  }
}
