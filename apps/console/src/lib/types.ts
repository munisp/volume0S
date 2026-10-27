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

export type Campaign = {
  id: string
  name: string
  client: string
  cycle: string
  status: 'awaiting_approval' | 'approved' | 'dispatching' | 'completed'
  channels: string[]
  budgetMinor: number
  updated: string
}

export type StudioPack = {
  id: string
  product: string
  client: string
  tier: 'starter' | 'growth' | 'pro'
  status: 'awaiting_qa' | 'approved' | 'rejected' | 'delivered'
  assets: number
  updated: string
}

export type PageId = 'overview' | 'onboarding' | 'campaigns' | 'studio' | 'offers' | 'settings'
