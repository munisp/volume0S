import { useState, type FormEvent } from 'react'
import { Link, useLocation } from 'react-router'
import { useSession, errorText } from '../state/session-context'
import { ACTIONS, allowed, roleLabel, type Action } from '../lib/access'
import { Confirm, Empty, Field, Modal, Note, PageHead, Panel, RoleNote, Status, Submit } from '../components/ui'

const ACTION_LABEL: Record<Action, string> = {
  createTenant: 'Create the business record', transitionTenant: 'Change business status (incl. offboarding)', suspendOutbound: 'Suspend or restore outbound messages',
  updateSuppression: 'Suppress or restore a recipient', createWorkspace: 'Create delivery workspaces', subscribe: 'Subscribe the business to an offer',
  startCampaign: 'Start campaigns', approveCampaign: 'Approve campaigns', startStudioPack: 'Start Studio packs', reviewStudioPack: 'QA Studio images',
  startOfferJob: 'Start offer jobs', reviewOfferJob: 'Review offer-job artifacts and front-desk replies', startEngagement: 'Start engagements', reviewEngagement: 'Approve or reject engagement plans',
  topUp: 'Fund the prepaid balance', startBillingCycle: 'Start billing cycles', recoverBilling: 'Resume a paused billing cycle', financeReports: 'Finance exports and partner terms', read: 'See this business\'s work and records',
}

export function AccountPage() {
  const { roles, user } = useSession()
  return <div className="page">
    <PageHead eyebrow="Account" title="Your access" description="What your roles let you do in this business. The servers enforce the same rules; this page only explains them." />
    <Panel title={String(user?.profile.name ?? 'You')} meta={[...roles].map(roleLabel).join(' · ') || 'No roles'} flush>
      <div className="table-wrap"><table className="data stack-sm"><thead><tr><th>Action</th><th>Requires</th><th className="right">You</th></tr></thead><tbody>
        {(Object.keys(ACTIONS) as Action[]).map(action => <tr key={action}>
          <td>{ACTION_LABEL[action]}</td>
          <td data-label="Requires">{ACTIONS[action].map(roleLabel).join(' or ')}</td>
          <td data-label="You" className="right">{allowed(roles, action) ? <Status state="done" label="Allowed" /> : <Status state="stopped" label="Not allowed" />}</td>
        </tr>)}
      </tbody></table></div>
    </Panel>
    <p className="small muted">Roles are assigned in Keycloak by an administrator. Super operators can do everything.</p>
  </div>
}

