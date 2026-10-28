export type RuntimeConfig = {
  coreApiUrl: string
  orchestratorApiUrl: string
  conversationsApiUrl: string
  oidc: {
    authority: string
    clientId: string
    scope: string
  }
}

export const defaultConfig: RuntimeConfig = {
  coreApiUrl: '',
  orchestratorApiUrl: '',
  conversationsApiUrl: '',
  oidc: { authority: '', clientId: '', scope: 'openid profile email' },
}

export async function loadRuntimeConfig(): Promise<RuntimeConfig> {
  try {
    const response = await fetch(`${import.meta.env.BASE_URL}runtime-config.json`, { cache: 'no-store' })
    if (!response.ok) return defaultConfig
    const value = await response.json() as Partial<RuntimeConfig>
    return {
      coreApiUrl: cleanUrl(value.coreApiUrl),
      orchestratorApiUrl: cleanUrl(value.orchestratorApiUrl),
      conversationsApiUrl: cleanUrl(value.conversationsApiUrl),
      oidc: {
        authority: cleanUrl(value.oidc?.authority),
        clientId: value.oidc?.clientId?.trim() ?? '',
        scope: value.oidc?.scope?.trim() || 'openid profile email',
      },
    }
  } catch {
    return defaultConfig
  }
}

function cleanUrl(value?: string) {
  return (value ?? '').trim().replace(/\/$/, '')
}

export function oidcConfigured(config: RuntimeConfig) {
  return Boolean(config.oidc.authority && config.oidc.clientId)
}
