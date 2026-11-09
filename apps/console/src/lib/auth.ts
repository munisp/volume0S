import { UserManager, WebStorageStateStore, type User } from 'oidc-client-ts'
import { oidcConfigured, type RuntimeConfig } from './config'

export class VolumeOSAuth {
  private manager: UserManager | null = null

  constructor(config: RuntimeConfig) {
    if (!oidcConfigured(config)) return
    // Always the site root (the address Keycloak knows); the page the person was on travels in the sign-in state, so
    // every screen can have its own URL without registering each one as a redirect.
    const redirectUri = `${window.location.origin}/`
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

  /** Called with the new user each time the token is renewed, so the app's copy never goes stale. */
  onUser(listener: (user: User) => void): () => void {
    if (!this.manager) return () => undefined
    this.manager.events.addUserLoaded(listener)
    return () => this.manager?.events.removeUserLoaded(listener)
  }

  async user() { return this.manager?.getUser() ?? null }

  /** A valid access token, quietly renewed with the refresh token when it has expired; null when the session is over. */
  async accessToken() {
    const current = await this.user()
    if (current && !current.expired) return current.access_token
    if (current?.refresh_token && this.manager) {
      try {
        const renewed = await (this.renewing ??= this.manager.signinSilent().finally(() => { this.renewing = null }))
        return renewed && !renewed.expired ? renewed.access_token : null
      } catch {
        return null
      }
    }
    return null
  }

  private renewing: Promise<User | null> | null = null

  async signIn(returnTo = '/') {
    if (!this.manager) throw new Error('Sign-in is not configured for this deployment.')
    await this.manager.signinRedirect({ state: { returnTo: safePath(returnTo) } })
  }

  async completeSignIn(): Promise<User | null> {
    if (!this.manager) return null
    const parameters = new URLSearchParams(window.location.search)
    if (!parameters.has('code') || !parameters.has('state')) return this.user()
    const user = await this.manager.signinRedirectCallback()
    window.history.replaceState({}, document.title, window.location.pathname)
    return user
  }

  /** Where to go after signing in: the page the person started from. */
  static returnTo(user: User | null): string {
    const state = user?.state as { returnTo?: unknown } | undefined
    return typeof state?.returnTo === 'string' ? safePath(state.returnTo) : '/'
  }

  /** Ends the identity-provider session too, so the next person on this computer must sign in again (BB-01). */
  async signOut() {
    if (!this.manager) return
    const current = await this.manager.getUser()
    await this.manager.removeUser()
    await this.manager.signoutRedirect({ id_token_hint: current?.id_token })
  }
}

/** Only same-site paths: never let a sign-in state send someone to another origin. */
function safePath(value: string): string {
  return /^\/(?!\/)[^\s]*$/.test(value) ? value : '/'
}
