import request, { get, post, put } from '@/utils/request'
import type { PlatformCategoryCandidate, PlatformCategoryProperty, PlatformMaterialAttribute } from './productPublish'

export type OperationStatus = 'queued' | 'running' | 'waiting_assets' | 'ready' | 'failed' | 'cancelled' | 'published' | 'publish_blocked' | 'publish_failed' | 'publish_unknown' | 'scheduled' | 'schedule_missed'
export type OperationValue = string | number | boolean | null | OperationValue[] | { [key: string]: OperationValue }
export interface OperationCoverage {
  unique: number
  relevant: number
  ranked: number
  detail_count: number
  want_coverage: number
}
export interface OperationItem {
  item_id: string
  rank: number
  title: string
  price?: string | number
  want_count?: number | null
  kind: string
}
export interface OperationVariant {
  style: string
  title: string
  description: string
}
export interface OperationResult {
  publication_audit?: PublicationAudit
  workshop?: {candidates:CoverCandidate[]}
  publication_recovery?: {state?:string;attempts?:number;message?:string}
  category?: OperationCategory
  automation?: { status: 'active' | 'needs_setup' | 'not_requested'; message?: string; auto_reply_enabled?: boolean; auto_delivery_enabled?: boolean }
  publication?: Publication
  collection?: { warnings?: string[] }
  ranking?: {
    ranking_note: string
    pricing: { median?: number; p25?: number; p75?: number; sample_count: number }
    hot100: OperationItem[]
  }
  analysis?: { summary: string; [key: string]: OperationValue }
  assets?: {
    images: { asset_id: string; name: string; url: string }[]
    videos?: { name: string; url: string }[]
    warnings?: string[]
  }
  facts?: { facts: OperationValue[]; missing?: string[]; conflicts?: string[] }
  price?: { suggested?: number; reason?: string; status?:string; review_recommended?:boolean; minimum_sale_price?:number }
  draft?: {
    positioning: OperationValue
    titles: string[]
    variants: OperationVariant[]
    cover_plans?: OperationValue[]
    faq?: OperationValue[]
    pending?: string[]
  }
  timeline?: { stage: string; at: string }[]
}
export interface OperationJob {
  conversation?: {account_id:string|null;title:string|null;publish_mode:'review'|'automatic';next_step:string;needs_attention:boolean}
  source_hash?:string
  source_job_id?: string | null
  outcome?: string
  id: string
  keyword: string
  status: OperationStatus
  stage: string
  error?: string | null
  updated_at?: string
  publish_at?:string
  coverage?: OperationCoverage
  hot_count: number
  material_id?: number
  artifacts?: string[]
  result?: OperationResult
  publication?: Publication
  parameters?: { publish_snapshot?: {account_id?:string;automation?:{ai_config_ref?:string|null}}; workflow_hold?:string; copy_guidance?:string; publish_at?:string; schedule_batches?:string[]; schedule_batch_id?:string; folder?: string; confirmed_facts?: string[]; publish_mode?: 'review' | 'automatic'; publish_account_id?: string | null; publish_variant_index?:number; account_id?: string | null; ai_config_ref?: string | null; delivery_card_id?: number | null; delivery_text?: string | null; delivery_chat_draft?: {name?:string;content?:string} }
}
export interface OperationProfile {
  copy_guidance?:string
  ai_config_ref?: string | null
  account_id?: string | null
  product_kind: string
  sample_target: number
  max_pages: number
  detail_limit: number
  request_interval: number
  confirmed_facts: string[]
  price_min?: number | null
  price_max?: number | null
  unit_cost?: number | null
  support_cost?: number | null
  refund_cost?: number | null
  acquisition_cost?: number | null
  target_contribution?: number | null
  fee_rate: number
  publish_mode: 'review' | 'automatic'
  publish_account_id?: string | null
  publish_template_id?: number | null
  publish_address?: string | null
  publish_variant_index: number
  auto_reply_enabled?: boolean
  auto_delivery_enabled?: boolean
  delivery_card_id?: number | null
  delivery_text?: string | null
}
export interface Publication {
  account_id?: string
  status: 'publishing' | 'published' | 'failed' | 'blocked' | 'unknown'
  item_id?: string
  item_url?: string
  message?: string
  blockers?: string[]
  log_id?: number
}
export interface OperationEvent {
  id: string | number
  kind: 'user' | 'assistant' | 'tool'
  name: string
  status: 'running' | 'success' | 'error'
  data: { text?: string; input?: OperationValue; output?: OperationValue; error?: string; error_type?: string; result?: string; operation?: string; historical?: boolean; outcome?: boolean }
  created_at: string
  finished_at?: string | null
}
export interface PublishOptions {
  accounts: { id: string; label: string; active: boolean }[]
  templates: { id: number; title: string; category?: string | null }[]
  cards?: { id: number; name: string; type: string; enabled: boolean }[]
}
export interface OperationCategory {
  status: 'ready' | 'needs_input' | 'waiting'
  label?: string
  message?: string
  candidates?: PlatformCategoryCandidate[]
  properties?: PlatformCategoryProperty[]
  patch?: { platform_attributes?: PlatformMaterialAttribute[] }
}
export type PublishSettings = Pick<OperationProfile, 'publish_mode' | 'publish_account_id' | 'publish_template_id' | 'publish_address' | 'auto_reply_enabled' | 'auto_delivery_enabled' | 'delivery_card_id' | 'delivery_text' | 'ai_config_ref'> & {publish_at?:string}
export interface AutomationPlan {
  delivery_preview?: DeliveryPreview
  ai_model?: string | null
  auto_reply_enabled: boolean
  auto_delivery_enabled: boolean
  reply_ready: boolean
  delivery_ready: boolean
  enable_delivery_card?: boolean
  delivery_card_id?: number | null
  delivery_name?: string | null
  delivery_text?: string
  blockers: string[]
  scope_note: string
}
export interface DeliveryPreview {kind:string;name:string;body:string;note:string}
export interface PublicationAudit {
  status:'pending'|'checking'|'verified'|'partial'|'differences'
  checked_at?:string;account_id?:string;item_id?:string;message:string;note?:string
  checks:{id:string;label:string;state:'matched'|'different'|'unknown'|'skipped';expected?:string|number|null;actual?:string|number|null;note:string}[]
}
export const getPublicationAudit=(id:string)=>get<{audit:PublicationAudit|null}>(`/api/v1/operations/jobs/${id}/publication/audit`)
export const runPublicationAudit=(id:string)=>post<{audit:PublicationAudit}>(`/api/v1/operations/jobs/${id}/publication/audit`)
export interface PublishedAccountPlan {
 job_id:string;account_id:string;account_name:string;item_id:string;fingerprint:string;ready:boolean;blockers:string[];note:string;login_note:string
 account:{enabled:boolean;credential_present:boolean};reply:{enabled:boolean;configured:boolean;model:string}
 delivery:{binding_ready:boolean;card_count:number;auto_confirm:boolean;only_send_card:boolean;legacy_warning:boolean}
 rules:{code:string;name:string;enabled:boolean;excluded:boolean;known:boolean;auto_close:boolean;only_card_after_close:boolean}[]
}
export const getPublishedAccountPlan=(id:string)=>get<PublishedAccountPlan>(`/api/v1/operations/jobs/${id}/publication/account`)
export const applyPublishedAccountRules=(id:string,codes:string[],fingerprint:string)=>post<{success:boolean;reused:boolean;reply:string}>(`/api/v1/operations/jobs/${id}/publication/account/rules`,{rule_codes:codes,fingerprint,confirmed:true})
export interface PublishedReplyPlan {
  job_id:string;account_id:string;account_name:string;item_id:string;ai_config_ref:string;fingerprint:string;ready:boolean;blockers:string[];note:string
  before:{model:string;enabled:boolean;knowledge_matches:boolean};after:{model:string;enabled:true;knowledge:string}
}
export const getPublishedReplyPlan=(id:string,ref:string)=>get<PublishedReplyPlan>(`/api/v1/operations/jobs/${id}/publication/reply?${new URLSearchParams({ai_config_ref:ref})}`)
export const applyPublishedReply=(id:string,ref:string,fingerprint:string)=>post<{success:boolean;reused:boolean;reply:string}>(`/api/v1/operations/jobs/${id}/publication/reply`,{ai_config_ref:ref,fingerprint,confirmed:true})
export interface PublishPlan {
  needs_prepare?:boolean
  pricing?:OperationResult['price']
  ready: boolean
  blockers: string[]
  fingerprint?: string
  settings: PublishSettings
  category?: OperationCategory
  automation?: AutomationPlan
  seller?: { name: string; account_id?: string | null; avatar?: string | null }
  preview?: { title: string; description: string; price: number; images: string[]; videos?: { name?: string; url: string }[]; platform_category_name?: string; address?: string }
  warnings?: string[]
  pending?: string[]
}
const base = '/api/v1/operations'
export interface PendingOperation {
  token:string;kind:string;title:string;confirm_label:string;count:number|null;expired:boolean;expires_at:string;description:string
}
export interface ConfirmationListing {
  title?:string;description?:string;price?:number|string;images?:string[];videos?:{name?:string;url:string}[];platform_category_name?:string;address?:string
}
export interface ConfirmationRow extends ConfirmationListing {
  job_id:string;account_name?:string;account_id?:string;publish_at?:string;old_time?:string;new_time?:string;operation_label?:string;reason?:string;notes?:string[];before?:ConfirmationListing
}
export interface OperationConfirmation extends PendingOperation {rows:ConfirmationRow[];notes:string[];blockers:string[];can_apply:boolean}
export const getPendingOperation=(id:string)=>get<{pending:PendingOperation|null}>(`${base}/jobs/${id}/confirmation`)
export const reviewPendingOperation=(id:string,token:string)=>post<OperationConfirmation>(`${base}/jobs/${id}/confirmation/review`,{token})
export const cancelPendingOperation=(id:string,token:string)=>post<{reply:string}>(`${base}/jobs/${id}/confirmation/cancel`,{token})
export const applyPendingOperation=(id:string,token:string)=>post<{reply:string}>(`${base}/jobs/${id}/confirmation/apply`,{token,confirmed:true})
export interface ScheduledProduct {job_id:string;batch_id:string;keyword:string;folder:string;publish_at:string;account_id:string;status:OperationStatus;mode:'review'|'automatic';ready:boolean;error?:string;publication?:Publication}
export const getOperationSchedules=(batchId?:string)=>get<{schedules:ScheduledProduct[]}>(`${base}/schedules${batchId?'?batch_id='+encodeURIComponent(batchId):''}`)
export interface DeliveryCard { id:number; name:string; type:string; enabled:boolean; usable:boolean }
export interface DeliveryState { selected:DeliveryCard|null; legacy_text:string; active_card_id?:number; active_cards:{id:number;name:string}[]; published:boolean; pending:boolean; fingerprint?:string|null; warnings?:string[] }
export interface DeliveryResponse { job:OperationJob; reply:string; delivery:DeliveryState }
export const getDeliveryCards = (search='',page=1) => get<{cards:DeliveryCard[];total:number;page:number}>(`${base}/delivery-cards?search=${encodeURIComponent(search)}&page=${page}`)
export const getDeliveryCard = (id:number) => get<DeliveryCard&{text_content?:string|null;note:string}>(`${base}/delivery-cards/${id}`)
export const getOperationDelivery = (id:string) => get<DeliveryState>(`${base}/jobs/${id}/delivery`)
export const chooseOperationDelivery = (id:string,card_id:number) => put<DeliveryResponse>(`${base}/jobs/${id}/delivery`,{card_id})
export const createOperationDelivery = (id:string,value:{name:string;content:string;request_key:string}) => post<DeliveryResponse>(`${base}/jobs/${id}/delivery/cards`,value)
export const applyOperationDelivery = (id:string,fingerprint:string) => post<DeliveryResponse>(`${base}/jobs/${id}/delivery/apply`,{fingerprint,confirmed:true})
export interface AiConnection { id: string; name: string; model: string; provider_type?: string }
export interface AiConnectionInput { name?: string; base_url: string; api_key: string; model?: string; provider_type: string }
export const getAiConnections = () => get<{connections:AiConnection[]}>(`${base}/ai-connections`)
export const createAiConnection = (value:AiConnectionInput) => post<AiConnection>(`${base}/ai-connections`,value)
export const fetchConnectionModels = (value:AiConnectionInput) => post<{models:{id:string;name:string}[]}>(`${base}/ai-connections/models`,value)
export interface DistributionRecord { job_id: string; account_id: string; status: OperationStatus; publication?: Publication; automation?: OperationResult['automation']; error?: string }
export interface DistributionTarget { account_id: string; job_id: string; action: 'reuse'|'publish'; status: string; ready: boolean; blockers: string[]; seller?: PublishPlan['seller']; preview?: PublishPlan['preview']; automation?: AutomationPlan; publication?: Publication }
export interface DistributionPlan { ready: boolean; fingerprint: string; targets: DistributionTarget[] }
export interface DistributionInput { account_ids: string[]; variant_index: number; ai_config_ref?: string | null; fingerprint?: string; automation_confirmed?: boolean }
export const getDistributionStatus = (id:string) => get<{targets:DistributionRecord[]}>(`${base}/jobs/${id}/distribution`)
export const getDistributionPlan = (id:string,value:DistributionInput) => post<DistributionPlan>(`${base}/jobs/${id}/distribution/plan`,value)
export const publishDistribution = (id:string,value:DistributionInput) => post<{reply:string}>(`${base}/jobs/${id}/distribution/publish`,value)
export const listOperationJobs = () => get<{ jobs: OperationJob[] }>(`${base}/jobs`)
export const getOperationJob = (id: string) => get<OperationJob>(`${base}/jobs/${id}`)
export const getOperationProfile = () => get<OperationProfile>(`${base}/profile`)
export const saveOperationProfile = (profile: OperationProfile) => put(`${base}/profile`, profile)
export const operationAction = (id: string, action: 'resume' | 'cancel') => post<{ job: OperationJob; reply: string }>(`${base}/jobs/${id}/${action}`)
export const sendOperationMessage = (message: string, jobId: string | null, requestKey: string, mode: 'review' | 'automatic' = 'review', accountId?: string | null) =>
  post<{ reply?: string; job?: OperationJob; delivery_action?:'open'|'create'|'refresh';refresh_options?:boolean;download?:{job_id:string;name:string};revision_id?:number; ui_action?:'account_check'|'preview'|'report'|'performance'|'products'|'templates'|'workshop'|'batch_review'|'revisions'|'quality'|'calendar'|'attention';attention_report?:AttentionReport;repair_review?:RepairReview;calendar_report?:PublicationCalendarReport;schedule_adjustment?:ScheduleAdjustment;quality_report?:ProductQualityReport;batch_id?:string;workflow?:OperationWorkflow;product_report?:ProductHubReport;performance?:PerformanceReport[];proposal?:OptimizationProposal }>(`${base}/chat`, { message, job_id: jobId, request_key: requestKey, new_task: !jobId, publish_mode: mode, ...(!jobId ? { publish_account_id: accountId || null } : {}) })
