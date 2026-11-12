import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowDown, CheckCheck, CreditCard, Download, Eye, FileText, FolderOpen, ClipboardCheck, ClipboardList, History, CalendarClock, LayoutGrid, Loader2, MessageSquare, PanelLeft, PanelRight, Plus, RefreshCw, Search, Send, Settings2, Sparkles, Square, Timer, X, Zap } from 'lucide-react'
import {
  addOperationVideos, configureOperationPublish, downloadOperationFile, getOperationEvents, getOperationProfile, prepareOperation,
  getOperationJob, getPublishOptions, getPublishPlan, operationAction, publishOperation, saveOperationProfile, sendOperationMessage,
  type OperationEvent, type OperationJob, type OperationProfile, type PublishOptions, type PublishPlan, type PerformanceReport, type OptimizationProposal, type ProductHubReport, type ProductQualityReport, type PublicationCalendarReport, type ScheduleAdjustment, type AttentionReport, type RepairReview,
} from '@/api/operations'
import { cn } from '@/utils/cn'
import { useAuthStore } from '@/store/authStore'
import { Answer, Conversation } from './Conversation'
import { ListingEditor } from './ListingEditor'
import { PublishConfirmation } from './PublishConfirmation'
import { publishPlanKey, publishSourceKey } from './publishReview'
import { PerformanceBoard } from './PerformanceBoard'
import { OperationsSettings } from './OperationsSettings'
import { ProductPreview } from './ProductPreview'
import { DistributionDialog, type DistributionEditor, type DistributionSelection } from './DistributionDialog'
import { PublicationAuditEditor, type AuditEditorTarget } from './PublicationAuditEditor'
import { PublicationResults } from './PublicationResults'
import { DeliveryDialog } from './DeliveryDialog'
import { ScheduleBoard } from './ScheduleBoard'
import { WorkflowCard } from './WorkflowCard'
import { BatchReviewPanel } from './BatchReviewPanel'
import { RevisionPanel } from './RevisionPanel'
import { QualityPanel } from './QualityPanel'
import { PublicationCalendar } from './PublicationCalendar'
import { AttentionPanel } from './AttentionPanel'
import { PendingActionCard } from './PendingActionCard'
import { ConversationFind } from './ConversationFind'
import { useConversationDrafts } from './useConversationDrafts'
import { useConversationData } from './useConversationData'
import { useConversationReading } from './useConversationReading'
import { SessionSidebar } from './SessionSidebar'
import { WorkspacePanel, type WorkspaceTab } from './WorkspacePanel'
import './operations.css'
import { buttonClass, Dialog, errorText, Evidence, statuses } from './ui'

type Mode = 'review' | 'automatic'
interface PublishReview {plan:PublishPlan|null;job:OperationJob;variant:number;accountName:string;deliveryName:string;revision:number}
interface ReviewReturn {jobId:string;variant:number;distribution?:DistributionSelection}
const emptyOptions: PublishOptions = { accounts: [], templates: [] }
function savedConversation(key:string|null){try{return key?sessionStorage.getItem(key):null}catch{return null}}
function rememberConversation(key:string|null,value:string){try{if(key)sessionStorage.setItem(key,value)}catch{/* Drafts continue in memory when browser storage is disabled. */}}

export function ProductOperations() {
  const owner=useAuthStore(state=>state.user?.user_id)
  return <OperationsWorkspace key={owner??'guest'} owner={owner}/>
}

