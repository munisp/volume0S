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
  { id: '03', name: 'Call Quality Scorecards', module: 'scorecards', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '04', name: 'AI Front Desk', module: 'conversations', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '05', name: 'Books on Autopilot', module: 'books', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '06', name: 'Shopfront Studio', module: 'studio', commercial_status: 'operator-review-required', tiers: [
    { id: 'starter', setup_minor: 0, monthly_minor: 0, pack_minor: 2000000, currency: 'NGN', turnaround_hours: 48, entitlements: { images: 5, studio_white: 1, lifestyle: 2 } },
    { id: 'growth', setup_minor: 0, monthly_minor: 0, pack_minor: 3000000, currency: 'NGN', turnaround_hours: 48, entitlements: { images: 7, studio_white: 1, lifestyle: 2, infographic: 1, before_after: 1 } },
    { id: 'pro', setup_minor: 0, monthly_minor: 0, pack_minor: 5500000, currency: 'NGN', turnaround_hours: 48, entitlements: { images: 12, short_video_loops: 1 } },
  ] },
  { id: '07', name: 'Compliance Watchtower', module: 'compliance', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '08', name: 'Funding Readiness', module: 'funding', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '09', name: 'Offline POS & Cash Control', module: 'commerce.pos', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '10', name: 'Inventory & Reorder Autopilot', module: 'commerce.inventory', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '11', name: 'Social Storefront & Order Capture', module: 'commerce.storefront', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '12', name: 'Quotes & Sales Pipeline', module: 'commerce.quotes', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '13', name: 'Deposits & Instalment Orders', module: 'commerce.instalments', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '14', name: 'Bookings & Service Dispatch', module: 'commerce.bookings', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '15', name: 'Loyalty & Referral Rewards', module: 'commerce.loyalty', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '16', name: 'Purchasing & Landed Cost', module: 'commerce.purchasing', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '17', name: 'Omnichannel Support & SLA', module: 'commerce.support', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '18', name: 'Field Sales & Van Stock', module: 'commerce.fieldsales', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '19', name: 'Language Multiplier', module: 'media.dubbing', commercial_status: 'operator-review-required', tiers: [
    { id: 'starter', setup_minor: 4000000, monthly_minor: 4500000, currency: 'NGN', entitlements: { videos_per_month: 2, languages: 1 } },
    { id: 'growth', setup_minor: 6000000, monthly_minor: 7500000, currency: 'NGN', entitlements: { videos_per_month: 4, languages: 2, subtitles: 1 } },
    { id: 'pro', setup_minor: 10000000, monthly_minor: 12000000, currency: 'NGN', entitlements: { videos_per_month: 8, languages: 3, series_handling: 1 } },
  ] },
  { id: '20', name: 'Vibe-Code Audit', module: 'security.audit', commercial_status: 'operator-review-required', tiers: [
    { id: 'audit', setup_minor: 0, monthly_minor: 0, pack_minor: 22000000, currency: 'NGN', entitlements: { documented_findings: 1, prioritized_fix_list: 1, evidence_pack: 1 } },
    { id: 're-audit', setup_minor: 0, monthly_minor: 8500000, currency: 'NGN', entitlements: { release_re_audit: 1, release_note_review: 1 } },
  ] },
  { id: '21', name: 'Department Copilot', module: 'knowledge.copilot', commercial_status: 'operator-review-required', tiers: [
    { id: 'department', setup_minor: 12000000, monthly_minor: 6500000, currency: 'NGN', entitlements: { departments: 1, call_preparation: 1, same_day_follow_up: 1, monday_pipeline_review: 1 } },
  ] },
  { id: '22', name: 'Intent Radar', module: 'leads.intent', commercial_status: 'operator-review-required', tiers: [
    { id: 'daily-digest', setup_minor: 3000000, monthly_minor: 5500000, currency: 'NGN', entitlements: { opportunities_per_day: 5, competitor_gripe_log: 1, human_voice_review: 1 } },
  ] },
  { id: '23', name: 'Monday Dashboard', module: 'analytics.dashboard', commercial_status: 'operator-review-required', tiers: [
    { id: 'monday', setup_minor: 12000000, monthly_minor: 7000000, currency: 'NGN', entitlements: { data_sources: 3, kpi_metrics: 10, monday_digest: 1 } },
  ] },
  { id: '24', name: 'Paperless Office', module: 'documents.archive', commercial_status: 'operator-review-required', tiers: [
    { id: 'archive', setup_minor: 10000000, monthly_minor: 5000000, currency: 'NGN', entitlements: { searchable_archive: 1, archive_qa: 1, retention_policy: 1 } },
  ] },
  { id: '25', name: 'Collections Engine', module: 'collections.receivables', commercial_status: 'operator-review-required', tiers: [
    { id: 'collections', setup_minor: 5000000, monthly_minor: 5500000, currency: 'NGN', entitlements: { reminder_steps: 4, weekly_receivables_report: 1, human_escalation: 1 } },
  ] },
  { id: '26', name: 'Attribution Engine', module: 'marketing.attribution', commercial_status: 'operator-review-required', tiers: [
    { id: 'attribution', setup_minor: 3000000, monthly_minor: 4000000, currency: 'NGN', entitlements: { qr_artwork: 1, monthly_attribution_report: 1, whatsapp_clickthrough: 1, keyword_fallback: 1 } },
  ] },
  { id: '27', name: 'Website Makeover Studio', module: 'web.makeover', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '28', name: 'Video Ad Packs', module: 'media.ads', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '29', name: 'Course Tutor', module: 'education.tutor', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '30', name: 'AI Team Install', module: 'agents.teams', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '31', name: 'Replacement Part Design', module: 'cad.parts', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '32', name: 'Industry Audio Briefings', module: 'research.briefings', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '33', name: 'Bid Radar', module: 'procurement.radar', commercial_status: 'source-only-unpriced', tiers: [] },
  { id: '34', name: 'Website Guard', module: 'monitoring.website', commercial_status: 'source-only-unpriced', tiers: [] },
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
