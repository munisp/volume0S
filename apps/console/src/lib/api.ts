import type { ContactInput } from './contacts'
import type {
  ArAgingReport, Balance, BillingCycleSnapshot, BillingDocument, Bundle, CampaignContact, CampaignDraft, CampaignResult, CampaignSnapshot,
  Checkout, CollectionReceipt, CollectionResolution, ExportResponse, Offer, PartnerStatement,
  StudioAssetResponse, StudioSnapshot, Subscription, Tenant, TrialBalance, Workspace, TenantSummary, Person,
  OfferJobSnapshot, ConversationQueueItem, ConversationDraft, EngagementSnapshot, EngagementStartInput,
} from './types'
import { plainError } from './messages.ts'

/** A refusal or failure, with a message a person can act on. `detail` keeps the server's own text for diagnostics. */
export class ApiError extends Error {
  status: number
  code: string
  detail: string

  constructor(message: string, status = 0, code = '') {
    super(plainError({ message, status, code }))
    this.status = status
    this.code = code
    this.detail = message
  }
}

export class VolumeOSApi {
  private coreUrl: string
  private orchestratorUrl: string
  private conversationsUrl: string
  private token: () => Promise<string | null>
  /** Platform operators only: the business they are working on, sent as X-VolumeOS-Tenant. The servers ignore it for
   *  anyone else, so it can never widen a client's or a business's own staff's access. */
  actingTenant: string | null = null

  constructor(coreUrl: string, orchestratorUrl: string, token: () => Promise<string | null>, conversationsUrl = '') {
    this.coreUrl = coreUrl
    this.orchestratorUrl = orchestratorUrl
    this.conversationsUrl = conversationsUrl
    this.token = token
  }

  get coreConfigured() { return Boolean(this.coreUrl) }
  get orchestratorConfigured() { return Boolean(this.orchestratorUrl) }
  get conversationsConfigured() { return Boolean(this.conversationsUrl) }

  offers() {
    return this.request<{ price_book: string; offers: Offer[]; bundles: Bundle[] }>(this.coreUrl, '/v1/offers', {}, false)
  }

  tenant() { return this.request<Tenant>(this.coreUrl, '/v1/tenants/current') }

  /** `tenant` (platform operators): create the record of a business other than the one being viewed. */
  createTenant(name: string, slug: string, tenant?: string) {
    return this.request<Tenant>(this.coreUrl, '/v1/tenants/current', { method: 'POST', body: { name, slug }, tenant })
  }

  /** Version-guarded: if the record changed since it was read, the server refuses (409) instead of overwriting. */
  transitionTenant(to: string, version: number, reason: string, tenant?: string) {
    return this.request<Tenant>(this.coreUrl, '/v1/tenants/current/transitions', { method: 'POST', body: { to, version, reason }, tenant })
  }

  /** Every business: platform operators only. */
  tenants() {
    return this.request<{ tenants: TenantSummary[] }>(this.coreUrl, '/v1/tenants', { tenant: '' })
  }

  people() {
    return this.request<{ people: Person[] }>(this.orchestratorUrl, '/v1/people')
  }

  /** Creates the sign-in; its one-time password is emailed to the person (never shown here). */
  addPerson(input: { email: string; first_name: string; last_name: string; role: string; business_name?: string }, tenant?: string) {
    return this.request<{ person: Person; sign_in_email: 'sent' | 'not_sent_test_address' }>(this.orchestratorUrl, '/v1/people', { method: 'POST', body: input, tenant })
  }

  /** Emails a fresh one-time password to someone who hasn't signed in yet (their first email went missing). */
  resendSignIn(id: string, businessName?: string) {
    return this.request<{ person: Person; sign_in_email: string }>(this.orchestratorUrl, `/v1/people/${encodeURIComponent(id)}`,
      { method: 'PATCH', body: { send_sign_in: true, business_name: businessName } })
  }

  updatePerson(id: string, change: { role?: string; enabled?: boolean }) {
    return this.request<{ person: Person }>(this.orchestratorUrl, `/v1/people/${encodeURIComponent(id)}`, { method: 'PATCH', body: change })
  }

  createWorkspace(name: string) {
    return this.request<Workspace>(this.coreUrl, '/v1/workspaces', { method: 'POST', body: { name } })
  }

  createSubscription(offer: string, tier: string, tenant?: string) {
    return this.request<Subscription>(this.coreUrl, '/v1/subscriptions', { method: 'POST', body: { offer, tier }, tenant })
  }

  setOutboundSuspension(suspended: boolean, reason: string) {
    return this.request<Tenant>(this.coreUrl, '/v1/tenants/current/outbound-suspension', {
      method: 'PUT', body: { suspended, reason },
    })
  }

