import type { Bundle, Campaign, Offer, StudioPack, Tenant } from './types'

export const previewTenant: Tenant = {
  id: 'preview-tenant', name: 'Aster Foods', slug: 'aster-foods', lifecycle: 'active', version: 4,
  outbound_suspended: false, created_at: '2026-10-25T09:30:00Z', updated_at: '2026-10-27T11:45:00Z',
}

export const previewOffers: Offer[] = [
  { id: '01', name: 'Come-Back Engine', module: 'campaigns.lifecycle', commercial_status: 'operator-review-required', tiers: [
    { id: 'starter', setup_minor: 4000000, monthly_minor: 3500000, currency: 'NGN', entitlements: { list_max: 999, reactivation_touches: 3, full_list_campaigns: 2 } },
    { id: 'growth', setup_minor: 7500000, monthly_minor: 8500000, currency: 'NGN', entitlements: { list_max: 5000, reactivation_touches: 3, full_list_campaigns: 2 } },
    { id: 'pro', setup_minor: 15000000, monthly_minor: 15000000, currency: 'NGN', entitlements: { list_max: 0, reactivation_touches: 3, full_list_campaigns: 2, multi_branch: 1 } },
  ] },
  { id: '02', name: 'Content Cadence', module: 'campaigns.social', commercial_status: 'operator-review-required', tiers: [
    { id: 'starter', setup_minor: 2500000, monthly_minor: 3500000, currency: 'NGN', entitlements: { posts: 8, platforms: 1 } },
    { id: 'growth', setup_minor: 4000000, monthly_minor: 5500000, currency: 'NGN', entitlements: { posts: 16, platforms: 3, performance_report: 1 } },
    { id: 'pro', setup_minor: 7500000, monthly_minor: 12000000, currency: 'NGN', entitlements: { posts: 30, platforms: 0, reels: 2, ad_creative_support: 1, strategy_call: 1 } },
  ] },
  { id: '06', name: 'Shopfront Studio', module: 'studio', commercial_status: 'operator-review-required', tiers: [
    { id: 'starter', setup_minor: 0, monthly_minor: 0, pack_minor: 2000000, currency: 'NGN', turnaround_hours: 48, entitlements: { images: 5, studio_white: 1, lifestyle: 2 } },
    { id: 'growth', setup_minor: 0, monthly_minor: 0, pack_minor: 3000000, currency: 'NGN', turnaround_hours: 48, entitlements: { images: 7, studio_white: 1, lifestyle: 2, infographic: 1, before_after: 1 } },
    { id: 'pro', setup_minor: 0, monthly_minor: 0, pack_minor: 5500000, currency: 'NGN', turnaround_hours: 48, entitlements: { images: 12, short_video_loops: 1 } },
  ] },
]

export const previewBundles: Bundle[] = [{ id: 'growth-cadence-studio', setup_minor: 5000000, monthly_minor: 9900000, currency: 'NGN', required_offers: ['02:growth', '06:growth'], studio_packs_per_month: 2 }]

export const previewCampaigns: Campaign[] = [
  { id: 'camp-jul-winback', name: 'July win-back', client: 'Ikeja Fitness', cycle: 'cycle-2026-07', status: 'awaiting_approval', channels: ['Email', 'SMS', 'WhatsApp'], budgetMinor: 18000000, updated: '12 minutes ago' },
  { id: 'camp-weekly-cadence', name: 'Weekly founder notes', client: 'Aster Foods', cycle: 'cycle-2026-w43', status: 'approved', channels: ['Social', 'Email'], budgetMinor: 8500000, updated: 'Yesterday' },
  { id: 'camp-lekki-reactivation', name: 'Lekki reactivation', client: 'Northline Salon', cycle: 'cycle-2026-w42', status: 'completed', channels: ['SMS', 'WhatsApp'], budgetMinor: 12000000, updated: '24 Oct' },
]

export const previewPacks: StudioPack[] = [
  { id: 'pack-adire-tote', product: 'Adire tote launch', client: 'Aster Foods', tier: 'growth', status: 'awaiting_qa', assets: 7, updated: '18 minutes ago' },
  { id: 'pack-zobo-kit', product: 'Zobo discovery kit', client: 'Aster Foods', tier: 'starter', status: 'delivered', assets: 5, updated: '26 Oct' },
  { id: 'pack-shea-set', product: 'Shea care set', client: 'Northline Salon', tier: 'pro', status: 'rejected', assets: 12, updated: '25 Oct' },
]
