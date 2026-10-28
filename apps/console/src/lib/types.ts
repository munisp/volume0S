export type OfferTier = {
  id: string
  setup_minor: number
  monthly_minor: number
  pack_minor?: number
  currency: string
  turnaround_hours?: number
  entitlements: Record<string, number>
}

export type Offer = {
  id: string
  name: string
  module: string
  commercial_status: string
  tiers: OfferTier[]
}

export type Bundle = {
  id: string
  setup_minor: number
  monthly_minor: number
  currency: string
  required_offers: string[]
  studio_packs_per_month: number
}

export type Tenant = {
  id: string
  name: string
  slug: string
  lifecycle: string
  version: number
  outbound_suspended: boolean
  suspension_reason?: string
  created_at?: string
  updated_at?: string
}

export type Workspace = { id: string; tenant_id: string; name: string }
export type Subscription = { id: string; tenant_id: string; offer: string; tier: string; status: string }

export type CampaignStatus = 'drafting' | 'awaiting_approval' | 'approved' | 'reserving' | 'dispatching' | 'settling' | 'completed' | 'approval_timed_out' | 'failed' | 'cancelled'
export type StudioStatus = 'generating' | 'awaiting_qa' | 'approved' | 'rejecting' | 'rejected' | 'delivering' | 'delivered' | 'qa_timed_out' | 'failed' | 'cancelled'

export type CampaignSnapshot = {
  tenant_id: string
  campaign_id: string
  cycle_id: string
  status: CampaignStatus
  amount_minor: number
  currency: string
  channels: string[]
  started_at: string
  updated_at: string
}

export type StudioSnapshot = {
  tenant_id: string
  pack_id: string
  product_id: string
  tier: 'starter' | 'growth' | 'pro'
  status: StudioStatus
  assets: number
  manifest_sha256: string
  started_at: string
  updated_at: string
}

export type StudioAsset = { id: string; url: string; expires_in: number }
export type StudioAssetResponse = { pack_id: string; manifest_sha256: string; assets: StudioAsset[] }

export type Campaign = {
  id: string
  name: string
  client: string
  cycle: string
  status: CampaignStatus
  channels: string[]
  budgetMinor: number
  currency?: string
  updated: string
}

export type StudioPack = {
  id: string
  product: string
  client: string
  tier: 'starter' | 'growth' | 'pro'
  status: StudioStatus
  assets: number
  manifestSha256?: string
  updated: string
}

export type PageId = 'overview' | 'onboarding' | 'campaigns' | 'studio' | 'billing' | 'offers' | 'settings'

export type Balance = {
  account_id: string
  debits_pending: string
  debits_posted: string
  credits_pending: string
  credits_posted: string
  available_minor: string | null
}

export type BillingCycleSnapshot = {
  tenant_id: string
  subscription_id: string
  cycle: string
  state: string
  dunning_attempts: number
  recovery_reference: string
  updated_at?: string
}

export type BillingDocument = {
  kind: 'invoice' | 'receipt'
  document_id: string
  tenant_id: string
  document_number: string
  structured: Record<string, unknown>
  created_at: string
  pdf_sha256: string
}

export type ArAgingReport = {
  schema: 'volumeos.ar-aging.v1'
  tenant_id: string
  as_of: string
  basis: string
  totals: Record<string, Record<string, string>>
  invoices: {
    invoice_id: string; document_number: string; issue_date: string; due_date: string
    age_days: number; currency: string; total_minor: string; paid_minor: string
    outstanding_minor: string; bucket: string
  }[]
}

export type PartnerStatement = {
  schema: 'volumeos.partner-share.v1'; tenant_id: string; partner_id: string
  period_start: string; period_end: string; receipt_count: number
  totals: Record<string, { receipt_minor: string; partner_share_minor: string }>
  receipts: Record<string, string | number>[]
}

export type TrialBalance = {
  schema: 'volumeos.trial-balance.v1'; currency: string; account_count: number
  total_debits_posted: string; total_credits_posted: string; balanced: boolean
  accounts: Record<string, string | number>[]
  revenue_per_offer: { offer: string; debits_posted: string; credits_posted: string; net_credit_minor: string }[]
}

export type ExportMetadata = {
  schema: 'volumeos.financial-export.v1'; object_key: string; sha256: string
  retained_until: string; object_lock: 'COMPLIANCE'; immutable: true; created: boolean
}

export type ExportResponse<T> = { report: T; export: ExportMetadata }
export type Checkout = { provider: string; reference: string; status: string; checkout_url: string }

export type CollectionReceipt = {
  event_id: string; provider: string; reference: string; amount_minor: string
  currency: 'NGN' | 'USD'; disposition: 'suspense'; ledger_transfer_id: string
}

export type CollectionResolution = {
  schema: 'volumeos.collection-resolution.v1'; resolution_id: string; event_id: string
  provider: string; reference: string; tenant_id: string; amount_minor: string
  currency: 'NGN' | 'USD'; ledger_transfer_id: string; approved_by: string
  reason: string; status: 'resolved'
}

export type OfferArtifact = {
  tenant_id: string
  offer: '03' | '05' | '07' | '08'
  job_id: string
  version: number
  object_key: string
  sha256: string
  media_type: string
  parent_sha256?: string | null
  status: 'draft' | 'reviewed' | 'released'
}

export type OfferJobSnapshot = {
  tenant_id: string
  job_id: string
  offer: '03' | '05' | '07' | '08'
  amount_minor: number
  currency: 'NGN' | 'USD'
  status: string
  artifact: OfferArtifact | null
  approval_count: number
  started_at: string
  updated_at: string
}

export type ConversationDraft = {
  tenant_id: string
  conversation_id: string
  inbound_message_id: string
  response_text: string
  confidence: number
  model_version: string
  escalation_reason: string | null
  status: 'requires-review' | 'approved' | 'rejected' | 'sent'
  created_at_unix: number
  draft_sha256: string
}

export type ConversationQueueItem = {
  conversation: {
    tenant_id: string
    conversation_id: string
    phone_number_id: string
    recipient: string
    contact_name: string
    subject_digest: string
    status: 'active' | 'suppressed'
    created_at_unix: number
  }
  drafts: ConversationDraft[]
}
