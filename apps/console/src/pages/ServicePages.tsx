import { useNavigate, useParams } from 'react-router'
import { useSession } from '../state/session-context'
import { useOperations } from '../state/operations-context'
import { allowed, needs, type Action } from '../lib/access'
import { EngagementOperationsSection } from '../sections/EngagementOperationsSection'
import { OfferOperationsSection } from '../sections/OfferOperationsSection'
import { BillingSection } from '../sections/BillingSection'
import { Empty, Note, PageHead, Panel, RoleNote } from '../components/ui'

function useNotify() {
  const { notify } = useSession()
  const { refresh } = useOperations()
  // Any change made on a service page also refreshes the shared queues (Overview, Approvals, navigation counts).
  return (kind: 'success' | 'error' | 'info', message: string) => { notify(kind, message); if (kind === 'success') void refresh() }
}

export function EngagementsPage({ clientView = false }: { clientView?: boolean }) {
  const { api, user, tenant, roles } = useSession()
  const { id } = useParams()
  const navigate = useNavigate()
  const notify = useNotify()
  return <EngagementOperationsSection key={tenant?.id ?? 'none'} api={api} connected={Boolean(user && tenant && api.orchestratorConfigured)} notify={notify}
    initialId={id} onSelect={engagementId => navigate(`/engagements/${encodeURIComponent(engagementId)}`, { replace: true })}
    canStart={!clientView && allowed(roles, 'startEngagement')} canReview={!clientView && allowed(roles, 'reviewEngagement')}
    reviewerLabel={reviewer => reviewer === user?.profile.sub ? 'you' : `another reviewer (account ${reviewer.slice(0, 8)}…)`}
    roleNote={action => clientView ? null : <RoleNote action={action === 'start' ? 'startEngagement' : 'reviewEngagement'} />} />
}

export function OfferJobsPage() {
  const { api, user, tenant, roles } = useSession()
  const { id } = useParams()
  const navigate = useNavigate()
  const notify = useNotify()
  return <OfferOperationsSection key={tenant?.id ?? 'none'} show="jobs" api={api} connected={Boolean(user && tenant && api.orchestratorConfigured)} conversationsConnected={false} notify={notify}
    initialJobId={id} onSelectJob={jobId => navigate(`/services/offer-jobs/${encodeURIComponent(jobId)}`, { replace: true })}
    canStart={allowed(roles, 'startOfferJob')} canReview={allowed(roles, 'reviewOfferJob')}
    roleNote={action => <RoleNote action={action === 'start' ? 'startOfferJob' : 'reviewOfferJob'} />} />
}

export function FrontDeskPage() {
  const { api, user, tenant, roles } = useSession()
  const notify = useNotify()
  if (!api.conversationsConfigured) return <div className="page">
    <PageHead eyebrow="Services · 04 AI Front Desk" title="Front desk" description="WhatsApp enquiries answered with AI-drafted replies that a person approves before anything is sent." />
    <Panel><Empty title="The front desk isn't running on this deployment">Its conversation service isn't connected, so there are no conversations to show. Nothing is substituted: when the service is connected, real WhatsApp conversations and their drafted replies appear here for review.</Empty></Panel>
  </div>
  return <OfferOperationsSection key={tenant?.id ?? 'none'} show="front-desk" api={api} connected={Boolean(user && tenant)} conversationsConnected notify={notify}
    canReview={allowed(roles, 'reviewOfferJob')} roleNote={() => <RoleNote action="reviewOfferJob" />} />
}

const BILLING_ACTIONS: { action: Action; label: string }[] = [
  { action: 'topUp', label: 'fund the prepaid balance' }, { action: 'recoverBilling', label: 'resume a paused billing cycle' },
  { action: 'startBillingCycle', label: 'start a billing cycle' }, { action: 'financeReports', label: 'export reports and record partner terms' },
]

export function FinancePage() {
  const { api, user, tenant, roles } = useSession()
  const blocked = BILLING_ACTIONS.filter(item => !allowed(roles, item.action))
  return <>
    {blocked.length > 0 && <div style={{ marginBottom: 12 }}><Note>With your role you can view finance but not {blocked.map(item => item.label).join(', ')}. ({blocked.map(item => needs(item.action)).join('; ')}.)</Note></div>}
    <BillingSection key={tenant?.id ?? 'none'} api={api} connected={Boolean(user && tenant && api.orchestratorConfigured)} platform={tenant?.id === 'platform'}
      allow={action => allowed(roles, action)} />
  </>
}
