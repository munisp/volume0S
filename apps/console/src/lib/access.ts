/**
 * Who can do what, read from the signed-in person's token.
 *
 * This mirrors the servers' own rule (svc-orchestrator auth.Claims.can, svc-tenancy Claims.Can) so the console only
 * offers what the API will accept. It is a convenience, never the gate: every API still checks the role itself.
 */

/** Roles that may read tenant data even without the exact role a route names (server: READER_ROLES). */
export const READER_ROLES = new Set(['delivery', 'sales', 'analyst', 'client-owner', 'partner', 'auditor', 'finance'])

/** Agency staff roles. A person with none of these and `client-owner` gets the client workspace. */
export const STAFF_ROLES = new Set(['super-operator', 'delivery', 'sales', 'analyst', 'finance', 'auditor', 'partner'])

const IGNORED = /^(default-roles-|offline_access$|uma_authorization$)/

export type Roles = ReadonlySet<string>

/** The business in the token (display only; the servers verify it). */
export function tenantFromToken(token: string | null | undefined): string | null {
  if (!token) return null
  try {
    const part = token.split('.')[1] ?? ''
    const json = JSON.parse(globalThis.atob(part.replaceAll('-', '+').replaceAll('_', '/').padEnd(Math.ceil(part.length / 4) * 4, '='))) as { tenant_id?: unknown }
    return typeof json.tenant_id === 'string' ? json.tenant_id : null
  } catch {
    return null
  }
}

/** The agency's own operators: token business "platform" and the super operator role (servers: scopeClaims). */
export function isPlatform(roles: Roles, tenant: string | null): boolean {
  return tenant === 'platform' && roles.has('super-operator')
}
export type Persona = 'operator' | 'client' | 'none'

/** Decodes the roles from a JWT access token (the `roles` claim plus Keycloak realm roles). Never verifies: display only. */
export function rolesFromToken(token: string | null | undefined): Roles {
  if (!token) return new Set()
  try {
    const part = token.split('.')[1] ?? ''
    const json = JSON.parse(globalThis.atob(part.replaceAll('-', '+').replaceAll('_', '/').padEnd(Math.ceil(part.length / 4) * 4, '='))) as {
      roles?: unknown; realm_access?: { roles?: unknown }
    }
    const raw = [...(Array.isArray(json.roles) ? json.roles : []), ...(Array.isArray(json.realm_access?.roles) ? json.realm_access.roles : [])]
    return new Set(raw.filter((role): role is string => typeof role === 'string' && role !== '' && !IGNORED.test(role)))
  } catch {
    return new Set()
  }
}

/** Same rule as the servers: super-operator can do anything; `reader` is satisfied by any reader-class role. */
export function can(roles: Roles, role: string): boolean {
  if (roles.has('super-operator') || roles.has(role)) return true
  // A shop owner runs their own business's services (servers: the same rule in Claims.can).
  if (role === 'delivery' && roles.has('client-owner')) return true
  if (role === 'reader') return [...roles].some(item => READER_ROLES.has(item))
  return false
}

export function personaOf(roles: Roles): Persona {
  if ([...roles].some(role => STAFF_ROLES.has(role)) || roles.has('reader')) return 'operator'
  if (roles.has('client-owner')) return 'client'
  return 'none'
}

/** Every action the console offers, with the role(s) the API requires for it (any one suffices). */
export const ACTIONS = {
  createTenant: ['super-operator'],
  managePeople: ['super-operator'],
  transitionTenant: ['super-operator'],
  suspendOutbound: ['super-operator'],
  updateSuppression: ['super-operator'],
  createWorkspace: ['delivery'],
  subscribe: ['sales'],
  startCampaign: ['delivery'],
  approveCampaign: ['delivery'],
  manageContacts: ['delivery'],
  startStudioPack: ['delivery'],
  reviewStudioPack: ['delivery'],
  manageCreators: ['delivery'],
  startOfferJob: ['delivery'],
  reviewOfferJob: ['delivery', 'analyst'],
  startEngagement: ['delivery'],
  reviewEngagement: ['delivery', 'analyst'],
  topUp: ['client-owner', 'delivery'],
  startBillingCycle: ['sales'],
  recoverBilling: ['delivery'],
  financeReports: ['finance'],
  read: ['reader'],
} as const satisfies Record<string, readonly string[]>

export type Action = keyof typeof ACTIONS

export function allowed(roles: Roles, action: Action): boolean {
  return ACTIONS[action].some(role => can(roles, role))
}

/** "Needs the delivery or analyst role" — for a disabled action's explanation. */
export function needs(action: Action): string {
  const list = ACTIONS[action].map(roleLabel)
  return `Needs the ${list.length > 1 ? `${list.slice(0, -1).join(', ')} or ${list.at(-1)}` : list[0]} role`
}

const LABELS: Record<string, string> = {
  'super-operator': 'Super operator', delivery: 'Delivery', sales: 'Sales', analyst: 'Analyst', finance: 'Finance',
  auditor: 'Auditor', partner: 'Partner', 'client-owner': 'Client owner', reader: 'Reader',
}

export function roleLabel(role: string): string {
  return LABELS[role] ?? role.replaceAll('-', ' ').replace(/^./, c => c.toUpperCase())
}

/** Navigation areas and the roles that see them (any one; super-operator sees all). */
export const AREAS = {
  overview: ['reader'],
  approvals: ['delivery', 'analyst'],
  engagements: ['reader'],
  operations: ['reader'],
  services: ['reader'],
  client: ['reader'],
  catalogue: ['reader'],
  // Reading billing is open to every reader-class role on the server; actions stay role-gated.
  finance: ['reader'],
  intelligence: ['analyst', 'sales'],
  administration: ['super-operator'],
} as const satisfies Record<string, readonly string[]>

export type Area = keyof typeof AREAS

export function sees(roles: Roles, area: Area): boolean {
  return AREAS[area].some(role => can(roles, role))
}