export const getOperationEvents = (id: string) => get<{ events: OperationEvent[] }>(`${base}/jobs/${id}/events`)
export const getPublishOptions = () => get<PublishOptions>(`${base}/publish-options`)
export const getPublishPlan = (id: string, variant = 0) => get<PublishPlan>(`${base}/jobs/${id}/preflight?variant_index=${variant}`)
export const publishOperation = (id: string, fingerprint: string, variant = 0) => post<{ job: OperationJob; reply: string }>(`${base}/jobs/${id}/publish`, { fingerprint, variant_index: variant, automation_confirmed: true })
export const configureOperationPublish = (id: string, settings: PublishPlan['settings']) => put<{job:OperationJob;reply:string}>(`${base}/jobs/${id}/publish-settings`, settings)
export const prepareOperation = (id: string) => post(`${base}/jobs/${id}/prepare`)
export const addOperationVideos = (id:string,paths:string[]=[]) => post<{job:OperationJob;reply:string}>(`${base}/jobs/${id}/media/videos`,{paths})
export const chooseOperationCategory = (id: string, value: { selected_index?: number; attributes?: PlatformMaterialAttribute[] }) => post<{job:OperationJob}>(`${base}/jobs/${id}/category`, value)
export const retryOperationSetup = (id:string) => post(`${base}/jobs/${id}/automation/retry`)
export const editOperationDraft = (id: string, value: { expected_source_hash?:string; variant_index: number; title: string; description: string; price: number }) => put(`${base}/jobs/${id}/draft`, value)
export async function downloadOperationFile(jobId: string, name: string) {
  const response = await request.get<Blob>(`${base}/jobs/${jobId}/files/${encodeURIComponent(name)}`, { responseType: 'blob' })
  const url = URL.createObjectURL(response.data)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 30_000)
}

