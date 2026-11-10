// Run: node --test src/lib/   (Node 24 strips the types itself; no extra test dependency)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { allowed, can, needs, personaOf, rolesFromToken, sees } from './access.ts'
import { byWaiting, countBy, describe, fromEngagement, fromOfferJob, matchesWork } from './workflows.ts'
import { plainError } from './messages.ts'
import { VolumeOSApi } from './api.ts'
import type { EngagementSnapshot, OfferJobSnapshot } from './types.ts'

const token = (claims: object) => `x.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.y`

test('roles come from both claims, without Keycloak defaults', () => {
  const roles = rolesFromToken(token({ roles: ['delivery'], realm_access: { roles: ['default-roles-volumeos', 'offline_access', 'analyst'] } }))
  assert.deepEqual([...roles].sort(), ['analyst', 'delivery'])
  assert.equal(rolesFromToken('not a token').size, 0)
  assert.equal(rolesFromToken(null).size, 0)
})

test('the console applies the servers\' rule', () => {
  const reader = new Set(['reader'])
  const finance = new Set(['finance'])
  const operator = new Set(['super-operator'])
  assert.ok(can(reader, 'reader'))
  assert.ok(can(finance, 'reader'), 'finance is a reader-class role')
  assert.ok(!can(reader, 'delivery'))
  assert.ok(can(operator, 'finance'))
  assert.ok(!allowed(reader, 'startEngagement'))
  assert.ok(allowed(new Set(['analyst']), 'reviewEngagement'), 'analysts review engagements')
  assert.ok(!allowed(new Set(['analyst']), 'startEngagement'))
  assert.ok(allowed(new Set(['client-owner']), 'topUp'))
  assert.equal(needs('reviewOfferJob'), 'Needs the Delivery or Analyst role')
})

test('staff get the operator console, client owners the client workspace', () => {
  assert.equal(personaOf(new Set(['delivery'])), 'operator')
  assert.equal(personaOf(new Set(['reader'])), 'operator')
  assert.equal(personaOf(new Set(['client-owner'])), 'client')
  assert.equal(personaOf(new Set(['client-owner', 'sales'])), 'operator')
  assert.equal(personaOf(new Set()), 'none')
  assert.ok(!sees(new Set(['reader']), 'approvals'), 'a reader has nothing to approve')
  assert.ok(!sees(new Set(['delivery']), 'administration'))
  assert.ok(sees(new Set(['super-operator']), 'administration'))
})

const engagement = (status: string, extra: object = {}) => ({
  tenant_id: 't', engagement_id: `e-${status}`, offer: '27', title: 'Plan', status, commercial_status: 'source-only-unpriced',
  delivery_mode: 'plan-only', charges_enabled: false, external_actions_enabled: false, plan: null, document: null, review: null,
  started_at: '2026-11-08T10:00:00Z', updated_at: '2026-11-09T10:00:00Z', ...extra,
}) as unknown as EngagementSnapshot

test('approved is not delivered, and a failed workflow says where it stopped', () => {
  assert.equal(describe('campaign', 'approved').state, 'running', 'an approved campaign still has to reserve, send and settle')
  assert.equal(describe('campaign', 'approved').stage, 'reserve')
  assert.equal(describe('engagement', 'awaiting-approval').state, 'needs-review')
  const failed = fromEngagement(engagement('failed', { stalled_status: 'planning', error: 'workflow failed' }))
  assert.equal(failed.state, 'failed')
  assert.equal(failed.stage, 'prepare')
  assert.equal(failed.stalledAt, 'planning')
  assert.equal(failed.href, '/engagements/e-failed')
})

test('a workflow whose worker can\'t answer reads as not answering, not as progress', () => {
  for (const kind of ['campaign', 'studio', 'offer-job', 'engagement'] as const) {
    assert.deepEqual([describe(kind, 'unavailable').label, describe(kind, 'unavailable').state], ['Not answering', 'failed'])
  }
})

