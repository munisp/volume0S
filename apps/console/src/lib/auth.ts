import { UserManager, WebStorageStateStore, type User } from 'oidc-client-ts'
import { oidcConfigured, type RuntimeConfig } from './config'

export class VolumeOSAuth {
  private manager: UserManager | null = null

  constructor(config: RuntimeConfig) {
    if (!oidcConfigured(config)) return
    const redirectUri = `${window.location.origin}${window.location.pathname}`
    this.manager = new UserManager({
      authority: config.oidc.authority,
      client_id: config.oidc.clientId,
      redirect_uri: redirectUri,
      post_logout_redirect_uri: redirectUri,
      response_type: 'code',
      scope: config.oidc.scope,
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
      automaticSilentRenew: false,
      monitorSession: false,
    })
  }

  get configured() { return Boolean(this.manager) }

  async user() { return this.manager?.getUser() ?? null }

  async accessToken() {
    const current = await this.user()
    return current && !current.expired ? current.access_token : null
  }

  async signIn() {
    if (!this.manager) throw new Error('OIDC is not configured for this deployment.')
    await this.manager.signinRedirect()
  }

  async completeSignIn(): Promise<User | null> {
    if (!this.manager) return null
    const parameters = new URLSearchParams(window.location.search)
    if (!parameters.has('code') || !parameters.has('state')) return this.user()
    const user = await this.manager.signinRedirectCallback()
    window.history.replaceState({}, document.title, window.location.pathname)
    return user
  }

  async signOut() { await this.manager?.removeUser() }
}
