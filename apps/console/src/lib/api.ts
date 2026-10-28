import type {
  ArAgingReport, Balance, BillingCycleSnapshot, BillingDocument, Bundle, CampaignSnapshot,
  Checkout, CollectionReceipt, CollectionResolution, ExportResponse, Offer, PartnerStatement,
  StudioAssetResponse, StudioSnapshot, Subscription, Tenant, TrialBalance, Workspace,
  OfferJobSnapshot, ConversationQueueItem, ConversationDraft,
} from './types'

export class ApiError extends Error {
  status: number

  constructor(message: string, status = 0) {
    super(message)
    this.status = status
  }
}

export class VolumeOSApi {
  private coreUrl: string
  private orchestratorUrl: string
  private conversationsUrl: string
  private token: () => Promise<string | null>

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

  createTenant(name: string, slug: string) {
    return this.request<Tenant>(this.coreUrl, '/v1/tenants/current', { method: 'POST', body: { name, slug } })
  }

  createWorkspace(name: string) {
    return this.request<Workspace>(this.coreUrl, '/v1/workspaces', { method: 'POST', body: { name } })
  }

  createSubscription(offer: string, tier: string) {
    return this.request<Subscription>(this.coreUrl, '/v1/subscriptions', { method: 'POST', body: { offer, tier } })
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

  approveCampaign(campaignId: string, approvalId: string) {
    return this.request(this.orchestratorUrl, `/v1/campaigns/${encodeURIComponent(campaignId)}/approval`, {
      method: 'POST', body: { approval_id: approvalId },
    })
  }

  studioPacks() {
    return this.request<{ packs: StudioSnapshot[] }>(this.orchestratorUrl, '/v1/studio/packs')
  }

  startStudioPack(input: Record<string, string>) {
    return this.request<{ status: string; pack_id: string }>(this.orchestratorUrl, '/v1/studio/packs', { method: 'POST', body: input })
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
    return this.request<Checkout>(this.orchestratorUrl, `/v1/billing/topups/${encodeURIComponent(provider)}/${encodeURIComponent(reference)}`, {
      method: 'POST', body: input,
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
  } = {}, authenticated = true): Promise<T> {
    if (!base) throw new ApiError('This API is not configured for the current deployment.')
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (authenticated) {
      const accessToken = await this.token()
      if (!accessToken) throw new ApiError('Sign in with the tenant-scoped OIDC account to continue.', 401)
      headers.Authorization = `Bearer ${accessToken}`
    }
    if (options.body !== undefined) headers['Content-Type'] = 'application/json'
    const response = await fetch(base + path, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: 'no-store',
      credentials: 'omit',
      signal: AbortSignal.timeout(15000),
    })
    const text = await response.text()
    let value: unknown = null
    if (text) {
      try { value = JSON.parse(text) } catch { throw new ApiError('The service returned an invalid JSON response.', response.status) }
    }
    if (!response.ok) {
      const document = value as { error?: { message?: string }; detail?: string } | null
      const detail = document?.error?.message || document?.detail
      throw new ApiError(detail || `Request failed with HTTP ${response.status}.`, response.status)
    }
    return value as T
  }
}