export interface LocalPerformance { recorded_orders:number;paid_orders:number;paid_amount:number;amount_missing:number;refund_orders:number;consultations:number;reply_failures:number;delivery_failures:number }
export interface PerformanceFinding {scope:string;item_id?:string;observation:string;suggestion:string}
export interface PerformanceReport {
  account_id:string;account_name:string;days:number;generated_at:string
  local:{start:string;end:string;last_order_sync:string|null;totals:LocalPerformance}
  shop:{available:boolean;metrics:Record<string,number|null>;dates:string[];sampled_at:string|null;message?:string}
  items:{item_id:string;title:string;price:number|null;job_id?:string;local:LocalPerformance;counters:{view_count:number|null;want_count:number|null;sampled_at:string|null};delta:{start:string;end:string;values:Record<string,number|null>;note:string}|null}[]
  findings:PerformanceFinding[];coverage:string[]
}
export interface OptimizationProposal {
  id:string;job_id:string;account_id:string;item_id:string;target:'live'|'draft';status:string;applicable:boolean;fingerprint:string
  before:{title:string;description:string;price:number};after:{title:string;description:string;price:number}
  reasons:string[];findings:PerformanceFinding[];note:string;created_at:string;expires_at:string
}
export const getPerformanceReport=(account:string,days=7)=>get<PerformanceReport>(`${base}/performance?account_id=${encodeURIComponent(account)}&days=${days}`)
export const refreshPerformance=(account:string,days=7,item_id?:string)=>post<PerformanceReport>(`${base}/performance/refresh`,{account_id:account,days,item_id})
export const getOptimizationHistory=(jobId?:string)=>get<{proposals:OptimizationProposal[]}>(`${base}/optimizations${jobId?'?job_id='+encodeURIComponent(jobId):''}`)
export const applyOptimization=(id:string,fingerprint:string)=>post<{proposal:OptimizationProposal;reply:string;job:OperationJob}>(`${base}/optimizations/${id}/apply`,{fingerprint,confirmed:true})

