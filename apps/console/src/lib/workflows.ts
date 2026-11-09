/**
 * One honest model for every kind of work VolumeOS runs in Temporal (campaigns, Studio packs, offer jobs,
 * engagements), so the console can list, count and explain them the same way.
 *
 * Each workflow's own status is mapped to:
 *  - a STATE (what a person needs to know: running, waiting on a person, done, stopped, failed), and
 *  - a STAGE on the shared track Prepare → Human review → Reserve funds → Execute → Settle & deliver.
 * Approval is never shown as delivery: "approved" sits at review-done, delivery is its own later stage.
 */
import type { CampaignSnapshot, EngagementSnapshot, OfferJobSnapshot, StudioSnapshot } from './types.ts'

export type WorkKind = 'campaign' | 'studio' | 'offer-job' | 'engagement'
export type WorkState = 'running' | 'needs-review' | 'done' | 'stopped' | 'failed'
export type Stage = 'prepare' | 'review' | 'reserve' | 'execute' | 'deliver'

export const STAGES: { id: Stage; label: string }[] = [
  { id: 'prepare', label: 'Prepare' },
  { id: 'review', label: 'Human review' },
  { id: 'reserve', label: 'Reserve funds' },
  { id: 'execute', label: 'Execute' },
  { id: 'deliver', label: 'Settle & deliver' },
]

export const KIND_LABEL: Record<WorkKind, string> = {
  campaign: 'Campaign', studio: 'Studio pack', 'offer-job': 'Offer job', engagement: 'Engagement',
}

/** Which stages a kind actually goes through (engagements are plan-only: nothing is reserved or delivered). */
export const KIND_STAGES: Record<WorkKind, Stage[]> = {
  campaign: ['prepare', 'review', 'reserve', 'execute', 'deliver'],
  studio: ['prepare', 'review', 'deliver'],
  'offer-job': ['reserve', 'execute', 'review', 'deliver'],
  engagement: ['prepare', 'review'],
}

type Mapping = { label: string; state: WorkState; stage: Stage }

const CAMPAIGN: Record<string, Mapping> = {
  drafting: { label: 'Drafting', state: 'running', stage: 'prepare' },
  awaiting_approval: { label: 'Awaiting approval', state: 'needs-review', stage: 'review' },
  approved: { label: 'Approved', state: 'running', stage: 'reserve' },
  reserving: { label: 'Reserving funds', state: 'running', stage: 'reserve' },
  dispatching: { label: 'Sending', state: 'running', stage: 'execute' },
  settling: { label: 'Settling', state: 'running', stage: 'deliver' },
  completed: { label: 'Completed', state: 'done', stage: 'deliver' },
  approval_timed_out: { label: 'Approval timed out', state: 'stopped', stage: 'review' },
  cancelled: { label: 'Cancelled', state: 'stopped', stage: 'prepare' },
  failed: { label: 'Failed', state: 'failed', stage: 'execute' },
}

const STUDIO: Record<string, Mapping> = {
  generating: { label: 'Generating', state: 'running', stage: 'prepare' },
  awaiting_qa: { label: 'Awaiting QA', state: 'needs-review', stage: 'review' },
  approved: { label: 'Approved', state: 'running', stage: 'deliver' },
  delivering: { label: 'Delivering', state: 'running', stage: 'deliver' },
  delivered: { label: 'Delivered', state: 'done', stage: 'deliver' },
  rejecting: { label: 'Rejecting', state: 'running', stage: 'review' },
  rejected: { label: 'Rejected', state: 'stopped', stage: 'review' },
  qa_timed_out: { label: 'QA timed out', state: 'stopped', stage: 'review' },
  cancelled: { label: 'Cancelled', state: 'stopped', stage: 'prepare' },
  failed: { label: 'Failed', state: 'failed', stage: 'prepare' },
}

const OFFER_JOB: Record<string, Mapping> = {
  validating: { label: 'Validating', state: 'running', stage: 'reserve' },
  reserving: { label: 'Reserving funds', state: 'running', stage: 'reserve' },
  executing: { label: 'Running', state: 'running', stage: 'execute' },
  'awaiting-review': { label: 'Awaiting review', state: 'needs-review', stage: 'review' },
  releasing: { label: 'Releasing', state: 'running', stage: 'deliver' },
  released: { label: 'Released', state: 'done', stage: 'deliver' },
  rejected: { label: 'Rejected', state: 'stopped', stage: 'review' },
  failed: { label: 'Failed', state: 'failed', stage: 'execute' },
  cancelled: { label: 'Cancelled', state: 'stopped', stage: 'reserve' },
}