  setSuppression(channel: string, recipient: string, reason: string, suppressed: boolean) {
    return this.request<{ status: string }>(this.coreUrl, '/v1/suppressions', {
      method: suppressed ? 'PUT' : 'DELETE', body: { channel, recipient, reason },
    })
  }

  campaigns() {
    return this.request<{ campaigns: CampaignSnapshot[] }>(this.orchestratorUrl, '/v1/campaigns')
  }

  startCampaign(input: { campaign_id: string; brief: string; amount_minor: number; currency: string; cycle_id: string }) {
    return this.request<{ status: string; campaign_id: string }>(this.orchestratorUrl, '/v1/campaigns', { method: 'POST', body: input })
  }

  /** The AI draft a person approves, and the per-channel results once it was sent. */
  campaignDetail(campaignId: string) {
    return this.request<{ draft: CampaignDraft | null; results: CampaignResult[] }>(this.orchestratorUrl, `/v1/campaigns/${encodeURIComponent(campaignId)}/detail`)
  }

  contacts(limit = 50, offset = 0) {
    return this.request<{ contacts: CampaignContact[]; total: number; reachable: { email?: number; whatsapp?: number } }>(this.orchestratorUrl, `/v1/contacts?limit=${limit}&offset=${offset}`)
  }

  importContacts(contacts: ContactInput[]) {
    return this.request<{ added: number; updated: number }>(this.orchestratorUrl, '/v1/contacts/import', { method: 'POST', body: { contacts } })
  }

  deleteContact(id: string) {
    return this.request<{ deleted: boolean }>(this.orchestratorUrl, `/v1/contacts/${encodeURIComponent(id)}`, { method: 'DELETE' })
  }

  approveCampaign(campaignId: string, approvalId: string) {
    return this.request(this.orchestratorUrl, `/v1/campaigns/${encodeURIComponent(campaignId)}/approval`, {
      method: 'POST', body: { approval_id: approvalId },
    })
  }

  studioPacks() {
    return this.request<{ packs: StudioSnapshot[] }>(this.orchestratorUrl, '/v1/studio/packs')
  }

  /** Stores a product photo under this business; returns the key a Studio pack starts from. */
  uploadStudioPhoto(file: Blob) {
    return this.request<{ source_image: string }>(this.orchestratorUrl, '/v1/studio/uploads', { method: 'POST', raw: file })
  }

  startStudioPack(input: Record<string, string>) {
    return this.request<{ status: string; pack_id: string }>(this.orchestratorUrl, '/v1/studio/packs', { method: 'POST', body: input })
  }

  /** Fetches a protected file (an image or download) with the user's token; returns a local blob: URL for it. */
  async blobUrl(path: string): Promise<string> {
    const accessToken = await this.token()
    if (!accessToken) throw new ApiError('Sign in to continue.', 401)
    const headers: Record<string, string> = { Authorization: `Bearer ${accessToken}` }
    if (this.actingTenant) headers['X-VolumeOS-Tenant'] = this.actingTenant
    const response = await fetch(this.orchestratorUrl + path, { headers, cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(30000) })
    if (!response.ok) throw new ApiError(`The file couldn't be loaded (${response.status}).`, response.status)
    return URL.createObjectURL(await response.blob())
  }

  studioDelivered(packId: string) {
    return this.request<{ pack_id: string; files: { path: string; url: string }[] }>(this.orchestratorUrl, `/v1/studio/packs/${encodeURIComponent(packId)}/delivered`)
  }

  studioAssets(packId: string) {
    return this.request<StudioAssetResponse>(this.orchestratorUrl, `/v1/studio/packs/${encodeURIComponent(packId)}/assets`)
  }

  reviewStudioPack(packId: string, decision: 'approve' | 'reject', reason: string, manifestSha256: string) {
    return this.request(this.orchestratorUrl, `/v1/studio/packs/${encodeURIComponent(packId)}/review`, {
      method: 'POST', body: { decision, reason, manifest_sha256: manifestSha256 },
    })
  }

  offerJobs() {
    return this.request<{ jobs: OfferJobSnapshot[] }>(this.orchestratorUrl, '/v1/offers/jobs')
  }

  startOfferJob(input: Record<string, unknown>) {
    return this.request<{ status: string; job_id: string }>(this.orchestratorUrl, '/v1/offers/jobs', { method: 'POST', body: input })
  }

  reviewOfferJob(jobId: string, decision: 'approved' | 'rejected', reason: string, artifactSha256: string) {
    return this.request(this.orchestratorUrl, `/v1/offers/jobs/${encodeURIComponent(jobId)}/review`, {
      method: 'POST', body: { decision, reason, artifact_sha256: artifactSha256 },
    })
  }