export interface CoverCandidate {id:string;name:string;headline:string;url:string;claims:{id:string;text:string}[]}
export interface HubProduct {id:string;job_id:string|null;account_id:string;account_name:string|null;account_status:string|null;item_id:string;title:string;price:number|null;status:string;stage:string;cover:string|null;item_url:string|null;delivery_ready:boolean;ai_ready:boolean;card_count:number}
export interface HubFilters {search:string;account_id:string;state:string;issue:string}
export interface ProductHubReport {items:HubProduct[];summary:{total:number;published:number;scheduled:number;attention:number;missing_delivery:number};page:number;page_size:number;coverage:string;filters:HubFilters}
export interface BusinessTemplate {id:string;name:string;fingerprint:string;minimum_sale_price:number;fields:string[];settings:Partial<OperationProfile>;updated_at:string}
export const getProductHub=(filters:Partial<HubFilters>={},page=1)=>get<ProductHubReport>(`${base}/products?${new URLSearchParams({...filters,page:String(page)}).toString()}`)
export const openHubProduct=(account_id:string,item_id:string)=>post<{job:OperationJob}>(`${base}/products/open`,{account_id,item_id})
export const getBusinessTemplates=()=>get<{templates:BusinessTemplate[]}>(`${base}/business-templates`)
export const saveBusinessTemplate=(value:{job_id:string;name:string;copy_guidance?:string;overwrite?:boolean})=>post<{template:BusinessTemplate;reply:string}>(`${base}/business-templates`,value)
export const applyBusinessTemplate=(id:string,job_id:string,fingerprint:string)=>post<{job:OperationJob;reply:string}>(`${base}/business-templates/${id}/apply`,{job_id,fingerprint})
export const generateOperationCovers=(id:string,headline='')=>post<{job:OperationJob;reply:string}>(`${base}/jobs/${id}/workshop/covers`,{headline})
export const useOperationCover=(id:string,candidate_id:string)=>put<{job:OperationJob;reply:string}>(`${base}/jobs/${id}/workshop/cover`,{candidate_id})
export const orderOperationImages=(id:string,order:number[])=>put<{job:OperationJob;reply:string}>(`${base}/jobs/${id}/workshop/order`,{order})