test('unknown statuses are shown as they are, never hidden', () => {
  assert.equal(describe('studio', 'brand_new_state').label, 'Brand new state')
})

test('the review queue lists the longest wait first, and counts add up', () => {
  const job = (id: string, status: string, updated: string) => fromOfferJob({ tenant_id: 't', job_id: id, offer: '08', amount_minor: 100,
    currency: 'NGN', status, artifact: null, approval_count: 0, started_at: updated, updated_at: updated } as OfferJobSnapshot)
  const items = [job('b', 'awaiting-review', '2026-11-09T09:00:00Z'), job('a', 'awaiting-review', '2026-11-08T09:00:00Z'), job('c', 'failed', '2026-11-09T11:00:00Z')]
  assert.deepEqual(byWaiting(items).map(i => i.id), ['a', 'b', 'c'])
  const counts = countBy(items)
  assert.equal(counts['offer-job']['needs-review'], 2)
  assert.equal(counts['offer-job'].failed, 1)
})

test('search ignores surrounding spaces and finds offer names', () => {
  const item = fromEngagement(engagement('awaiting-approval', { engagement_id: 'e2e-155811-c', title: 'E2E console plan 155811', offer: '29' }))
  const name = (id: string | null) => (id === '29' ? 'Course Tutor' : '')
  assert.ok(matchesWork(item, '  155811  ', name))
  assert.ok(matchesWork(item, 'course tutor', name))
  assert.ok(matchesWork(item, '', name))
  assert.ok(!matchesWork(item, 'website', name))
})

test('refusals read as plain words', () => {
  assert.equal(plainError({ status: 403, code: 'forbidden', message: 'required role: delivery' }), 'Your role can\'t do this. It needs the Delivery role.')
  assert.equal(plainError({ status: 409, code: 'conflict', message: 'resource exists or version is stale' }),
    'That already exists, or it changed since you opened it. Refresh and try again.')
  assert.equal(plainError({ status: 422, code: 'invalid_state', message: 'invalid state change' }),
    'Some details weren\'t accepted. Check the lengths and formats, then try again.')
  assert.equal(plainError({ status: 503, message: '' }), 'This service isn\'t running on this deployment right now.')
  assert.equal(plainError({ status: 403, code: 'forbidden', message: 'Required role: delivery or analyst.' }), 'Your role can\'t do this. It needs the Delivery or Analyst role.')
  assert.equal(plainError({ status: 422, code: 'invalid_state', message: 'invalid state change: for email, the recipient must be an email address' }),
    'for email, the recipient must be an email address')
  assert.equal(plainError({ status: 400, code: 'invalid_request', message: 'Check these fields: title, offer.' }), 'Check these fields: title, offer.')
  assert.equal(plainError({ status: 409, code: 'conflict', message: 'Engagement ID already exists.' }), 'Engagement ID already exists.', 'specific server text is kept')
  assert.equal(plainError({ status: 503, code: 'money_service_unavailable', message: 'Money service is unavailable.' }),
    'Billing isn\'t running on this deployment, so this can\'t be done yet.')
})

test('a top-up sends the amount as a whole number of kobo, as the orchestrator requires', async () => {
  const sent: unknown[] = []
  const realFetch = globalThis.fetch
  globalThis.fetch = (async (_url: string, init: { body: string }) => {
    sent.push(JSON.parse(init.body))
    return new Response(JSON.stringify({ status: 'awaiting_confirmation', checkout_url: 'https://checkout.example/x' }), { status: 200, headers: { 'content-type': 'application/json' } })
  }) as typeof fetch
  try {
    const api = new VolumeOSApi('https://core', 'https://orch', async () => 'token')
    await api.startTopup('paystack', 'ref-1', { amount_minor: '2500000', currency: 'NGN', customer_email: 'a@b.ng', redirect_url: 'https://x/cb' })
    assert.equal((sent[0] as { amount_minor: unknown }).amount_minor, 2500000)
  } finally { globalThis.fetch = realFetch }
})
