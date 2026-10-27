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

export type PageId = 'overview' | 'onboarding' | 'campaigns' | 'studio' | 'offers' | 'settings'