export interface OperationWorkflow {id:string;root_job_id:string;job_id:string;status:'queued'|'running'|'waiting'|'paused'|'completed'|'cancelled';message:string;steps:{label:string;status:'done'|'running'|'pending'}[];completed:number;total:number;updated_at:string}
export const getOperationWorkflows=(id:string)=>get<{workflows:OperationWorkflow[]}>(`${base}/jobs/${id}/workflows`)
export const controlOperationWorkflow=(id:string,operation:'pause'|'resume'|'cancel')=>post<{workflow:OperationWorkflow}>(`${base}/workflows/${id}/control`,{operation})
export interface ScheduleBatch {id:string;total:number;reviewable:number;first_at:string;last_at:string}
export interface BatchReviewItem extends ScheduledProduct {source_hash:string;variant_index:number;preview:{title:string;description:string;price?:number;images:string[];videos?:{url:string;name?:string}[];platform_category_name?:string;address?:string};seller:{name?:string;account_id?:string};automation?:Partial<AutomationPlan>;pricing?:OperationResult['price'];reviewable:boolean;blockers:string[];warnings:string[];pending:string[]}
export interface BatchReview {id:string;batch_id:string;fingerprint:string;expires_at:string;items:BatchReviewItem[];total:number;ready_count:number;note:string}
export const getScheduleBatches=()=>get<{batches:ScheduleBatch[]}>(`${base}/schedule-batches`)
export const prepareBatchReview=(id:string)=>post<BatchReview>(`${base}/schedule-batches/${id}/review`,{})
export const approveBatchReview=(review:BatchReview,job_ids:string[])=>post<{count:number;reply:string}>(`${base}/batch-reviews/${review.id}/approve`,{fingerprint:review.fingerprint,job_ids,confirmed:true})


