import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import type { User } from 'oidc-client-ts'
import { VolumeOSApi, ApiError } from '../lib/api'
import { VolumeOSAuth } from '../lib/auth'
import { defaultConfig, loadRuntimeConfig, oidcConfigured, type RuntimeConfig } from '../lib/config'
import { isPlatform, personaOf, rolesFromToken, tenantFromToken } from '../lib/access'
import type { Tenant } from '../lib/types'
import { SessionContext, errorText, type Load, type Session, type Toast, type ToastKind } from './session-context'

const ACTING_KEY = 'volumeos.actingTenant'

/** Per browser and tab-independent; a convenience only (the servers decide what the header may reach). */
function savedActingTenant(): string | null {
  try { return window.localStorage.getItem(ACTING_KEY) } catch { return null }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  // navigate can change identity on every location change; the sign-in bootstrap must run exactly once.
  const navigateRef = useRef(navigate)
  useEffect(() => { navigateRef.current = navigate }, [navigate])
  const [config, setConfig] = useState<RuntimeConfig>(defaultConfig)
  const [auth, setAuth] = useState<VolumeOSAuth | null>(null)
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)
  const [tenant, setTenantState] = useState<Tenant | null>(null)
  const [tenantLoad, setTenantLoad] = useState<Load>('loading')
  const [tenantError, setTenantError] = useState<string | null>(null)
  const [catalogue, setCatalogue] = useState<Session['catalogue']>({ offers: [], bundles: [], priceBook: '', load: 'loading', error: null })
  const [toast, setToast] = useState<Toast | null>(null)

  const [sessionEnded, setSessionEnded] = useState(false)
  // A token that can't be renewed mid-use ends the session once, cleanly, instead of every feed failing on its own.
  // Only a session that existed can end: the check happens on the current user, inside the state update.
  const endSession = useCallback(() => {
    setUser(current => {
      if (current) queueMicrotask(() => { setTenantState(null); setTenantLoad('missing'); setSessionEnded(true) })
      return null
    })
  }, [])
  const [actingTenant] = useState<string | null>(savedActingTenant)
  const api = useMemo(() => {
    const client = new VolumeOSApi(
      config.coreApiUrl, config.orchestratorApiUrl, async () => {
        const token = await (auth?.accessToken() ?? Promise.resolve(null))
        if (!token) endSession()
        return token
      }, config.conversationsApiUrl,
    )
    client.actingTenant = actingTenant
    return client
  }, [config, auth, endSession, actingTenant])
  useEffect(() => auth?.onUser(renewed => setUser(renewed)), [auth])

  // Display only (the servers verify the token): read roles from the claims even when the token is about to be renewed.
  const roles = useMemo(() => rolesFromToken(user?.access_token), [user])
  const persona = user ? personaOf(roles) : 'none'
  const platform = isPlatform(roles, tenantFromToken(user?.access_token))

  const notify = useCallback((kind: ToastKind, message: string) => setToast({ kind, message, id: Date.now() }), [])

  const loadTenant = useCallback(async (client: VolumeOSApi) => {
    setTenantLoad('loading')
    try {
      setTenantState(await client.tenant())
      setTenantLoad('ok'); setTenantError(null)
    } catch (error) {
      setTenantState(null)
      if (error instanceof ApiError && error.status === 404) { setTenantLoad('missing'); setTenantError(null) } else { setTenantLoad('error'); setTenantError(errorText(error)) }
    }
  }, [])

  useEffect(() => {
    let active = true
    void (async () => {
      const runtime = await loadRuntimeConfig()
      if (!active) return
      setConfig(runtime)
      const identity = new VolumeOSAuth(runtime)
      setAuth(identity)
      const client = new VolumeOSApi(runtime.coreApiUrl, runtime.orchestratorApiUrl, () => identity.accessToken(), runtime.conversationsApiUrl)
      client.actingTenant = savedActingTenant()
      // The public price list loads for everyone, signed in or not.
      void client.offers().then(
        result => active && setCatalogue({ offers: result.offers, bundles: result.bundles, priceBook: result.price_book, load: 'ok', error: null }),
        error => active && setCatalogue({ offers: [], bundles: [], priceBook: '', load: 'error', error: errorText(error) }),
      )
      let current: User | null = null
      const returning = new URLSearchParams(window.location.search).has('code')
      try {
        current = await identity.completeSignIn()
      } catch (error) {
        if (active) notify('error', `Sign-in couldn't be completed. ${errorText(error)}`)
      }
      if (!active) return
      // An expired saved session gets one quiet renewal before it counts as ended.
      const live = current && (!current.expired || await identity.accessToken()) ? await identity.user() : null
      if (!active) return
      setUser(live)
      if (current && !live) setSessionEnded(true)
      // A platform operator who hasn't chosen a business has none to load: they start on the Clients page.
      const platformWithoutBusiness = live && isPlatform(rolesFromToken(live.access_token), tenantFromToken(live.access_token)) && !client.actingTenant
      if (live && !platformWithoutBusiness) await loadTenant(client)
      else if (live) client.actingTenant = null
      if (!live || platformWithoutBusiness) setTenantLoad('missing')
      if (returning && current) navigateRef.current(VolumeOSAuth.returnTo(current), { replace: true })
      setReady(true)
    })()
    return () => { active = false }
  }, [loadTenant, notify])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), toast.kind === 'error' ? 9000 : 5000)
    return () => window.clearTimeout(timer)
  }, [toast])

  const offerName = useCallback((id: string | null | undefined) => {
    if (!id) return ''
    return catalogue.offers.find(offer => offer.id === id)?.name ?? `Offer ${id}`
  }, [catalogue.offers])

  const value: Session = {
    ready, config, signInAvailable: oidcConfigured(config), sessionEnded, user, roles, persona, api,
    platform, actingTenant: platform ? actingTenant : null,
    switchTenant: (tenantId, path = tenantId ? '/client' : '/clients') => {
      try {
        if (tenantId) window.localStorage.setItem(ACTING_KEY, tenantId)
        else window.localStorage.removeItem(ACTING_KEY)
      } catch { /* private mode: the choice lasts until the reload only */ }
      window.location.assign(path)
    },
    tenant, tenantLoad, tenantError,
    reloadTenant: () => loadTenant(api),
    setTenant: next => { setTenantState(next); setTenantLoad('ok') },
    catalogue, offerName, toast, notify, dismissToast: () => setToast(null),
    signIn: async (returnTo = window.location.pathname) => {
      try { await auth?.signIn(returnTo) } catch (error) { notify('error', errorText(error)) }
    },
    signOut: async () => {
      // Leaves for the identity provider's sign-out page and comes back to the landing page, signed out everywhere.
      try { await auth?.signOut() } catch (error) {
        setUser(null); setTenantState(null); setTenantLoad('missing')
        navigate('/', { replace: true })
        notify('error', `You're signed out of this tab, but the sign-in service couldn't be reached to end the session everywhere. ${errorText(error)}`)
      }
    },
  }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}
