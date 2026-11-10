import { createContext, useContext } from 'react'
import type { User } from 'oidc-client-ts'
import type { VolumeOSApi } from '../lib/api'
import type { RuntimeConfig } from '../lib/config'
import type { Persona, Roles } from '../lib/access'
import type { Bundle, Offer, Tenant } from '../lib/types'

export type ToastKind = 'success' | 'error' | 'info'
export type Toast = { kind: ToastKind; message: string; id: number }
export type Load = 'loading' | 'ok' | 'missing' | 'error'

export type Session = {
  ready: boolean
  config: RuntimeConfig
  signInAvailable: boolean
  /** True when a session existed but could no longer be renewed: show "sign in again", not a fresh landing. */
  sessionEnded: boolean
  user: User | null
  roles: Roles
  persona: Persona
  /** The agency's own operator (token business "platform" + super operator): sees every client and switches between them. */
  platform: boolean
  /** Platform operators: the business being worked on, or null (choose one on the Clients page). */
  actingTenant: string | null
  /** Platform operators: work on another business. Reloads the console so every page starts clean for it. */
  switchTenant: (tenantId: string | null, path?: string) => void
  api: VolumeOSApi
  tenant: Tenant | null
  tenantLoad: Load
  tenantError: string | null
  reloadTenant: () => Promise<void>
  setTenant: (tenant: Tenant) => void
  catalogue: { offers: Offer[]; bundles: Bundle[]; priceBook: string; load: Load; error: string | null }
  offerName: (id: string | null | undefined) => string
  toast: Toast | null
  notify: (kind: ToastKind, message: string) => void
  dismissToast: () => void
  signIn: (returnTo?: string) => Promise<void>
  signOut: () => Promise<void>
}

export const SessionContext = createContext<Session | null>(null)

export function useSession(): Session {
  const value = useContext(SessionContext)
  if (!value) throw new Error('useSession outside SessionProvider')
  return value
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'That didn\'t work. Try again.'
}