  engagements() {
    return this.request<{ engagements: EngagementSnapshot[] }>(this.orchestratorUrl, '/v1/engagements')
  }

  startEngagement(input: EngagementStartInput) {
    return this.request<{ status: string; engagement_id: string; commercial_status: string; delivery_mode: string }>(
      this.orchestratorUrl, '/v1/engagements', { method: 'POST', body: input },
    )
  }

  engagement(engagementId: string) {
    return this.request<EngagementSnapshot>(
      this.orchestratorUrl, `/v1/engagements/${encodeURIComponent(engagementId)}`,
    )
  }

  reviewEngagement(engagementId: string, decision: 'approved' | 'rejected', reason: string, planSha256: string) {
    return this.request<{ status: string; engagement_id: string }>(
      this.orchestratorUrl, `/v1/engagements/${encodeURIComponent(engagementId)}/review`, {
        method: 'POST', body: { decision, reason, plan_sha256: planSha256 },
      },
    )
  }

  conversationQueue() {
    return this.request<{ conversations: ConversationQueueItem[] }>(this.conversationsUrl, '/v1/conversations')
  }

  reviewConversation(conversationId: string, draft: ConversationDraft, decision: 'approved' | 'rejected', reason: string) {
    const path = `/v1/conversations/${encodeURIComponent(conversationId)}/drafts/${encodeURIComponent(draft.draft_sha256)}/review`
    return this.request<ConversationDraft>(this.conversationsUrl, path, { method: 'POST', body: { decision, reason } })
  }

  sendConversation(conversationId: string, draftSha256: string) {
    const path = `/v1/conversations/${encodeURIComponent(conversationId)}/drafts/${encodeURIComponent(draftSha256)}/send`
    return this.request<{ status: string; provider_message_id: string }>(this.conversationsUrl, path, { method: 'POST' })
  }

  bookConversation(conversationId: string, slotId: string) {
    return this.request<Record<string, unknown>>(this.conversationsUrl,
      `/v1/conversations/${encodeURIComponent(conversationId)}/bookings`, { method: 'POST', body: { slot_id: slotId } })
  }

  suppressConversation(conversationId: string, reason: string) {
    return this.request<{ status: string }>(this.conversationsUrl,
      `/v1/conversations/${encodeURIComponent(conversationId)}/suppression`, { method: 'POST', body: { reason } })
  }

  balance(currency: 'NGN' | 'USD') {
    return this.request<Balance>(this.orchestratorUrl, `/v1/billing/balance/${currency}`)
  }

  startTopup(provider: string, reference: string, input: Record<string, string>) {
    // The orchestrator takes amount_minor as a whole number (pydantic StrictInt); a string was refused with
    // "Check these fields: amount_minor", so no top-up could start from the console. Amounts are capped at
    // 10^12 kobo, well inside the integers a JavaScript number holds exactly.
    const body = { ...input, amount_minor: Number(input.amount_minor) }
    return this.request<Checkout>(this.orchestratorUrl, `/v1/billing/topups/${encodeURIComponent(provider)}/${encodeURIComponent(reference)}`, {
      method: 'POST', body,
    })
  }

  billingCycles() {
    return this.request<{ cycles: BillingCycleSnapshot[] }>(this.orchestratorUrl, '/v1/billing/cycles')
  }

  startBillingCycle(input: Record<string, unknown>) {
    return this.request<{ status: string; subscription_id: string; cycle: string }>(this.orchestratorUrl, '/v1/billing/cycles', {
      method: 'POST', body: input,
    })
  }

  recoverBilling(input: Record<string, string>) {
    return this.request<{ status: string; recovery_reference: string }>(this.orchestratorUrl, '/v1/billing/recovery', {
      method: 'POST', body: input,
    })
  }

  openCollectionSuspense() {
    return this.request<{ receipts: CollectionReceipt[] }>(this.orchestratorUrl, '/v1/billing/collection-suspense')
  }

  resolveCollectionSuspense(receipt: CollectionReceipt, tenantId: string, reason: string) {
    return this.request<CollectionResolution>(
      this.orchestratorUrl,
      `/v1/billing/collection-suspense/${encodeURIComponent(receipt.provider)}/${encodeURIComponent(receipt.reference)}/resolutions`,
      { method: 'POST', body: { tenant_id: tenantId, reason } },
    )
  }

  billingDocuments() {
    return this.request<{ documents: BillingDocument[] }>(this.orchestratorUrl, '/v1/billing/documents')
  }