function OperationsWorkspace({owner}:{owner:number|undefined}) {
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [selectionReady,setSelectionReady]=useState(false)
  const selectedId = useRef(currentId)
  selectedId.current = currentId
  const data=useConversationData(owner,currentId,selectedId)
  const {jobs,job,setJob,events,setEvents,signature}=data
  const drafts=useConversationDrafts(owner,currentId)
  const {text,setText}=drafts
  const [optimistic, setOptimistic] = useState<OperationEvent[]>([])
  const [sending, setSending] = useState(false)
  const sendingRef=useRef(false)
  const [failedMessage,setFailedMessage]=useState<{id:string|null;text:string}|null>(null)
  const [findOpen,setFindOpen]=useState(false)
  const [find,setFind]=useState<{query:string;id:string|number|null}>({query:'',id:null})
  const [actionBusy, setActionBusy] = useState<'resume' | 'cancel' | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [variant, setVariant] = useState(0)
  const [newMode, setNewMode] = useState<Mode>('review')
  const [modeBusy, setModeBusy] = useState(false)
  const [newAccount, setNewAccount] = useState<string | null>(null)
  const [accountBusy, setAccountBusy] = useState(false)
  const [accountsBusy, setAccountsBusy] = useState(true)
  const [plan, setPlan] = useState<PublishPlan | null>(null)
  const [planBusy, setPlanBusy] = useState(false)
  const [planError, setPlanError] = useState('')
  const [publishBusy, setPublishBusy] = useState(false)
  const publishing = useRef(false)
  const [mediaBusy,setMediaBusy]=useState(false)
  const [confirmPlan, setConfirmPlan] = useState<PublishReview | null>(null)
  const reviewSerial = useRef(0)
  const [reviewReturn,setReviewReturn]=useState<ReviewReturn|null>(null)
  const reviewReturnTarget=useRef(reviewReturn)
  const returnRequest=useRef(0)
  const [returningReview,setReturningReview]=useState(false)
  const [loadingReviewSettings,setLoadingReviewSettings]=useState(false)
  const [returnError,setReturnError]=useState('')
  const [editOpen, setEditOpen] = useState<{job:OperationJob;plan:PublishPlan|null;variant:number}|null>(null)
  const prepared = useRef(new Set<string>())
  const [historyOpen, setHistoryOpen] = useState(false)
  const closeHistory = useCallback(() => setHistoryOpen(false), [])
  const [sessionsCollapsed, setSessionsCollapsed] = useState(() => localStorage.getItem('operations:sessions-collapsed') === 'true')
  const [previewCollapsed, setPreviewCollapsed] = useState(() => localStorage.getItem('operations:preview-collapsed') === 'true')
  const [reportOpen, setReportOpen] = useState(false)
  const [qualityTarget,setQualityTarget]=useState<{jobId:string;variant?:number;report?:ProductQualityReport}|null>(null)
  const [revisionTarget,setRevisionTarget]=useState<{jobId:string;revision?:number}|null>(null)
  const [batchReviewOpen,setBatchReviewOpen]=useState(false)
  const [reviewBatch,setReviewBatch]=useState<string>()
  const openBatchReview=(id?:string)=>{setReviewBatch(id);setBatchReviewOpen(true)}
  const [calendarTarget,setCalendarTarget]=useState<{batch?:string;start?:string;jobIds?:string[];report?:PublicationCalendarReport;adjustment?:ScheduleAdjustment}|null>(null)
  const [accountCheckTarget,setAccountCheckTarget]=useState<AuditEditorTarget|null>(null)
  const [attentionTarget,setAttentionTarget]=useState<{batch?:string;report?:AttentionReport;review?:RepairReview}|null>(null)
  const [workspaceTab,setWorkspaceTab]=useState<WorkspaceTab|null>(null)
  const [workspaceReport,setWorkspaceReport]=useState<ProductHubReport>()
  const openWorkspace=(tab:WorkspaceTab='products')=>{setHistoryOpen(false);setWorkspaceReport(undefined);setWorkspaceTab(tab)}
  const [performanceOpen,setPerformanceOpen]=useState(false)
  const [performanceReports,setPerformanceReports]=useState<PerformanceReport[]>()
  const [optimizationProposal,setOptimizationProposal]=useState<OptimizationProposal>()
  const [previewOpen, setPreviewOpen] = useState(false)
  const [distributionOpen,setDistributionOpen]=useState(false)
  const [distributionSelection,setDistributionSelection]=useState<DistributionSelection|null>(null)
  const [progressOpen,setProgressOpen]=useState(false)
  const [deliveryOpen,setDeliveryOpen]=useState<'select'|'create'|null>(null)
  const [settingsOpen, setSettingsOpen] = useState<'default' | 'task' | null>(null)
  const [profile, setProfile] = useState<OperationProfile | null>(null)
  const [options, setOptions] = useState<PublishOptions>(emptyOptions)
  const [height, setHeight] = useState(650)
  const root = useRef<HTMLDivElement>(null)
  const previewDialog = useRef<HTMLDivElement>(null)
  const feed = useRef<HTMLDivElement>(null)
  const feedContent = useRef<HTMLDivElement>(null)
  const planRequest = useRef(0)
  const reload = useRef<() => Promise<void>>(async () => {})
  reload.current=data.reload
  const lastJobKey=owner?`operations:last-job:${owner}`:null
  const contextBlocked=!selectionReady||Boolean(currentId&&(!job||job.id!==currentId||data.sessionError))
  const historicalJob=job&&!jobs.some(row=>row.id===job.id)&&!data.sessionError?.unavailable?job:null
  const sidebarJobs=historicalJob?[historicalJob,...jobs]:jobs
  const mode = job?.parameters?.publish_mode || newMode
  const active = job?.status === 'queued' || job?.status === 'running'
  const publication = job?.result?.publication || job?.publication
  const conversationEvents=[...events,...optimistic.filter(entry=>!events.some(event=>event.kind==='user'&&event.data.text===entry.data.text&&Date.parse(event.created_at)>=Date.parse(entry.created_at)-2000))]
  const reading=useConversationReading({owner,currentId,feed,content:feedContent,events:conversationEvents,
    ready:Boolean(job&&job.id===currentId&&data.historyReady&&!data.sessionError?.unavailable),searching:findOpen})
  const publicationLocked = ['published', 'publishing', 'unknown'].includes(publication?.status || '')
  const reviewReadOnly=contextBlocked||active||publicationLocked||['published','publish_unknown'].includes(job?.status||'')
  const reviewStale=Boolean(confirmPlan&&(!job||publishSourceKey(job,variant)!==publishSourceKey(confirmPlan.job,confirmPlan.variant)
    ||(plan&&publishPlanKey(plan)!==publishPlanKey(confirmPlan.plan))))
  const availableAccounts = options.accounts.filter(account => account.active)
  const uniqueAccount = availableAccounts.length === 1 ? availableAccounts[0].id : ''
  const accountId = (currentId ? (publicationLocked ? publication?.account_id : null) || job?.parameters?.publish_account_id || plan?.settings.publish_account_id || uniqueAccount : newAccount ?? profile?.publish_account_id ?? uniqueAccount) || ''
  const selectedAccount = options.accounts.find(account => account.id === accountId)
  const accountName = selectedAccount?.label || accountId
  const refreshAccounts = useCallback(async () => {
    setAccountsBusy(true)
    try { setOptions(await getPublishOptions()) }
    catch(reason) { setError(errorText(reason)) }
    finally { setAccountsBusy(false) }
  }, [])

  useEffect(() => {
    void refreshAccounts()
    const focus = () => { void refreshAccounts() }
    window.addEventListener('focus', focus)
    return () => window.removeEventListener('focus', focus)
  }, [refreshAccounts])

  useEffect(() => {
    let disposed = false
    void getOperationProfile().then(value => { if (!disposed) { setProfile(value); setNewMode(value.publish_mode) } }).catch(() => {})
    return () => { disposed = true }
  }, [])

  useEffect(() => {
    const measure = () => {
      const top = root.current?.getBoundingClientRect().top || 125
      const main = root.current?.closest('main')
      const bottom = main ? Number.parseFloat(getComputedStyle(main).paddingBottom) : 18
      setHeight(Math.max(220, (window.visualViewport?.height || window.innerHeight) - top - bottom))
    }
    window.scrollTo(0, 0)
    measure()
    const observer = new ResizeObserver(measure)
    if (root.current?.parentElement) observer.observe(root.current.parentElement)
    window.addEventListener('resize', measure)
    window.visualViewport?.addEventListener('resize', measure)
    return () => { observer.disconnect(); window.removeEventListener('resize', measure); window.visualViewport?.removeEventListener('resize', measure) }
  }, [])

  const refreshPlan = useCallback(async () => {
    if (selectedId.current !== currentId) return
    const request = ++planRequest.current
    if (!currentId||!job||job.id!==currentId||contextBlocked) { setPlan(null); setPlanBusy(false); return }
    setPlanBusy(true); setPlanError('')
    try {
      const next = await getPublishPlan(currentId, variant)
      if (request === planRequest.current) { setPlan(next); return next }
    } catch (reason) { if (request === planRequest.current) { setPlan(null); setPlanError(errorText(reason)) } }
    finally { if (request === planRequest.current) setPlanBusy(false) }
  }, [currentId, variant, job?.id, contextBlocked])
  useEffect(() => { setPlan(null); void refreshPlan(); return () => { planRequest.current++ } }, [refreshPlan, job])
  useEffect(()=>{setVariant(job?.parameters?.publish_variant_index||0)},[job?.id,job?.parameters?.publish_variant_index])

  useEffect(() => {
    if (contextBlocked || !job?.result?.draft || (job.material_id&&job.result.category) || active || publicationLocked || ['failed','cancelled'].includes(job.status) || prepared.current.has(job.id)) return
    prepared.current.add(job.id)
    void prepareOperation(job.id).then(async () => { signature.current = ''; await reload.current() }).catch(reason => setError(errorText(reason)))
  }, [job, active, publicationLocked, contextBlocked])

  useEffect(() => {
    if(!previewOpen)return
    const previous=document.activeElement as HTMLElement|null
    const overflow=document.body.style.overflow
    document.body.style.overflow='hidden'
    previewDialog.current?.focus()
    return () => { document.body.style.overflow=overflow; previous?.focus() }
  }, [previewOpen])

  const changed = async () => { signature.current = ''; await reload.current(); await refreshPlan() }
  const openFind=()=>{reading.pause();setFindOpen(true);requestAnimationFrame(()=>document.getElementById('operation-conversation-find')?.focus())}
  const closeFind=()=>{setFindOpen(false);setFind({query:'',id:null});document.getElementById('operation-message')?.focus()}
  const locateMessage=(id:string|number)=>{
    requestAnimationFrame(()=>reading.locate(id))
  }

  const select = (id: string) => {
    setDeliveryOpen(null)
    setDistributionOpen(false)
    setDistributionSelection(null)
    if (id === currentId) { setHistoryOpen(false); return }
    setProgressOpen(false)
    clearReviewReturn();setSettingsOpen(null)
    reading.capture()
    signature.current = ''; planRequest.current++; setSelectionReady(true);data.clearIssues()
    rememberConversation(lastJobKey,id)
    selectedId.current=id;setFindOpen(false);setFind({query:'',id:null})
    setConfirmPlan(null); setEditOpen(null); setVariant(0); setCurrentId(id); setJob(null); setEvents([]); setOptimistic([]); setPlan(null); setError(''); setNotice(''); setHistoryOpen(false)
  }
  const fresh = () => {
    setProgressOpen(false)
    clearReviewReturn();setSettingsOpen(null)
    setDeliveryOpen(null)
    setDistributionOpen(false)
    setDistributionSelection(null)
    reading.capture()
    setSelectionReady(true); signature.current = ''; planRequest.current++;data.clearIssues()
    rememberConversation(lastJobKey,'new')
    selectedId.current=null;setFindOpen(false);setFind({query:'',id:null})
    setConfirmPlan(null); setEditOpen(null); setCurrentId(null); setJob(null); setEvents([]); setOptimistic([]); setVariant(0); setPlan(null); setError(''); setNotice(''); setHistoryOpen(false)
  }
  useEffect(()=>{
    if(selectionReady||!owner)return
    const saved=savedConversation(lastJobKey)
    if(saved==='new'){setSelectionReady(true);return}
    if(saved){select(saved);return}
    if(!data.listReady)return
    const recent=jobs.find(row=>!['cancelled','failed'].includes(row.status))||jobs[0]
    if(recent)select(recent.id)
    else setSelectionReady(true)
  },[selectionReady,owner,lastJobKey,data.listReady,jobs])
  const send = async (event?: FormEvent,command?:string) => {
    event?.preventDefault()
    const message = (command??text).trim()
    if (!message || sendingRef.current || accountBusy || accountsBusy || contextBlocked) return
    sendingRef.current=true
    setSending(true); setError(''); setNotice('');setFailedMessage(null)
    if(command===undefined)drafts.clear(currentId)
    if(findOpen)closeFind()
    reading.jumpLatest()
    setOptimistic([{ id: `pending-${crypto.randomUUID()}`, kind: 'user', name: '用户消息', status: 'success', created_at: new Date().toISOString(), data: { text: message } }])
    let accepted=false
    try {
      const response = await sendOperationMessage(message, currentId, crypto.randomUUID(), mode, accountId)
      accepted=true
      if(useAuthStore.getState().user?.user_id!==owner)return
      if (selectedId.current !== currentId) { setOptimistic([]); await reload.current(); return }
      signature.current = ''
      if(response.delivery_action||response.refresh_options){
        await refreshAccounts()
        if(response.job&&selectedId.current===currentId)setJob(response.job)
      }
      if(selectedId.current!==currentId)return
      if(response.download)await downloadOperationFile(response.download.job_id,response.download.name)
      if(selectedId.current!==currentId)return
      if(response.ui_action==='preview'&&(window.innerWidth<1280||previewCollapsed))setPreviewOpen(true)
      if(response.ui_action==='report')setReportOpen(true)
      if(response.ui_action==='quality'&&response.job){setPreviewOpen(false);setQualityTarget({jobId:response.job.id,variant:response.quality_report?.variant_index,report:response.quality_report})}
      if(response.ui_action==='revisions'&&response.job)setRevisionTarget({jobId:response.job.id,revision:response.revision_id})
      if(response.ui_action==='batch_review')openBatchReview(response.batch_id)
      if(response.ui_action==='calendar'){setConfirmPlan(null);setCalendarTarget({batch:response.batch_id,report:response.calendar_report,adjustment:response.schedule_adjustment})}
      if(response.ui_action==='account_check'&&response.job){setConfirmPlan(null);setAccountCheckTarget({jobId:response.job.id,kind:'account',accountId:response.job.result?.publication?.account_id,itemId:response.job.result?.publication?.item_id})}
      if(response.ui_action==='attention'){setConfirmPlan(null);setAttentionTarget({report:response.attention_report,review:response.repair_review})}
      if(response.ui_action==='products'||response.ui_action==='templates'||response.ui_action==='workshop'){setWorkspaceReport(response.product_report);setWorkspaceTab(response.ui_action)}
      if(response.ui_action==='performance'){setPerformanceReports(response.performance);setOptimizationProposal(response.proposal);setPerformanceOpen(true)}
      if(!response.job&&response.reply)setNotice(response.reply)
      if (response.job && response.job.id !== currentId) { if(currentId===null)drafts.transfer(currentId,response.job.id);select(response.job.id) }
      else {
        const persisted = currentId ? await getOperationEvents(currentId) : null
        if(selectedId.current!==currentId)return
        if (persisted) setEvents(persisted.events)
        setOptimistic([])
        await reload.current()
      }
    } catch (reason) {
      if(useAuthStore.getState().user?.user_id!==owner)return
      if(!accepted){
        const restored=command===undefined&&drafts.restore(currentId,message)
        if(!restored)setFailedMessage({id:currentId,text:message})
      }
      if (selectedId.current === currentId) {setError(accepted?'指令已处理，结果刷新暂未完成。请刷新查看当前任务，避免重复发送相同操作。':errorText(reason));setOptimistic([])}
      await reload.current()
    }
    finally {sendingRef.current=false;setSending(false)}
  }
  const action = async (name: 'resume' | 'cancel') => {
    if (!currentId || actionBusy || contextBlocked) return
    setActionBusy(name); setError(''); setNotice('')
    try {
      const response = await operationAction(currentId, name)
      if (selectedId.current === currentId) {
        setJob(response.job); setNotice(response.reply); signature.current = ''
        await reload.current()
      }
    } catch (reason) { if (selectedId.current === currentId) setError(errorText(reason)) }
    finally { setActionBusy(null) }
  }
  const download = async (name: string) => {
    if (!currentId) return
    try { await downloadOperationFile(currentId, name) }
    catch (reason) { setError(errorText(reason)) }
  }
  const openSettings = async (scope: 'default' | 'task') => {
    const selection=selectedId.current
    const target=reviewReturnTarget.current
    const request=target?++returnRequest.current:0
    if(target){setReturningReview(true);setLoadingReviewSettings(Boolean(target.distribution));setReturnError('')}
    try {
      const [nextProfile, nextOptions, sharedPlan] = await Promise.all([getOperationProfile(), getPublishOptions(),target?.distribution?getPublishPlan(target.jobId,target.variant):Promise.resolve(null)])
      if(selectedId.current!==selection||useAuthStore.getState().user?.user_id!==owner||(target&&request!==returnRequest.current))return
      if(target){setReturningReview(false);setLoadingReviewSettings(false)}
      if(sharedPlan){planRequest.current++;setPlan(sharedPlan);setPlanError('')}
      setProfile(nextProfile); setOptions(nextOptions); setSettingsOpen(scope)
    } catch (reason) { if(selectedId.current===selection&&(!target||request===returnRequest.current)){setError(errorText(reason));if(target?.distribution)setReturnError(errorText(reason));else if(target)void resumeReview()} }
  }
  const changeAccount = async (next: string) => {
    if (active || publicationLocked || accountBusy || sending || publishBusy || contextBlocked) return
    setError(''); setNotice(''); setConfirmPlan(null)
    if (!currentId) { setNewAccount(next); return }
    setAccountBusy(true); setPlan(null); planRequest.current++
    try {
      const response = await configureOperationPublish(currentId, { publish_mode: mode, publish_account_id: next || null })
      if (selectedId.current === currentId) {
        setJob(response.job); signature.current = ''; await reload.current()
        if(selectedId.current!==currentId)return
        const chosen = options.accounts.find(account => account.id === next)
        setNotice(next ? `本次商品将发布到「${chosen?.label || next}」，AI回复和自动发货也关联此账号。` : '请在发布前选择目标账号。')
      }
    } catch(reason) { if (selectedId.current === currentId) setError(errorText(reason)) }
    finally { setAccountBusy(false) }
  }
  const changeMode = async (next: Mode) => {
    if(contextBlocked)return
    if (next === 'automatic' && !accountId) { setError('请先选择本次发布账号，再使用自动模式'); return }
    if (!currentId) { setNewMode(next); return }
    if (active || publicationLocked || modeBusy) return
    setModeBusy(true); setError('')
    try {
      await configureOperationPublish(currentId, { ...plan?.settings, publish_account_id: accountId || null, publish_mode: next })
      if(selectedId.current!==currentId)return
      signature.current = ''; await reload.current()
      if(selectedId.current!==currentId)return
      setNotice(next === 'automatic' ? '自动模式已启用：下次生成完成且条件齐备后直接发布。当前草稿可在预览中审核发布。' : '已切换审核模式：生成后等你检查并点击发布。')
    } catch (reason) { if(selectedId.current===currentId)setError(errorText(reason)) }
    finally { setModeBusy(false) }
  }
  const publish = async () => {
    if (!currentId || !confirmPlan?.plan?.ready || !confirmPlan.plan.fingerprint || !plan?.ready || !plan.preview || !plan.fingerprint || reviewStale || reviewReadOnly || planBusy || planError || publishing.current || accountBusy || accountsBusy || mediaBusy) return
    publishing.current=true;setPublishBusy(true); setError('')
    try {
      const response = await publishOperation(currentId, confirmPlan.plan.fingerprint, confirmPlan.variant)
      if (selectedId.current !== currentId) return
      setConfirmPlan(null); setNotice(response.reply); signature.current = ''; await reload.current()
      if(selectedId.current===currentId)setProgressOpen(true)
    } catch (reason) { if (selectedId.current === currentId) { setConfirmPlan(null); setPlanError(errorText(reason)); await refreshPlan(); setError(errorText(reason)) } }
    finally { publishing.current=false;setPublishBusy(false) }
  }
  const prepareDraft=async()=>{
    if(!currentId||active||publicationLocked||contextBlocked)return
    try{setError('');await prepareOperation(currentId);if(selectedId.current===currentId)await changed()}catch(reason){if(selectedId.current===currentId)setError(errorText(reason))}
  }
  const addVideos=async()=>{
    if(!currentId||mediaBusy||active||publicationLocked||contextBlocked)return
    setMediaBusy(true);setError('')
    try{const response=await addOperationVideos(currentId);if(selectedId.current!==currentId)return;setNotice(response.reply);await changed()}catch(reason){if(selectedId.current===currentId)setError(errorText(reason))}finally{setMediaBusy(false)}
  }
  const publishSettings = plan?.settings || { publish_mode: mode }
  const openEditor=(index=variant)=>{if(job){setPreviewOpen(false);setEditOpen({job,plan:index===variant?plan:null,variant:index})}}
  const openDelivery=()=>{setPreviewOpen(false);setSettingsOpen(null);setConfirmPlan(null);setDeliveryOpen('select')}
  const openProgress=()=>{setPreviewOpen(false);setProgressOpen(true)}
  const openDistribution=()=>{clearReviewReturn();setPreviewOpen(false);setDistributionSelection(null);setDistributionOpen(true)}
  const editDistribution=(kind:DistributionEditor,selection:DistributionSelection)=>{
    if(!job||contextBlocked||active||publicationLocked||job.status==='scheduled'||job.parameters?.workflow_hold)return
    const target:ReviewReturn={jobId:job.id,variant,distribution:selection}
    clearReviewReturn();reviewReturnTarget.current=target;setReviewReturn(target)
    setDistributionSelection(selection);setDistributionOpen(false)
    if(kind==='listing')setEditOpen({job,plan:null,variant})
    else if(kind==='settings')void openSettings('task')
    else openDelivery()
  }
  const deliveryName=options.cards?.find(card=>card.id===job?.parameters?.delivery_card_id)?.name||plan?.automation?.delivery_name||(job?.parameters?.delivery_text?'商品专属交付内容':'待选择')
  const reviewSnapshot=(next:PublishPlan|null,source=job!,index=variant):PublishReview=>({plan:next,job:source,variant:index,
    accountName:next?.seller?.name||options.accounts.find(account=>account.id===source.parameters?.publish_account_id)?.label||'',
    deliveryName:next?.automation?.delivery_name||options.cards?.find(card=>card.id===source.parameters?.delivery_card_id)?.name||'待选择',revision:++reviewSerial.current})
  const clearReviewReturn=()=>{returnRequest.current++;reviewReturnTarget.current=null;setReviewReturn(null);setReturningReview(false);setLoadingReviewSettings(false);setReturnError('')}
  const openReview=()=>{if(job&&!contextBlocked){clearReviewReturn();setPreviewOpen(false);setConfirmPlan(reviewSnapshot(plan))}}
  const resumeReview=async()=>{
    const target=reviewReturnTarget.current
    if(!target||selectedId.current!==target.jobId)return
    const request=++returnRequest.current
    const valid=()=>request===returnRequest.current&&selectedId.current===target.jobId&&useAuthStore.getState().user?.user_id===owner
    setReturningReview(true);setLoadingReviewSettings(false);setReturnError('')
    try{
      const latest=await getOperationJob(target.jobId)
      if(!valid())return
      if(latest.id!==target.jobId)throw new Error('读取的商品与当前审核任务不一致，请重新读取')
      const index=latest.result?.draft?.variants[target.variant]?target.variant:latest.parameters?.publish_variant_index||0
      if(target.distribution){
        planRequest.current++;signature.current='';data.clearIssues();setJob(latest);setVariant(index);setPlan(null);setPlanError('')
        setDistributionSelection(target.distribution);setDistributionOpen(true);clearReviewReturn();return
      }
      const next=await getPublishPlan(target.jobId,index)
      if(!valid())return
      planRequest.current++;signature.current='';setJob(latest);setVariant(index);setPlan(next);setPlanError('')
      setConfirmPlan(reviewSnapshot(next,latest,index));clearReviewReturn()
    }catch(reason){if(valid())setReturnError(errorText(reason))}
  }
  const finishReviewStep=(close:()=>void)=>{close();if(reviewReturnTarget.current)void resumeReview()}
  const refreshReview=async()=>{
    if(!confirmPlan||!job)return
    const revision=confirmPlan.revision
    const next=await refreshPlan()
    if(next&&selectedId.current===job.id){
      const snapshot=reviewSnapshot(next)
      setConfirmPlan(previous=>previous?.revision===revision?snapshot:previous)
    }
  }
  const leaveReview=(action:()=>void)=>{if(confirmPlan){const target={jobId:confirmPlan.job.id,variant:confirmPlan.variant};reviewReturnTarget.current=target;setReviewReturn(target)}setConfirmPlan(null);setPreviewOpen(false);action()}
  const reviewLabel=reviewReturn?.distribution?'同步审核':'发布总览'
  const renderPreview = (expanded = false) => <ProductPreview readOnly={contextBlocked} expanded={expanded} onExpand={() => setPreviewOpen(true)} onClose={expanded ? () => setPreviewOpen(false) : undefined} job={job} plan={plan} planBusy={planBusy} planError={planError} variant={variant} onVariant={value => { setPlan(null); setVariant(value) }} publishBusy={publishBusy || accountBusy || accountsBusy || mediaBusy || contextBlocked} onPublish={openReview} onEdit={()=>openEditor()} onProgress={openProgress} onSettings={() => void openSettings('task')} onRefresh={() => void refreshPlan()} onDistribute={openDistribution} onDelivery={openDelivery} deliveryName={deliveryName} onQuality={()=>{if(job){setPreviewOpen(false);setQualityTarget({jobId:job.id,variant})}}} onPrepare={()=>void prepareDraft()} onAddVideo={()=>void addVideos()} mediaBusy={mediaBusy}/>
  const result = job?.result

  const toggleSessions = () => setSessionsCollapsed(previous => { localStorage.setItem('operations:sessions-collapsed', String(!previous)); return !previous })
  const togglePreview = () => {
    if (window.innerWidth < 1280) { setPreviewOpen(true); return }
    setPreviewCollapsed(previous => { localStorage.setItem('operations:preview-collapsed', String(!previous)); return !previous })
  }

  return <div ref={root} style={{ height }} className="operations-workspace" data-testid="product-operations" onKeyDown={event=>{
    if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='f'&&currentId&&!(event.target as HTMLElement).closest('[role="dialog"]')){event.preventDefault();openFind()}
  }}>
    <div className="flex min-h-0 flex-1">
      <SessionSidebar jobs={sidebarJobs} historicalId={historicalJob?.id} readError={data.listError} readBusy={data.listBusy} retry={()=>void data.retryList()} accounts={options.accounts} draftIds={drafts.draftIds} currentId={currentId} select={select} fresh={fresh} disabled={sending} mobileOpen={historyOpen} close={closeHistory} collapsed={sessionsCollapsed} settings={() => void openSettings('default')} workspace={() => openWorkspace()} attention={()=>{setHistoryOpen(false);setAttentionTarget({})}} performance={() => { setPerformanceReports(undefined); setOptimizationProposal(undefined); setPerformanceOpen(true) }} />
      <section className="operations-conversation" aria-label="运营对话">
        <header className="operations-chat-header">
          <button className="shell-icon-button hidden lg:flex" onClick={toggleSessions} title={sessionsCollapsed ? '展开会话侧栏' : '收起会话侧栏'} aria-label={sessionsCollapsed ? '展开会话侧栏' : '收起会话侧栏'} aria-expanded={!sessionsCollapsed}><PanelLeft size={17} /></button>
          <button className="shell-icon-button lg:hidden" onClick={() => setHistoryOpen(true)} title="打开会话列表" aria-label="打开会话列表"><PanelLeft size={17} /></button>
          <div className="operations-chat-title min-w-0 flex-1">
            <h1 className="truncate text-[14px] font-semibold">{job?.keyword || (currentId ? '读取商品会话' : '商品运营助手')}</h1>
            <p className="mt-1 flex items-center gap-1.5 text-[10px] text-slate-400">{active ? <Loader2 size={10} className="animate-spin" /> : <span className={cn('h-1.5 w-1.5 rounded-full', job?.status === 'published' ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-slate-500')} />}{data.sessionError ? '会话读取待恢复' : job ? statuses[job.status] : !selectionReady||currentId ? '正在恢复当前工作区' : '研究、创作与发布，一个对话完成'}</p>
          </div>
          <div className="operations-chat-tools flex shrink-0 items-center gap-1">
            <button className="shell-icon-button" disabled={!currentId} aria-label="查找当前对话" title="查找当前对话 · Ctrl/⌘ F" onClick={openFind}><Search size={16}/></button>
            <button className="shell-icon-button hidden sm:flex" disabled={!job||contextBlocked} onClick={()=>job&&setQualityTarget({jobId:job.id,variant})} title="商品发布前检查" aria-label="商品发布前检查"><ClipboardCheck size={17}/></button>
            <button className="shell-icon-button" disabled={!job||contextBlocked} onClick={()=>job&&setRevisionTarget({jobId:job.id})} title="商品版本记录" aria-label="商品版本记录"><History size={17}/></button>
            <button className="shell-icon-button" onClick={()=>setCalendarTarget({})} title="发布日历" aria-label="发布日历"><CalendarClock size={17}/></button>
            <button className="shell-icon-button hidden sm:flex" onClick={()=>setAttentionTarget({})} title="运营待办" aria-label="运营待办"><ClipboardList size={17}/></button>
            <button className="shell-icon-button" onClick={()=>openWorkspace()} title="商品工作台" aria-label="商品工作台"><LayoutGrid size={17}/></button>
            <button className="shell-icon-button lg:hidden" onClick={fresh} disabled={sending} title="新建对话" aria-label="新对话"><Plus size={17} /></button>
            <button className="shell-icon-button" disabled={contextBlocked} onClick={() => void openSettings(job ? 'task' : 'default')} title={job ? '当前商品设置' : '默认经营设置'} aria-label="经营设置"><Settings2 size={17} /></button>
            <button className={cn('shell-icon-button', !previewCollapsed && 'xl:bg-slate-100 dark:xl:bg-slate-700')} onClick={togglePreview} title="切换商品预览" aria-label="切换商品预览"><PanelRight size={17} /></button>
          </div>
        </header>
        <div className="operations-context-bar">
          <label className="operation-account-picker"><span className="shrink-0 text-slate-400">账号</span><select aria-label="本次发布账号" value={accountId} disabled={active || publicationLocked || accountBusy || accountsBusy || modeBusy || sending || publishBusy || contextBlocked} onChange={event => void changeAccount(event.target.value)}>
            <option value="">{accountsBusy ? '读取账号中…' : availableAccounts.length > 1 ? '选择发布账号' : '请先登录账号'}</option>
            {accountId && !options.accounts.some(account => account.id === accountId) && <option value={accountId}>{accountId} · 待确认</option>}
            {options.accounts.map(account => <option key={account.id} value={account.id} disabled={!account.active}>{account.label}（{account.id}）{!account.active ? ' · 未登录或未启用' : ''}</option>)}
          </select><button type="button" aria-label="刷新账号列表" title="刷新账号列表" disabled={accountsBusy || accountBusy} onClick={() => void refreshAccounts()} className="shrink-0 rounded p-1 text-slate-400 hover:text-slate-700">{accountsBusy || accountBusy ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}</button></label>
          <label className={cn('operation-mode-picker', mode === 'automatic' && 'is-automatic')}>{mode === 'automatic' ? <Zap size={12} /> : <CheckCheck size={13} />}<select aria-label="发布模式" value={mode} disabled={active || publicationLocked || modeBusy || accountBusy || accountsBusy || sending || contextBlocked} onChange={event => void changeMode(event.target.value as Mode)}><option value="review">审核后发布</option><option value="automatic">自动生成并发布</option></select>{modeBusy && <Loader2 size={11} className="animate-spin" />}</label>
          <div className="ml-auto flex items-center gap-2">{job && ['failed', 'cancelled'].includes(job.status) && <button disabled={!!actionBusy || accountBusy || contextBlocked} className="flex items-center gap-1 whitespace-nowrap text-[11px] text-blue-600 disabled:opacity-50" onClick={() => void action('resume')}>{actionBusy === 'resume' && <Loader2 size={11} className="animate-spin" />}继续任务</button>}{result?.analysis && <button className="flex items-center gap-1 whitespace-nowrap text-[11px] text-slate-500 hover:text-blue-600" onClick={() => setReportOpen(true)} title="查看研究结果"><FileText size={13} /><span className="hidden sm:inline">研究报告</span></button>}</div>
        </div>
        {findOpen&&currentId&&<ConversationFind key={currentId} events={events} change={(query,id)=>setFind({query,id})} locate={locateMessage} close={closeFind}/>}
        <div className="operations-thread">
        <div ref={feed} className="operations-feed" data-testid="conversation-feed" onScroll={reading.onScroll}>
          <div ref={feedContent} className="mx-auto w-full max-w-[760px] px-5 pb-7 pt-3 sm:px-8">
            {!job && !currentId && selectionReady && <div className="operations-welcome">
              <span className="welcome-mark"><Sparkles size={27} strokeWidth={1.5} /></span><p className="mb-3 mt-6 text-[10px] font-medium tracking-[0.18em] text-slate-400">你的商品运营工作空间</p>
              <h2 className="text-[25px] font-semibold tracking-tight sm:text-[28px]">下一款好商品，从这里开始</h2>
              <p className="mx-auto mt-4 max-w-md text-[13px] leading-7 text-slate-400">告诉我商品关键词与素材路径，<br className="hidden sm:block" />我会研究市场、准备文案和定价，交给你审核发布。</p>
              <div className="welcome-suggestions">
                {[{ icon: Search, title: '研究热卖商品', description: '采集竞品，找到文案与定价思路', text: '采集“AI口播源码”的Hot100' }, { icon: FolderOpen, title: '用素材生成商品', description: '读取文件夹，准备完整商品方案', text: '我的素材文件夹是：' }, { icon: Timer, title: '安排发布计划', description: '为多件商品设置发布时间', text: '帮我安排批量定时发布，素材文件夹和时间：' }, { icon: MessageSquare, title: '了解对话能力', description: '卡券、账号、发货，都可以直接说', text: '对话功能' }].map(item => <button key={item.title} onClick={() => { setText(item.text); document.getElementById('operation-message')?.focus() }}><item.icon size={17} strokeWidth={1.6} /><strong>{item.title}</strong><span>{item.description}</span></button>)}
              </div>
            </div>}
            {(!selectionReady||currentId&&!job)&&!data.sessionError&&<p className="flex items-center gap-2 py-8 text-xs text-slate-400" role="status"><Loader2 size={14} className="animate-spin" />{selectionReady?'正在读取对话…':'正在恢复上次的工作区…'}</p>}
            {data.sessionError&&<section className="conversation-read-notice" role="alert" aria-label="会话读取状态"><h2>{data.sessionError.unavailable?'该会话暂不可用':'商品读取暂未完成'}</h2><p>{data.sessionError.unavailable?'该商品可能已移除，或不属于当前登录用户。请选择其他商品，也可以重试读取。':'已保留当前输入和已读取内容。后台任务继续执行，重新读取后即可接着操作。'}</p><div><button disabled={data.sessionBusy} onClick={()=>void data.retrySession()}>{data.sessionBusy?<Loader2 size={13} className="animate-spin"/>:<RefreshCw size={13}/>}重新读取会话</button>{data.sessionError.unavailable&&<button onClick={()=>openWorkspace()}>打开商品工作台</button>}<button onClick={fresh}>新建对话</button></div></section>}
            {data.historyError&&job&&<section className="conversation-read-notice" role="status" aria-label="消息记录读取状态"><h2>消息记录刷新暂未完成</h2><p>商品和输入草稿已保留，可以继续操作；历史消息正在自动重试读取。</p><button disabled={data.sessionBusy} onClick={()=>void data.retrySession()}><RefreshCw size={13}/>重新读取消息记录</button></section>}
            {!contextBlocked&&(job?.parameters?.schedule_batches?.length || job?.parameters?.schedule_batch_id) && <ScheduleBoard batchId={job.parameters.schedule_batches?.slice(-1)[0] || job.parameters.schedule_batch_id!} onOpen={select} onCommand={value => setText(value)} onReview={openBatchReview} onCalendar={batch=>setCalendarTarget({batch})} />}
            {currentId&&job&&!contextBlocked&&<WorkflowCard key={currentId} jobId={currentId} revision={job.updated_at||notice} onOpen={select} changed={changed}/>}
            <Conversation query={find.query} activeId={find.id} events={conversationEvents} />
            {job && active && <div className="assistant-working" role="status"><span className="working-indicator"><i /><i /><i /></span><span>{publication?.status === 'publishing' ? '正在发布商品并关联回复、发货…' : '正在准备商品，完成后给你完整结果…'}</span></div>}
            {job && !active && job.outcome && !events.some(event => event.kind === 'assistant' && event.data.outcome) && <div className="my-7"><Answer text={job.outcome} /></div>}
            {job && !active && job.error && !job.outcome?.includes(job.error) && !events.some(event => event.kind === 'assistant' && event.data.text?.includes(job.error!)) && <p className="my-4 text-xs leading-6 text-amber-700">{job.error}</p>}
            {result?.draft && !active && <button className="my-3 flex items-center gap-1.5 text-xs text-blue-600 xl:hidden" onClick={() => setPreviewOpen(true)}><Eye size={13} />查看商品预览</button>}
            {job && (result?.draft||publication) && <PublicationResults key={job.id} job={job} options={options} open={progressOpen} show={openProgress} close={()=>setProgressOpen(false)} disabled={contextBlocked||publishBusy} changed={changed} onOpen={id=>{setProgressOpen(false);select(id)}} onQuality={id=>{setProgressOpen(false);setQualityTarget({jobId:id})}} onCalendar={id=>{setProgressOpen(false);setCalendarTarget({jobIds:[id]})}} onAttention={()=>{setProgressOpen(false);setAttentionTarget({})}} />}
            {sending && <p className="my-4 flex items-center gap-2 text-xs text-slate-400"><Loader2 size={14} className="animate-spin" />正在处理你的消息…</p>}
          </div>
        </div>
        {reading.away&&currentId&&job&&<div className="conversation-navigation" role="group" aria-label="对话阅读导航">
          {reading.unread>0&&<button className="has-unread" onClick={reading.jumpUnread} aria-label={`查看${reading.unread}条新回复`}><MessageSquare size={13}/><span aria-live="polite">{reading.unread} 条新回复</span></button>}
          <button onClick={()=>{if(findOpen)closeFind();reading.jumpLatest()}} aria-label="回到最新消息"><ArrowDown size={13}/>回到最新</button>
        </div>}
        </div>
        <div className="operations-composer">
          {currentId&&job&&!contextBlocked&&<PendingActionCard key={currentId} jobId={currentId} revision={(job.updated_at||'')+events.length+notice} disabled={sending||accountBusy||publishBusy||contextBlocked} onDelivery={openDelivery} done={async reply=>{setNotice(reply);await changed()}}/>}
          {(error || notice) && <div role={error ? 'alert' : 'status'} className={cn('mb-2 flex max-h-20 items-start gap-2 overflow-auto rounded-lg px-3 py-2 text-[11px] leading-5', error ? 'bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200' : 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-200')}><span className="flex-1">{error || notice}</span><button aria-label="关闭提示" className="shrink-0 pt-0.5" onClick={() => { setError(''); setNotice('') }}><X size={12} /></button></div>}
          {failedMessage&&failedMessage.id===currentId&&<details className="mb-2 max-h-28 overflow-auto rounded-lg bg-amber-50 px-3 py-2 text-[11px] leading-6 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200"><summary className="cursor-pointer">本次发送未完成，查看发送内容；输入框里的新草稿已保留</summary><p className="whitespace-pre-wrap break-words">{failedMessage.text}</p><button className="text-slate-500" onClick={()=>setFailedMessage(null)}>关闭发送记录</button></details>}
          <form className="composer-input" onSubmit={event => void send(event)}>
            <label className="sr-only" htmlFor="operation-message">运营指令</label><textarea id="operation-message" value={text} onChange={event => setText(event.target.value)} placeholder={job ? '继续修改文案、绑定卡券，或告诉我下一步…' : '描述你想卖的商品，或粘贴素材文件夹路径…'} maxLength={10000} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} />
            <div className="composer-toolbar"><div className="flex min-w-0 items-center gap-1">{job ? <button type="button" className="composer-shortcut disabled:opacity-40" disabled={contextBlocked} onClick={openDelivery} title={`发货卡券：${deliveryName}`} aria-label="发货卡券"><CreditCard size={14} /><span>发货卡券</span></button> : <span className="flex items-center gap-1.5 text-[10px] text-slate-400"><Sparkles size={13} />用对话完成运营</span>}<span className="hidden text-[10px] text-slate-400 2xl:inline">Enter 发送 · Shift+Enter 换行</span></div>
              <div className="ml-auto flex shrink-0 items-center gap-2">{active && !publicationLocked && <button type="button" disabled={!!actionBusy} onClick={() => void action('cancel')} className="composer-stop" aria-label="停止任务">{actionBusy === 'cancel' ? <Loader2 size={12} className="animate-spin" /> : <Square size={11} />}停止</button>}<button type="submit" disabled={sending || !text.trim() || accountBusy || accountsBusy || contextBlocked} className="composer-send" aria-label="发送">{sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}<span>发送</span></button></div>
            </div>
          </form>
          {drafts.hint&&<p className="mt-1.5 text-right text-[9px] text-slate-400" role="status">{drafts.hint}</p>}
          <p className="composer-note">{job?.publish_at ? '定时发布按北京时间执行，请保持项目运行与账号在线。' : accountId ? `${mode === 'automatic' ? '自动模式 · 生成完成后发布到' : '审核模式 · 发布到'}「${accountName}」` : '先研究，再完善。发布前选择账号并审核商品内容。'}</p>
        </div>
      </section>
      <aside className={cn('operations-preview', previewCollapsed && 'is-collapsed')}>{renderPreview()}</aside>
    </div>
    {previewOpen && <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 sm:p-3" onClick={() => setPreviewOpen(false)}>
      <div ref={previewDialog} tabIndex={-1} className="flex h-[100dvh] w-full max-w-[480px] flex-col overflow-hidden bg-white outline-none sm:h-[calc(100dvh-24px)] sm:rounded-2xl dark:bg-slate-800" role="dialog" aria-modal="true" aria-label="商品预览面板" onClick={event => event.stopPropagation()} onKeyDown={event => { if(event.key==='Escape'){ event.stopPropagation(); setPreviewOpen(false) } }}>
        {renderPreview(true)}
      </div>
    </div>}
    {reportOpen && job && <Dialog title="研究与商品方案" close={() => setReportOpen(false)}><div className="space-y-5 text-xs leading-7">
      <div className="flex flex-wrap gap-2">{job.artifacts?.filter(name => name.endsWith('.zip') || name.endsWith('.csv') || name.endsWith('.md')).map(name => <button key={name} className={buttonClass} onClick={() => void download(name)}><Download size={13} />{name}</button>)}</div>
      <p className="whitespace-pre-wrap">{result?.analysis?.summary}</p>
      {[
        ['竞品文案分析', result?.analysis], ['商品事实与来源', result?.facts], ['定位与主图方案', result?.draft], ['定价依据', result?.price],
      ].map(([label, value]) => value && <details key={String(label)} className="rounded-xl border border-slate-200 p-3 dark:border-slate-700"><summary className="cursor-pointer font-medium">{String(label)}</summary><div className="mt-3"><Evidence value={value as Parameters<typeof Evidence>[0]['value']} /></div></details>)}
      {!!result?.ranking?.hot100?.length && <details className="rounded-xl border border-slate-200 p-3 dark:border-slate-700"><summary className="cursor-pointer font-medium">采样热度榜 · {job.hot_count} 件</summary><p className="my-3 text-slate-400">{result.ranking.ranking_note}</p><div className="overflow-auto"><table className="w-full text-left"><thead><tr>{['排名', '商品', '标价', '想要'].map(label => <th className="p-2" key={label}>{label}</th>)}</tr></thead><tbody>{result.ranking.hot100.map(item => <tr key={item.item_id} className="border-t border-slate-100 dark:border-slate-700"><td className="p-2">{item.rank}</td><td className="min-w-[180px] p-2"><a className="text-primary-600" href={`https://www.goofish.com/item?id=${encodeURIComponent(item.item_id)}`} target="_blank" rel="noopener noreferrer">{item.title}</a></td><td className="p-2">{item.price ?? '—'}</td><td className="p-2">{item.want_count ?? '缺失'}</td></tr>)}</tbody></table></div></details>}
    </div></Dialog>}
    {qualityTarget&&<QualityPanel key={qualityTarget.jobId} jobId={qualityTarget.jobId} variant={qualityTarget.variant} initial={qualityTarget.report} close={()=>setQualityTarget(null)} action={value=>{if(qualityTarget.variant!=null)setVariant(qualityTarget.variant);setQualityTarget(null);if(value==='settings'||value==='pricing')void openSettings('task');else if(value==='delivery')openDelivery();else if(value==='prepare')void prepareDraft();else if(value==='workshop')openWorkspace('workshop');else if(value==='edit')openEditor(qualityTarget.variant??variant);else if(value==='publication')void send(undefined,'核对发布结果');else if(value==='facts')setText('补充商品事实：')}} optimized={async(proposal,reply)=>{setQualityTarget(null);setPerformanceReports(undefined);setOptimizationProposal(proposal);setPerformanceOpen(true);setNotice(reply);await changed()}}/>}
    {revisionTarget&&<RevisionPanel key={revisionTarget.jobId} jobId={revisionTarget.jobId} initialRevision={revisionTarget.revision} close={()=>setRevisionTarget(null)} changed={async()=>{setConfirmPlan(null);await changed()}}/>}
    {accountCheckTarget&&<PublicationAuditEditor target={accountCheckTarget} close={()=>setAccountCheckTarget(null)} changed={changed}/>}
    {attentionTarget&&<AttentionPanel initial={attentionTarget.report} initialReview={attentionTarget.review} initialBatch={attentionTarget.batch} close={()=>setAttentionTarget(null)} onOpen={id=>{setAttentionTarget(null);select(id)}} onQuality={id=>{setAttentionTarget(null);select(id);setQualityTarget({jobId:id})}} onCalendar={(jobIds,start,batch)=>{setAttentionTarget(null);setCalendarTarget({jobIds,start,batch})}} changed={async()=>{setConfirmPlan(null);await changed()}}/>}
    {calendarTarget&&<PublicationCalendar initialStart={calendarTarget.start} initialJobIds={calendarTarget.jobIds} initialBatch={calendarTarget.batch} initialReport={calendarTarget.report} initialAdjustment={calendarTarget.adjustment} close={()=>setCalendarTarget(null)} onOpen={id=>{setCalendarTarget(null);select(id)}} onAttention={batch=>{setCalendarTarget(null);setAttentionTarget({batch})}} onReview={batch=>{setCalendarTarget(null);openBatchReview(batch)}} changed={async()=>{setConfirmPlan(null);await changed()}}/>}
    {batchReviewOpen&&<BatchReviewPanel onCalendar={batch=>{setBatchReviewOpen(false);setCalendarTarget({batch})}} onQuality={(id,index)=>{setBatchReviewOpen(false);select(id);setQualityTarget({jobId:id,variant:index})}} onHistory={id=>{setBatchReviewOpen(false);select(id);setRevisionTarget({jobId:id})}} initialBatch={reviewBatch} close={()=>setBatchReviewOpen(false)} onOpen={id=>{setBatchReviewOpen(false);select(id)}} changed={changed}/>}
    {workspaceTab&&<WorkspacePanel initialTab={workspaceTab} initialReport={workspaceReport} job={job} options={options} close={()=>setWorkspaceTab(null)} open={id=>{setWorkspaceTab(null);select(id)}} saved={async reply=>{setConfirmPlan(null);setNotice(reply);await changed()}} onCommand={command=>{setWorkspaceTab(null);void send(undefined,command)}}/>}
    {performanceOpen&&<PerformanceBoard key={currentId||'new'} accountId={accountId} options={options} jobId={currentId||undefined} reports={performanceReports} proposal={optimizationProposal} close={()=>setPerformanceOpen(false)} onCommand={command=>{setPerformanceOpen(false);void send(undefined,command)}} changed={changed}/>}
    {editOpen && <ListingEditor key={`${editOpen.job.id}:${editOpen.variant}`} job={editOpen.job} plan={editOpen.plan} variant={editOpen.variant} returnToReview={Boolean(reviewReturn)} reviewLabel={reviewLabel} close={() => finishReviewStep(()=>setEditOpen(null))} saved={changed} />}
    {deliveryOpen&&job&&<DeliveryDialog key={job.id} job={job} initialCreate={deliveryOpen==='create'} returnToReview={Boolean(reviewReturn)} reviewLabel={reviewLabel} close={()=>finishReviewStep(()=>setDeliveryOpen(null))} saved={async reply=>{setConfirmPlan(null);setNotice(reply);await refreshAccounts();await changed()}}/>}
    {distributionOpen&&job&&<DistributionDialog key={job.id} job={job} options={options} variant={variant} initialSelection={distributionSelection} edit={editDistribution} disabled={contextBlocked} close={()=>{setDistributionOpen(false);setDistributionSelection(null)}} done={async reply=>{setNotice(reply);await changed();if(selectedId.current===job.id)setProgressOpen(true)}}/>}
    {confirmPlan && <PublishConfirmation key={confirmPlan.revision} plan={confirmPlan.plan} job={confirmPlan.job} variant={confirmPlan.variant} accountName={confirmPlan.accountName} deliveryName={confirmPlan.deliveryName} busy={publishBusy} checking={planBusy||accountBusy||accountsBusy||mediaBusy} stale={reviewStale} readOnly={reviewReadOnly} error={planError} close={() => {setConfirmPlan(null);clearReviewReturn()}} confirm={() => void publish()} refresh={()=>void refreshReview()} edit={()=>leaveReview(()=>openEditor())} settings={()=>leaveReview(()=>void openSettings('task'))} delivery={()=>leaveReview(openDelivery)} prepare={()=>leaveReview(()=>{void prepareDraft().finally(()=>void resumeReview())})} />}
    {returningReview&&<Dialog title={loadingReviewSettings?'读取共用商品设置':reviewReturn?.distribution?'返回同步审核':'返回发布审核'} close={clearReviewReturn}><div className="space-y-4 text-xs leading-7">{returnError?<><p role="alert" className="rounded-lg bg-amber-50 p-3 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{loadingReviewSettings?'共用设置':reviewLabel}读取暂未完成：{returnError}。{loadingReviewSettings?'已保留账号和同步接口选择，可重试读取设置。':'已提交的修改保留，重新读取后继续审核。'}</p><button className={buttonClass} onClick={()=>void (loadingReviewSettings?openSettings('task'):resumeReview())}>{loadingReviewSettings?'重试读取共用设置':reviewReturn?.distribution?'重新读取同步审核':'重新读取总览'}</button></>:<p role="status" className="flex items-center gap-2 text-slate-500 dark:text-slate-400"><Loader2 size={15} className="animate-spin"/>{loadingReviewSettings?'正在读取共用分类、所在地与托管设置…':'正在重新核对商品内容、账号与发货设置…'}</p>}<div className="flex justify-end gap-2">{loadingReviewSettings&&<button className={buttonClass} onClick={()=>void resumeReview()}>返回同步审核</button>}<button className={buttonClass} onClick={clearReviewReturn}>返回对话</button></div></div></Dialog>}
    {settingsOpen && profile && <OperationsSettings key={`${settingsOpen}:${job?.id||'default'}`} returnToReview={Boolean(reviewReturn)} reviewLabel={reviewLabel} onDelivery={openDelivery} job={settingsOpen === 'task' ? job : null} plan={settingsOpen === 'task' ? plan : null} changed={changed} profile={settingsOpen === 'default' ? profile : undefined} settings={settingsOpen === 'default' ? profile : publishSettings} options={options} close={() => finishReviewStep(()=>setSettingsOpen(null))} save={async value => {
      if (settingsOpen === 'default') { await saveOperationProfile(value as OperationProfile); setProfile(value as OperationProfile); if (!currentId) setNewMode(value.publish_mode); setNotice('默认经营设置已保存，新任务会沿用。') }
      else if (currentId) { await configureOperationPublish(currentId, value); signature.current = ''; await reload.current(); await refreshPlan(); setNotice('当前任务发布设置已更新。') }
    }} />}
  </div>
}