export interface ProductRevision {id:number;label:string;created_at:string;title:string;price:number;cover?:string;is_current:boolean;changes:string[]}
export interface RevisionPreview {title:string;description:string;price:number|null;images:string[];videos:{url:string;name?:string}[]}
export interface RevisionList {revisions:ProductRevision[];current_hash:string;has_draft:boolean;note:string}
export interface RevisionComparison {id:number;label:string;current_hash:string;before:RevisionPreview;after:RevisionPreview;changes:string[];can_restore:boolean;reason:string}
export const getProductRevisions=(id:string)=>get<RevisionList>(`${base}/jobs/${id}/revisions`)
export const getProductRevision=(id:string,revision:number)=>get<RevisionComparison>(`${base}/jobs/${id}/revisions/${revision}`)
export const saveProductRevision=(id:string,label:string,expected_hash:string)=>post<{id:number;reply:string}>(`${base}/jobs/${id}/revisions`,{label,expected_hash})
export const restoreProductRevision=(id:string,revision:number,expected_hash:string)=>post<{job:OperationJob;reply:string}>(`${base}/jobs/${id}/revisions/${revision}/restore`,{expected_hash,confirmed:true})


export type QualityAction='settings'|'delivery'|'prepare'|'workshop'|'pricing'|'publication'|'optimize'|'facts'|'edit'
export interface QualityCheck {id:string;group:string;state:'blocked'|'attention'|'pass'|'info';title:string;detail:string;action:QualityAction|null}
export interface ProductQualityReport {job_id:string;variant_index:number;fingerprint:string;generated_at:string;checks:QualityCheck[];counts:Record<QualityCheck['state'],number>;publication_ready:boolean;can_optimize:boolean;optimize_reason:string;note:string}
export const getProductQuality=(id:string,variant?:number)=>get<ProductQualityReport>(`${base}/jobs/${id}/quality${variant==null?'':'?variant_index='+variant}`)
export const optimizeProductQuality=(id:string,report:ProductQualityReport)=>post<{job:OperationJob;proposal:OptimizationProposal;reply:string}>(`${base}/jobs/${id}/quality/optimize`,{fingerprint:report.fingerprint,variant_index:report.variant_index})