  arAging(asOf: string) {
    return this.request<ArAgingReport>(this.orchestratorUrl, `/v1/billing/reports/ar-aging?as_of=${encodeURIComponent(asOf)}`)
  }

  exportArAging(asOf: string) {
    return this.request<ExportResponse<ArAgingReport>>(this.orchestratorUrl, '/v1/billing/reports/ar-aging/exports', {
      method: 'POST', body: { as_of: asOf },
    })
  }

  partnerStatement(partnerId: string, start: string, end: string) {
    const query = `start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`
    return this.request<PartnerStatement>(this.orchestratorUrl, `/v1/billing/reports/partner-share/${encodeURIComponent(partnerId)}?${query}`)
  }

  exportPartnerStatement(partnerId: string, start: string, end: string) {
    return this.request<ExportResponse<PartnerStatement>>(this.orchestratorUrl, `/v1/billing/reports/partner-share/${encodeURIComponent(partnerId)}/exports`, {
      method: 'POST', body: { start, end },
    })
  }

  createPartnerContract(input: Record<string, string | number | null>) {
    return this.request<Record<string, unknown>>(this.orchestratorUrl, '/v1/billing/partner-contracts', { method: 'POST', body: input })
  }

  trialBalance(currency: 'NGN' | 'USD') {
    return this.request<TrialBalance>(this.orchestratorUrl, `/v1/billing/reports/trial-balance/${currency}`)
  }

  exportTrialBalance(currency: 'NGN' | 'USD') {
    return this.request<ExportResponse<TrialBalance>>(this.orchestratorUrl, `/v1/billing/reports/trial-balance/${currency}/exports`, { method: 'POST' })
  }

  documentPdf(kind: string, documentId: string) {
    return this.download(`/v1/billing/documents/${encodeURIComponent(kind)}/${encodeURIComponent(documentId)}/pdf`)
  }

  financialExport(kind: string, digest: string) {
    return this.download(`/v1/billing/financial-exports/${encodeURIComponent(kind)}/${encodeURIComponent(digest)}`)
  }

  private async download(path: string): Promise<Blob> {
    if (!this.orchestratorUrl) throw new ApiError('The orchestrator API is not configured.')
    const accessToken = await this.token()
    if (!accessToken) throw new ApiError('Sign in with the tenant-scoped OIDC account to continue.', 401)
    const response = await fetch(this.orchestratorUrl + path, {
      headers: { Authorization: `Bearer ${accessToken}` }, cache: 'no-store', credentials: 'omit',
      signal: AbortSignal.timeout(15000),
    })
    if (!response.ok) {
      let message = `Download failed with HTTP ${response.status}.`
      try {
        const value = await response.json() as { error?: { message?: string } }
        message = value.error?.message || message
      } catch { /* Preserve the safe HTTP error when the body is not JSON. */ }
      throw new ApiError(message, response.status)
    }
    return response.blob()
  }

  private async request<T>(base: string, path: string, options: {
    method?: string
    body?: unknown
    /** A file sent as-is (e.g. a product photo) instead of a JSON body. */
    raw?: Blob
    /** Business to act on (platform operators); '' sends none, undefined uses actingTenant. */
    tenant?: string
  } = {}, authenticated = true): Promise<T> {
    if (!base) throw new ApiError('This API is not configured for the current deployment.')
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (authenticated) {
      const accessToken = await this.token()
      if (!accessToken) throw new ApiError('Sign in with the tenant-scoped OIDC account to continue.', 401)
      headers.Authorization = `Bearer ${accessToken}`
      const scope = options.tenant ?? this.actingTenant
      if (scope) headers['X-VolumeOS-Tenant'] = scope
    }
    if (options.body !== undefined) headers['Content-Type'] = 'application/json'
    if (options.raw) headers['Content-Type'] = options.raw.type || 'application/octet-stream'
    const response = await fetch(base + path, {
      method: options.method ?? 'GET',
      headers,
      body: options.raw ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
      cache: 'no-store',
      credentials: 'omit',
      signal: AbortSignal.timeout(15000),
    })
    const text = await response.text()
    let value: unknown = null
    if (text) {
      try { value = JSON.parse(text) } catch {
        // A stopped service answers through the gateway with an HTML error page, not JSON: report the outage itself.
        throw new ApiError(response.ok ? 'The service sent an answer the console can\'t read.' : '', response.status)
      }
    }
    if (!response.ok) {
      const document = value as { error?: { message?: string; code?: string }; detail?: string } | null
      const detail = document?.error?.message || document?.detail
      throw new ApiError(detail || `Request failed with HTTP ${response.status}.`, response.status, document?.error?.code ?? '')
    }
    return value as T
  }
}
