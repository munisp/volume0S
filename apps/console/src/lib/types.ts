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

/** As svc-tenancy sends it (domain.go Tenant). The lifecycle is `state`; the console once read a `lifecycle` field
 * that never existed, so every tenant showed as "required". */
export type Tenant = {
  id: string
  name: string
  slug: string
  state: 'prospect' | 'onboarding' | 'active' | 'paused' | 'offboarded'
  version: number
  outbound_suspended: boolean
  created_at?: string
  updated_at?: string
  workspaces?: Workspace[] | null
  subscriptions?: Subscription[] | null
}

/** One row of the agency's client list (platform operators only). */
export type TenantSummary = {
  id: string; name: string; slug: string; state: Tenant['state']; outbound_suspended: boolean
  created_at: string; updated_at: string; workspaces: number; offers: string[]
}

/** A sign-in of one business (Keycloak user), with its single VolumeOS role. */
export type Person = {
  id: string; username: string; email: string; first_name: string; last_name: string
  enabled: boolean; roles: string[]; created_at: number; must_set_password: boolean
}

export type Workspace = { id: string; tenant_id: string; name: string; created_at?: string }
export type Subscription = { id: string; tenant_id: string; offer: string; tier: string; created_at?: string }

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

export type StudioAsset = { id: string; url: string }
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


export type EngagementOffer = '27' | '28' | '29' | '30' | '31' | '32' | '33' | '34'

export type EngagementPlan = {
  tenant_id: string
  engagement_id: string
  offer: EngagementOffer
  version: number
  object_key: string
  sha256: string
  media_type: string
  status: string
}

export type EngagementDocument = {
  schema_version: string
  tenant_id: string
  engagement_id: string
  offer: EngagementOffer
  title: string
  scope: string
  requested_outcome: string
  commercial_status: 'source-only-unpriced'
  delivery_mode: 'plan-only'
  mandatory_controls: string[]
  prohibited_actions: string[]
  approval_requirement: string
  acceptance_gate: string
}

export type EngagementReview = {
  plan_sha256: string
  reviewer: string
  decision: 'approved' | 'rejected'
  reason: string
  reviewed_at_unix: number
}

export type EngagementSnapshot = {
  tenant_id: string
  engagement_id: string
  offer: EngagementOffer
  title: string
  status: 'validating' | 'planning' | 'awaiting-approval' | 'approved' | 'rejected' | 'approval-expired' | 'failed' | 'cancelled'
  /** Set by the orchestrator when the workflow ended without finishing (status then reads failed/cancelled). */
  error?: string | null
  stalled_status?: string | null
  commercial_status: 'source-only-unpriced'
  delivery_mode: 'plan-only'
  charges_enabled: false
  external_actions_enabled: false
  plan: EngagementPlan | null
  document: EngagementDocument | null
  review: EngagementReview | null
  started_at: string
  updated_at: string
}

export type EngagementStartInput = {
  engagement_id: string
  offer: EngagementOffer
  title: string
  scope: string
  requested_outcome: string
}

export type CampaignDraft = { draft_id: string; campaign_id: string; subject: string; email_body: string; whatsapp_text: string; social_text?: string; model: string; created_at: string }
export type CampaignResult = { cycle_id: string; channel: 'email' | 'whatsapp' | 'social'; status: 'sent' | 'test_address' | 'blocked' | 'failed'; count: number }
export type CampaignContact = { id: string; first_name: string; email: string | null; phone: string | null; email_consent: boolean; whatsapp_consent: boolean; source: string; created_at: string }
export type SocialAccount = { id: string; name: string; network: string; picture: string; disabled: boolean }
export type SocialStatus = { available: boolean; connected: boolean; accounts: SocialAccount[]; postiz_url?: string; problem?: string }
export type InsightMetrics = { paid_in_minor: number; topups?: number; campaign_spend_minor?: number; charged_minor: number; invoices: number; receipts: number; emails: number; whatsapp: number; social_posts: number; campaigns_sent: number; studio_packs: number; studio_images: number; contacts_added: number }
export type InsightMonth = { month: string; title: string; summary: string; metrics: InsightMetrics; updated_at: string | null }
export type ActivityRow = { trace_id: string; event_date: string; message: string; service: string; level: string; tenant_id: string }

/** Creators Home (Offer 35): a creator's channel and the video projects made for it. */
export type CreatorChannel = {
  id: string
  name: string
  youtube_handle: string
  niche: string
  audience: string
  language: string
  writing_instructions: string
  visual_preset: string
  default_voice: string
  created_at: string
  updated_at: string
}

export type CreatorStageState = 'pending' | 'running' | 'needs_approval' | 'approved' | 'done' | 'failed' | 'skipped'

export type CreatorStage = { key: string; label: string; kind: 'work' | 'approval'; state: CreatorStageState; updated_at: string | null; note: string }

export type CreatorScriptSection = { index: number; heading: string; text: string; words: number }

export type CreatorProject = {
  id: string
  channel_id: string
  title: string
  topic: string
  duration_minutes: number
  style: string
  audience: string
  version: number
  created_by: string
  created_at: string
  updated_at: string
  target_words: number
  current_stage: string | null
  stages: CreatorStage[]
  record: {
    title?: { chosen?: string; options?: string[]; source?: string }
    script?: { text: string; sections: CreatorScriptSection[]; words: number; target_words: number; estimated_minutes: number; source: string; updated_at: string }
    approvals?: Record<string, { by: string; at: string; note: string }>
    characters?: { name: string; look: string }[]
    seo?: { description: string; full_description: string; primary_keywords: string[]; secondary_keywords: string[]; long_tail_keywords: string[]
      tags: string[]; hashtags: string[]; chapters: { time: string; title: string }[]; pinned_comment: string; playlist: string; end_screen: string
      cards: { at: string; suggest: string }[]; keyword_note: string; made_at: string }
    video?: { key: string; captions_key: string; bytes: number; seconds: number; width: number; height: number; burned_subtitles: boolean; render_seconds: number; made_at: string }
    scenes?: CreatorScene[]
    thumbnail?: { options: { angle: string; caption: string; prompt: string; text_side: string; key: string; seed: number }[]; chosen: number | null; made_at: string }
    voiceover?: { voice: string; speed: number; script_sha256: string; total_seconds: number; provider: string; updated_at: string;
      sections: { index: number; heading: string; key: string; seconds: number }[] }
    youtube?: { video_id: string; url: string; studio_url: string; privacy: 'private' | 'unlisted' | 'public'; publish_at: string; channel: string; notes: string[]; uploaded_at: string }
    costs?: unknown[]
  }
}

export type CreatorYouTube = { configured: boolean; connected: boolean
  youtube: { youtube_channel_id: string; title: string; handle: string; subscribers: number | null; connected_at: string } | null }

export type CreatorEvent = { kind: string; stage: string; actor: string; detail: Record<string, unknown>; created_at: string }

export type CreatorJob = { id: string; kind: 'script' | 'voiceover' | 'scenes' | 'images' | 'thumbnail' | 'seo' | 'assembly' | 'publish'; status: 'queued' | 'running' | 'done' | 'failed'; progress: string; error: string;
  params: Record<string, unknown>; attempts: number; created_at: string; started_at: string | null; finished_at: string | null }

export type CreatorVoice = { id: string; name: string; gender: 'male' | 'female'; accent: string }

export type CreatorScene = { number: number; chapter: number; narration: string; visual: string; prompt: string; camera: string; lighting: string;
  seconds: number; seed: number; image: { key: string; prompt: string; model: string; made_at: string; stale?: boolean } | null }
