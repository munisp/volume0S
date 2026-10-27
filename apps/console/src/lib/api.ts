import type { Bundle, Offer, Subscription, Tenant, Workspace } from './types'

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
  private token: () => Promise<string | null>

  constructor(coreUrl: string, orchestratorUrl: string, token: () => Promise<string | null>) {
    this.coreUrl = coreUrl
    this.orchestratorUrl = orchestratorUrl
    this.token = token
  }

  get coreConfigured() { return Boolean(this.coreUrl) }
  get orchestratorConfigured() { return Boolean(this.orchestratorUrl) }

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

  startCampaign(input: { campaign_id: string; brief: string; amount_minor: number; currency: string; cycle_id: string }) {
    return this.request(this.orchestratorUrl, '/v1/campaigns', { method: 'POST', body: input })
  }

  approveCampaign(campaignId: string, approvalId: string) {
    return this.request(this.orchestratorUrl, `/v1/campaigns/${encodeURIComponent(campaignId)}/approval`, {
      method: 'POST', body: { approval_id: approvalId },
    })
  }

  startStudioPack(input: Record<string, string>) {
    return this.request(this.orchestratorUrl, '/v1/studio/packs', { method: 'POST', body: input })
  }

  reviewStudioPack(packId: string, decision: 'approve' | 'reject', reason: string) {
    return this.request(this.orchestratorUrl, `/v1/studio/packs/${encodeURIComponent(packId)}/review`, {
      method: 'POST', body: { decision, reason },
    })
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
      const detail = (value as { error?: { message?: string } } | null)?.error?.message
      throw new ApiError(detail || `Request failed with HTTP ${response.status}.`, response.status)
    }
    return value as T
  }
}