const ENGAGEMENT: Record<string, Mapping> = {
  validating: { label: 'Validating', state: 'running', stage: 'prepare' },
  planning: { label: 'Planning', state: 'running', stage: 'prepare' },
  'awaiting-approval': { label: 'Awaiting approval', state: 'needs-review', stage: 'review' },
  approved: { label: 'Plan approved', state: 'done', stage: 'review' },
  rejected: { label: 'Plan rejected', state: 'stopped', stage: 'review' },
  'approval-expired': { label: 'Approval expired', state: 'stopped', stage: 'review' },
  failed: { label: 'Failed', state: 'failed', stage: 'prepare' },
  cancelled: { label: 'Cancelled', state: 'stopped', stage: 'prepare' },
}

const TABLES: Record<WorkKind, Record<string, Mapping>> = { campaign: CAMPAIGN, studio: STUDIO, 'offer-job': OFFER_JOB, engagement: ENGAGEMENT }

export function describe(kind: WorkKind, status: string): Mapping {
  return TABLES[kind][status] ?? { label: humanise(status), state: 'running', stage: KIND_STAGES[kind][0] }
}

export function humanise(value: string): string {
  const words = value.replaceAll('_', ' ').replaceAll('-', ' ').trim()
  return words ? words[0].toUpperCase() + words.slice(1) : 'Unknown'
}

export type WorkItem = {
  kind: WorkKind
  id: string
  title: string
  offer: string | null
  status: string
  label: string
  state: WorkState
  stage: Stage
  amountMinor: number | null
  currency: string
  startedAt: string
  updatedAt: string
  /** Where a person reviews or inspects it in the console. */
  href: string
  /** The server's explanation when the workflow ended without finishing. */
  error: string | null
  /** For a failed workflow: the status it was in when it stopped. */
  stalledAt: string | null
}

type Extra = { error?: string | null; stalled_status?: string | null }

function base(kind: WorkKind, id: string, status: string, startedAt: string, updatedAt: string, extra: Extra) {
  const mapping = describe(kind, status)
  return {
    kind, id, status, label: mapping.label, state: mapping.state,
    stage: extra.stalled_status ? describe(kind, extra.stalled_status).stage : mapping.stage,
    startedAt, updatedAt, error: extra.error ?? null, stalledAt: extra.stalled_status ?? null,
  }
}

export function fromCampaign(s: CampaignSnapshot & Extra): WorkItem {
  return { ...base('campaign', s.campaign_id, s.status, s.started_at, s.updated_at, s), title: s.campaign_id, offer: null,
    amountMinor: s.amount_minor, currency: s.currency, href: `/services/campaigns/${encodeURIComponent(s.campaign_id)}` }
}

export function fromStudio(s: StudioSnapshot & Extra): WorkItem {
  return { ...base('studio', s.pack_id, s.status, s.started_at, s.updated_at, s), title: s.product_id || s.pack_id, offer: '06',
    amountMinor: null, currency: 'NGN', href: `/services/studio/${encodeURIComponent(s.pack_id)}` }
}

export function fromOfferJob(s: OfferJobSnapshot & Extra): WorkItem {
  return { ...base('offer-job', s.job_id, s.status, s.started_at, s.updated_at, s), title: s.job_id, offer: s.offer,
    amountMinor: s.amount_minor, currency: s.currency, href: `/services/offer-jobs/${encodeURIComponent(s.job_id)}` }
}

export function fromEngagement(s: EngagementSnapshot & Extra): WorkItem {
  return { ...base('engagement', s.engagement_id, s.status, s.started_at, s.updated_at, s), title: s.title || s.engagement_id,
    offer: s.offer, amountMinor: null, currency: 'NGN', href: `/engagements/${encodeURIComponent(s.engagement_id)}` }
}

/** Newest activity first. */
export function byRecent(items: WorkItem[]): WorkItem[] {
  return [...items].sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''))
}

/** Waiting longest first: the order a reviewer should work through. */
export function byWaiting(items: WorkItem[]): WorkItem[] {
  return [...items].sort((a, b) => (a.updatedAt || '').localeCompare(b.updatedAt || ''))
}

export function countBy(items: WorkItem[]): Record<WorkKind, Record<WorkState, number>> {
  const empty = (): Record<WorkState, number> => ({ running: 0, 'needs-review': 0, done: 0, stopped: 0, failed: 0 })
  const counts: Record<WorkKind, Record<WorkState, number>> = { campaign: empty(), studio: empty(), 'offer-job': empty(), engagement: empty() }
  for (const item of items) counts[item.kind][item.state] += 1
  return counts
}
