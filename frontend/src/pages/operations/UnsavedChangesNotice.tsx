import { useEffect, useRef } from 'react'
import type { useUnsavedChanges } from './useUnsavedChanges'

export function UnsavedChangesNotice({guard}:{guard:ReturnType<typeof useUnsavedChanges>}) {
  const panel=useRef<HTMLDivElement>(null)
  useEffect(()=>{if(guard.pending){panel.current?.focus();panel.current?.scrollIntoView({block:'nearest'})}},[guard.pending])
  if(!guard.pending)return null
  return <div ref={panel} tabIndex={-1} role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs outline-none dark:border-amber-800 dark:bg-amber-950/30" data-testid="unsaved-changes-notice">
    <p className="leading-6 text-amber-800 dark:text-amber-200">当前还有未保存修改。{guard.pending.label}前，请选择如何处理。</p>
    <div className="mt-2 flex flex-wrap gap-4"><button type="button" className="font-medium text-primary-600 dark:text-primary-400" disabled={guard.busy} onClick={guard.stay}>继续编辑</button><button type="button" className="text-amber-800 dark:text-amber-200" disabled={guard.busy} onClick={guard.discard}>放弃修改并继续</button></div>
  </div>
}