export interface CalendarItem {
  job_id:string;batch_id:string;keyword:string;title:string;status:OperationStatus;account_id:string|null;account_name:string
  mode:string|null;folder:string|null;cover:string|null;publish_at:string;day:string;can_adjust:boolean;adjust_reason:string
  conflicts:{job_id:string;kind:'same_minute'|'close';minutes:number}[];conflict_count:number;missed:boolean
}
export interface PublicationCalendarReport {
  start:string;days:number;timezone:string;accounts:{id:string;label:string}[];batches:string[];items:CalendarItem[]
  calendar:{day:string;total:number;approved:number;conflicts:number;attention:number}[]
  summary:{total:number;approved:number;conflicts:number;missed:number};truncated:boolean
  filters:{account_id:string;batch_id:string;state:string};note:string
}
export interface ScheduleAdjustment {
  id:string;fingerprint:string;status:string;operation:'shift'|'spread';can_apply:boolean;expires_at:string;note:string
  rows:{job_id:string;batch_id:string;title:string;account_id:string|null;account_name:string;status:string;old_time:string;new_time:string;reason:string}[]
  conflicts:{job_id:string;count:number;nearby:CalendarItem['conflicts']}[]
}
export interface ScheduleAdjustmentInput {job_ids:string[];operation:'shift'|'spread';minutes?:number;start_at?:string;interval_minutes?:number;per_account?:boolean}
export const getPublicationCalendar=(filters:{start?:string;days?:number;account_id?:string;batch_id?:string;state?:string}={})=>get<PublicationCalendarReport>(`${base}/publication-calendar?${new URLSearchParams(Object.fromEntries(Object.entries(filters).map(([k,v])=>[k,String(v)]))).toString()}`)
export const previewScheduleAdjustment=(input:ScheduleAdjustmentInput)=>post<ScheduleAdjustment>(`${base}/schedule-adjustments`,input)
export const applyScheduleAdjustment=(value:ScheduleAdjustment)=>post<{count:number;reply:string}>(`${base}/schedule-adjustments/${value.id}/apply`,{fingerprint:value.fingerprint,confirmed:true})

export type AttentionKind='generation'|'publish'|'blocked'|'missed'|'unknown'|'setup'|'audit'|'audit_pending'
export interface AttentionItem {
  job_id:string;keyword:string;title:string;status:OperationStatus;kind:AttentionKind;account_id:string|null;account_name:string;batch_id:string|null
  label:string;reason_code:string;reason:string;suggestion:string;action:'check'|'calendar'|'setup'|'quality'|'prepare'|'workflow'|'audit'
  can_audit?:boolean;audit?:{status:PublicationAudit['status'];difference_fields:string[];unknown_fields:string[];checked_at:string|null}|null
  publish_at:string|null;can_reschedule:boolean;can_prepare:boolean;can_check:boolean;updated_at:string
}
export interface AttentionReport {items:AttentionItem[];summary:Record<AttentionKind|'total',number>;accounts:{id:string;label:string}[];total:number;page:number;page_size:number;filters:{account_id:string;batch_id:string;kind:string};note:string}
export interface RepairReview {id:string;fingerprint:string;status:string;can_apply:boolean;expires_at:string;note:string;rows:{job_id:string;title:string;account_id:string|null;account_name:string;operation:'prepare'|'resume';operation_label:string;publish_at:string|null;reason:string}[]}
export const getOperationsAttention=(filters:{account_id?:string;batch_id?:string;kind?:string;page?:number}={})=>get<AttentionReport>(`${base}/attention?${new URLSearchParams(Object.fromEntries(Object.entries(filters).map(([k,v])=>[k,String(v)]))).toString()}`)
export const previewOperationRepairs=(job_ids:string[])=>post<RepairReview>(`${base}/repair-reviews`,{job_ids})
export const applyOperationRepairs=(review:RepairReview)=>post<{count:number;reply:string}>(`${base}/repair-reviews/${review.id}/apply`,{fingerprint:review.fingerprint,confirmed:true})
export const checkOperationPublication=(id:string)=>post<{job:OperationJob;reply:string}>(`${base}/jobs/${id}/publication/check`,{})
