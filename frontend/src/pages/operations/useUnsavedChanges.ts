import { useEffect, useRef, useState } from 'react'

export function useUnsavedChanges(dirty:boolean, busy:boolean) {
  const [pending,setPending]=useState<{label:string;run:()=>void}|null>(null)
  const previousFocus=useRef<HTMLElement|null>(null)
  useEffect(()=>{
    if(!dirty)return
    const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue=''}
    window.addEventListener('beforeunload',warn)
    return()=>window.removeEventListener('beforeunload',warn)
  },[dirty])
  const attempt=(run:()=>void,label='离开当前编辑')=>{
    if(busy)return
    if(!dirty){run();return}
    previousFocus.current=document.activeElement as HTMLElement|null
    setPending({label,run})
  }
  const stay=()=>{setPending(null);requestAnimationFrame(()=>previousFocus.current?.focus())}
  const discard=()=>{if(!busy&&pending){setPending(null);pending.run()}}
  return {pending,attempt,stay,discard,busy}
}
