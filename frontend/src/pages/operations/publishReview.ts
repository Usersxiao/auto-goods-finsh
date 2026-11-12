import type { OperationJob, PublishPlan } from '@/api/operations'

export function automationScope(reply: boolean, delivery: boolean) {
  const changes = [reply && '绑定当前商品知识，并启用所选账号的 AI 回复', delivery && '绑定当前商品交付内容，并启用所选账号的自动确认发货'].filter(Boolean)
  return changes.length ? `发布成功后${changes.join('；')}。这些账号开关也会影响该账号的其他商品，请核对账号现有规则。未勾选的功能保留账号原有设置。` : '本次发布跳过回复与发货配置，保留账号原有开关和规则。'
}

function stableKey(value: unknown) {
  return JSON.stringify(value, (_key, entry) => entry && typeof entry === 'object' && !Array.isArray(entry)
    ? Object.fromEntries(Object.entries(entry).sort(([a], [b]) => a.localeCompare(b))) : entry)
}

// Polling timestamps and conversation events do not change the reviewed product.
export function publishSourceKey(job: OperationJob, variant: number) {
  const parameters = job.parameters as Record<string, unknown> | undefined
  const fields = ['workflow_hold', 'publish_mode', 'publish_account_id', 'publish_template_id', 'publish_address', 'publish_at', 'publish_variant_index',
    'auto_reply_enabled', 'auto_delivery_enabled', 'delivery_card_id', 'delivery_text', 'delivery_revision', 'ai_config_ref',
    'price_min', 'price_max', 'unit_cost', 'support_cost', 'refund_cost', 'acquisition_cost', 'target_contribution', 'fee_rate']
  return stableKey({id:job.id, variant, status:job.status, source:job.source_hash, material:job.material_id, publishAt:job.publish_at,
    draft:job.result?.draft, assets:job.result?.assets, price:job.result?.price, category:job.result?.category,
    publication:job.result?.publication || job.publication,
    settings:Object.fromEntries(fields.map(key => [key, parameters?.[key]]))})
}

export function publishPlanKey(plan: PublishPlan | null) {
  if (!plan) return ''
  const automation = plan.automation
  return stableKey({fingerprint:plan.fingerprint, ready:plan.ready, prepare:plan.needs_prepare, blockers:plan.blockers,
    settings:plan.settings, preview:plan.preview, seller:plan.seller, pricing:plan.pricing, pending:plan.pending, warnings:plan.warnings,
    automation:automation && {reply:automation.auto_reply_enabled, delivery:automation.auto_delivery_enabled,
      replyReady:automation.reply_ready, deliveryReady:automation.delivery_ready, model:automation.ai_model,
      card:automation.delivery_card_id, name:automation.delivery_name, enableCard:automation.enable_delivery_card}})
}
