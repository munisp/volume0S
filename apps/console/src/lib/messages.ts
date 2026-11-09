/**
 * Server refusals in words a person can act on.
 *
 * The APIs answer with terse, generic texts ("required role: delivery", "resource exists or version is stale",
 * "invalid state change") and the gateway answers a stopped service with an HTML 503 page. The console used to show
 * those as they came ("The service returned an invalid JSON response."). The server's text is kept where it is
 * already specific.
 */
import { roleLabel } from './access.ts'

type Failure = { message?: string; status?: number; code?: string }

const GENERIC = new Set([
  'resource exists or version is stale', 'invalid state change', 'Request fields are invalid.',
  'resource not found in tenant scope', 'Workflow was not found in this tenant.',
])

export function plainError(error: unknown): string {
  if (!error || typeof error !== 'object') return 'That didn\'t work. Try again.'
  const { message = '', status = 0, code = '' } = error as Failure
  const role = /^required role: ([a-z-]+)\.?$/i.exec(message) ?? /^Required role: ([a-z-]+)\.?$/.exec(message)
  if (status === 403 || code === 'forbidden') {
    return role ? `Your role can't do this. It needs the ${roleLabel(role[1])} role.` : 'Your role can\'t do this.'
  }
  if (status === 401) return 'Your session has ended. Sign in again to continue.'
  if (code === 'stale_plan') return 'The plan changed after you opened it. The current version has been loaded; check it before deciding.'
  if (code === 'money_service_unavailable') return 'Billing isn\'t running on this deployment, so this can\'t be done yet.'
  if (code === 'orchestrator_unavailable') return 'The workflow service didn\'t answer in time. Try again shortly.'
  if (code === 'dependency_timeout' || status === 504) return 'A service took too long to answer. Try again shortly.'
  if (status === 409 || code === 'conflict') {
    return GENERIC.has(message) || !message
      ? 'That already exists, or it changed since you opened it. Refresh and try again.'
      : message
  }
  if (status === 404) return GENERIC.has(message) || !message ? 'That item isn\'t in this business.' : message
  if (status === 400 || status === 422 || code === 'invalid_request' || code === 'invalid_state' || code === 'invalid_json') {
    return GENERIC.has(message) || !message
      ? 'Some details weren\'t accepted. Check the lengths and formats, then try again.'
      : message
  }
  if (status === 502 || status === 503) return 'This service isn\'t running on this deployment right now.'
  return message || 'That didn\'t work. Try again.'
}