export function AdminPage() {
  const { config, tenant, roles, api, setTenant, notify } = useSession()
  const [modal, setModal] = useState<'suspension' | 'suppression' | null>(null)
  const [confirm, setConfirm] = useState<{ suspended: boolean; reason: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const keycloakAdmin = config.oidc.authority.replace(/\/realms\/([^/]+)$/, '/admin/$1/console/')

  async function run(action: () => Promise<void>) {
    setBusy(true)
    try { await action() } catch (error) { notify('error', errorText(error)) } finally { setBusy(false) }
  }

  function suppression(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    void run(async () => {
      const result = await api.setSuppression(String(data.get('channel')), String(data.get('recipient')).trim(), String(data.get('reason')).trim(), data.get('suppressed') === 'true')
      setModal(null)
      if (data.get('suppressed') === 'true') notify('success', 'Recipient added to the opt-out list. They won\'t be messaged on that channel.')
      else if (result.status === 'not_suppressed') notify('info', 'That recipient wasn\'t on the opt-out list for that channel, so nothing changed.')
      else notify('success', 'Recipient removed from the opt-out list.')
    })
  }

  return <div className="page">
    <PageHead eyebrow="Administration" title="Administration" description="Safety controls for this business, who can do what, and where staff and audit records are managed." />

    <Panel title="Outbound safety" meta="Checked before every new provider call: email, SMS, WhatsApp and social.">
      <div className="stack">
        <div className="row">Outbound messages for <strong>{tenant?.name ?? 'this business'}</strong> are {tenant?.outbound_suspended ? <Status state="failed" label="Suspended" /> : <Status state="done" label="Allowed" />}</div>
        {allowed(roles, 'suspendOutbound') ? <div className="row">
          <button className={`button ${tenant?.outbound_suspended ? 'button-secondary' : 'button-danger'}`} disabled={!tenant} onClick={() => setConfirm({ suspended: !tenant?.outbound_suspended, reason: '' })}>
            {tenant?.outbound_suspended ? 'Restore outbound messages' : 'Suspend all outbound messages'}</button>
          <button className="button button-secondary" onClick={() => setModal('suppression')}>Suppress a recipient</button>
        </div> : <RoleNote action="suspendOutbound" />}
      </div>
    </Panel>

    <div className="grid-2">
      <Panel title="Staff and roles" meta="Managed in Keycloak (realm volumeos)">
        <div className="stack">
          <p className="body-copy">People, passwords and role assignments live in the identity service, not in VolumeOS. A person's business comes from their account's tenant claim.</p>
          {keycloakAdmin.includes('/admin/') && <a className="button button-secondary" href={keycloakAdmin} target="_blank" rel="noreferrer">Open the Keycloak admin console</a>}
          <Link to="/account">See what each role allows</Link>
        </div>
      </Panel>
      <Panel title="Audit history">
        <Empty title="Not available in the console yet">Kill-switch changes, suppressions and status changes are recorded with their reason on the server, but no API exposes that history to read. It isn't shown rather than shown incompletely.</Empty>
      </Panel>
    </div>

    <Panel title="Connections" meta="Supplied by the deployment">
      <dl className="props">
        <dt>Core API</dt><dd className="mono">{config.coreApiUrl || 'Not configured'}</dd>
        <dt>Workflow API</dt><dd className="mono">{config.orchestratorApiUrl || 'Not configured'}</dd>
        <dt>Front desk API</dt><dd className="mono">{config.conversationsApiUrl || 'Not configured on this deployment'}</dd>
        <dt>Sign-in</dt><dd className="mono">{config.oidc.authority || 'Not configured'}</dd>
      </dl>
    </Panel>

    {confirm && <Confirm title={confirm.suspended ? 'Suspend all outbound messages?' : 'Restore outbound messages?'} danger={confirm.suspended}
      confirmLabel={confirm.suspended ? 'Suspend now' : 'Restore'} busy={busy || !confirm.reason.trim()} onCancel={() => setConfirm(null)}
      onConfirm={() => void run(async () => {
        const updated = await api.setOutboundSuspension(confirm.suspended, confirm.reason.trim())
        setTenant(updated); setConfirm(null)
        notify('success', confirm.suspended ? 'Outbound messages suspended. New provider calls are refused.' : 'Outbound messages restored.')
      })}>
      <p>{confirm.suspended ? 'Every new email, SMS, WhatsApp or social send for this business will be refused until someone restores it. Work already sent is not recalled.' : 'New sends will be allowed again, subject to approvals and suppressions.'}</p>
      <label className="field" style={{ marginTop: 10 }}><span>Reason (kept in the audit trail)</span><textarea rows={3} value={confirm.reason} onChange={event => setConfirm({ ...confirm, reason: event.target.value })} placeholder={confirm.suspended ? 'Incident INC-42: stop all new sends' : 'Incident resolved; approved by …'} required /></label>
    </Confirm>}
    {modal === 'suppression' && <Modal title="Suppress or restore a recipient" description="The address is normalised and stored only as a keyed digest. Use real customer details only in production." onClose={() => setModal(null)}>
      <form onSubmit={suppression} className="form-stack">
        <div className="form-grid">
          <label className="field"><span>Channel</span><select name="channel"><option value="email">Email</option><option value="sms">SMS</option><option value="whatsapp">WhatsApp</option><option value="social">Social</option></select></label>
          <label className="field"><span>Action</span><select name="suppressed"><option value="true">Suppress</option><option value="false">Lift suppression</option></select></label>
        </div>
        <Field label="Recipient" name="recipient" placeholder="customer@example.com or +2348012345678" required hint="Email for email; an international number starting with + for SMS and WhatsApp; a handle for social." />
        <Field label="Reason" name="reason" placeholder="Customer opted out on 9 Nov" maxLength={500} required />
        <Submit busy={busy} label="Save" />
      </form>
    </Modal>}
  </div>
}

export function IntelligencePage() {
  return <div className="page">
    <PageHead eyebrow="Insight" title="Intelligence" description="Business analytics and geospatial market intelligence for Nigerian MSMEs." />
    <Panel title="Not built in the console yet">
      <div className="stack">
        <p className="body-copy">VolumeOS has the groundwork for this in source: data contracts for raw, cleaned and modelled layers, geospatial contracts, a census sync service and a report search service. None of them is running on this deployment, and no API serves analytics to the console, so there is nothing real to chart.</p>
        <p className="body-copy">No sample maps, market statistics or business counts are shown in their place. When the data services run, this page will show business distribution, density and regional performance from their results.</p>
        <Note>Service-level figures you can trust today are on Overview and Operations: work waiting, running, failed and finished, per type of work.</Note>
      </div>
    </Panel>
  </div>
}

export function NotFoundPage() {
  const location = useLocation()
  return <div className="page"><PageHead title="Page not found" /><Panel><Empty title={`Nothing lives at ${location.pathname}`}>The address may be mistyped, or the page moved. <Link to="/">Go to the overview</Link>.</Empty></Panel></div>
}

export function ForbiddenPage({ area }: { area: string }) {
  const { roles } = useSession()
  return <div className="page"><PageHead title="Not available to your role" /><Panel>
    <Empty title={`${area} isn't part of your work`}>Your roles ({[...roles].map(roleLabel).join(', ') || 'none'}) don't include it. Opening the address directly doesn't change that: the server refuses the same actions. <Link to="/account">See your access</Link>.</Empty>
  </Panel></div>
}

