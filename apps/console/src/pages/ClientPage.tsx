import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { Plus } from 'lucide-react'
import { useSession, errorText } from '../state/session-context'
import { useOperations } from '../state/operations-context'
import { allowed, needs } from '../lib/access'
import { byRecent, countBy, KIND_LABEL } from '../lib/workflows'
import type { Tenant } from '../lib/types'
import { Confirm, Empty, Field, Modal, Note, OfferMark, PageHead, Panel, RoleNote, Segmented, Status, Submit, When, WorkStatus } from '../components/ui'
import { dateTime } from '../lib/format'

const LIFECYCLE: Record<Tenant['state'], { label: string; state: 'running' | 'done' | 'stopped' | 'needs-review' }> = {
  prospect: { label: 'Prospect', state: 'needs-review' }, onboarding: { label: 'Onboarding', state: 'running' },
  active: { label: 'Active', state: 'done' }, paused: { label: 'Paused', state: 'stopped' }, offboarded: { label: 'Offboarded', state: 'stopped' },
}
/** The moves svc-tenancy accepts (domain.go CanTransition). Offboarding is permanent. */
const NEXT: Record<Tenant['state'], Tenant['state'][]> = {
  prospect: ['onboarding', 'offboarded'], onboarding: ['active', 'offboarded'], active: ['paused', 'offboarded'], paused: ['active', 'offboarded'], offboarded: [],
}
type Tab = 'profile' | 'services' | 'workspaces' | 'activity'

export function ClientPage() {
  const { tenant, tenantLoad, tenantError, roles, api, setTenant, reloadTenant, notify, offerName } = useSession()
  const { items } = useOperations()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') ?? 'profile') as Tab
  const [modal, setModal] = useState<'tenant' | 'workspace' | null>(null)
  const [transition, setTransition] = useState<{ to: Tenant['state']; reason: string } | null>(null)
  const [busy, setBusy] = useState(false)

  async function run(action: () => Promise<void>) {
    setBusy(true)
    try { await action() } catch (error) { notify('error', errorText(error)) } finally { setBusy(false) }
  }

  function createTenant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    void run(async () => {
      const created = await api.createTenant(String(data.get('name')).trim(), String(data.get('slug')).trim())
      setTenant(created); setModal(null)
      notify('success', `${created.name} has been created.`)
    })
  }

  function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    void run(async () => {
      await api.createWorkspace(String(data.get('name')).trim())
      await reloadTenant(); setModal(null)
      notify('success', 'Workspace created.')
    })
  }

  function confirmTransition() {
    if (!transition || !tenant) return
    void run(async () => {
      const response = await api.transitionTenant(transition.to, tenant.version, transition.reason.trim())
      setTenant(response); setTransition(null)
      notify('success', `${response.name} is now ${LIFECYCLE[response.state].label.toLowerCase()}.`)
    })
  }

  if (tenantLoad === 'loading') return <div className="page"><PageHead title="Client" /><Empty title="Loading this business" /></div>
  if (tenantLoad === 'error') return <div className="page"><PageHead title="Client" /><Note tone="failed">This business couldn't be loaded: {tenantError}</Note></div>

  if (!tenant) return <div className="page">
    <PageHead eyebrow="Clients" title="Set up this business" description="Your sign-in is scoped to a business that doesn't have a record yet. Creating it is the first onboarding step." />
    <Panel title="Onboarding">
      <ol className="stack" style={{ margin: 0, paddingLeft: 18 }}>
        <li><strong>Business record</strong> — name and a URL-safe slug. The business id comes from your sign-in, never from a form.</li>
        <li><strong>Workspace</strong> — where delivery work is organised.</li>
        <li><strong>Services</strong> — subscribe to a priced offer from the catalogue.</li>
        <li><strong>Status</strong> — move from prospect to onboarding to active.</li>
      </ol>
      <div style={{ marginTop: 16 }}>{allowed(roles, 'createTenant')
        ? <button className="button button-primary" onClick={() => setModal('tenant')}><Plus size={15} /> Create business record</button>
        : <RoleNote action="createTenant" />}</div>
    </Panel>
    {modal === 'tenant' && <Modal title="Create business record" description="The business id comes from your sign-in; it is never taken from this form." onClose={() => setModal(null)}>
      <form onSubmit={createTenant} className="form-stack">
        <Field label="Business name" name="name" placeholder="e.g. Aster Foods Ltd" minLength={2} maxLength={120} required />
        <Field label="Short name (URL-safe)" name="slug" placeholder="aster-foods" pattern="[a-z0-9][a-z0-9\-]{1,62}" title="Lowercase letters, digits and hyphens; 2 to 63 characters" required hint="Lowercase letters, digits and hyphens." />
        <Submit busy={busy} label="Create business" />
      </form>
    </Modal>}
  </div>

  const life = LIFECYCLE[tenant.state] ?? { label: tenant.state, state: 'running' as const }
  const subscriptions = tenant.subscriptions ?? []
  const workspaces = tenant.workspaces ?? []
  const counts = countBy(items)
  const waiting = items.filter(item => item.state === 'needs-review').length
  const failed = items.filter(item => item.state === 'failed').length
  const setTab = (value: Tab) => setParams(value === 'profile' ? {} : { tab: value }, { replace: true })

  return <div className="page">
    <PageHead eyebrow="Client" title={tenant.name} description={<>{tenant.slug} · <Status state={life.state} label={life.label} />{tenant.outbound_suspended && <> · <Status state="failed" label="Outbound suspended" /></>}</>}
      actions={allowed(roles, 'transitionTenant') && NEXT[tenant.state].length > 0
        ? <>{NEXT[tenant.state].map(to => <button key={to} className={`button ${to === 'offboarded' ? 'button-danger-quiet' : 'button-secondary'}`} onClick={() => setTransition({ to, reason: '' })}>
            {to === 'offboarded' ? 'Offboard' : `Move to ${LIFECYCLE[to].label.toLowerCase()}`}</button>)}</>
        : undefined} />

    <div className="figures">
      <div className="figure"><span>Services</span><strong>{subscriptions.length}</strong><small>subscribed</small></div>
      <div className="figure"><span>Workspaces</span><strong>{workspaces.length}</strong></div>
      <div className={`figure ${waiting ? 'attention' : ''}`}><span>Waiting on a person</span><strong>{waiting}</strong></div>
      <div className={`figure ${failed ? 'failed' : ''}`}><span>Failed work</span><strong>{failed}</strong></div>
      <div className="figure"><span>Client since</span><strong style={{ fontSize: 15, paddingTop: 6 }}>{dateTime(tenant.created_at).split(',')[0]}</strong></div>
    </div>

    <Segmented label="Section" value={tab} onChange={setTab} options={[
      { id: 'profile', label: 'Profile' }, { id: 'services', label: 'Services', count: subscriptions.length },
      { id: 'workspaces', label: 'Workspaces', count: workspaces.length }, { id: 'activity', label: 'Activity', count: items.length }]} />

    {tab === 'profile' && <div className="grid-2">
      <Panel title="Business">
        <dl className="props">
          <dt>Name</dt><dd>{tenant.name}</dd>
          <dt>Short name</dt><dd className="mono">{tenant.slug}</dd>
          <dt>Business id</dt><dd className="mono">{tenant.id}</dd>
          <dt>Status</dt><dd><Status state={life.state} label={life.label} /></dd>
          <dt>Outbound messages</dt><dd>{tenant.outbound_suspended ? 'Suspended: no new provider calls' : 'Allowed'}</dd>
          <dt>Record version</dt><dd className="num">{tenant.version}</dd>
          <dt>Created</dt><dd>{dateTime(tenant.created_at)}</dd>
          <dt>Last changed</dt><dd>{dateTime(tenant.updated_at)}</dd>
        </dl>
      </Panel>
      <Panel title="Not recorded yet">
        <p className="body-copy">The business record holds a name, short name and status only. Category, contact details, location and verification documents have no fields in the API yet, so they aren't shown or invented here.</p>
      </Panel>
    </div>}

    {tab === 'services' && <Panel title="Subscribed services" actions={<Link className="button button-secondary small" to="/catalogue">Open catalogue</Link>} flush>
      {subscriptions.length === 0 ? <Empty title="No services yet">Subscribe this business to a priced offer from the catalogue.</Empty>
        : <div className="table-wrap"><table className="data stack-sm"><thead><tr><th>Offer</th><th>Tier</th><th>Since</th><th className="right">Subscription id</th></tr></thead><tbody>
          {subscriptions.map(sub => <tr key={sub.id}><td><OfferMark id={sub.offer} name={offerName(sub.offer)} /></td><td data-label="Tier" style={{ textTransform: 'capitalize' }}>{sub.tier}</td><td data-label="Since">{dateTime(sub.created_at)}</td><td data-label="Id" className="right mono">{sub.id}</td></tr>)}
        </tbody></table></div>}
    </Panel>}

    {tab === 'workspaces' && <Panel title="Delivery workspaces" actions={allowed(roles, 'createWorkspace') ? <button className="button button-secondary small" onClick={() => setModal('workspace')}><Plus size={14} /> New workspace</button> : undefined} flush>
      {!allowed(roles, 'createWorkspace') && <div className="panel-body"><RoleNote action="createWorkspace" /></div>}
      {workspaces.length === 0 ? <Empty title="No workspaces yet" />
        : <div className="table-wrap"><table className="data stack-sm"><thead><tr><th>Name</th><th>Created</th><th className="right">Id</th></tr></thead><tbody>
          {workspaces.map(ws => <tr key={ws.id}><td className="title">{ws.name}</td><td data-label="Created">{dateTime(ws.created_at)}</td><td data-label="Id" className="right mono">{ws.id}</td></tr>)}
        </tbody></table></div>}
    </Panel>}

    {tab === 'activity' && <Panel title="Work for this business" meta={`${counts.engagement['needs-review'] + counts['offer-job']['needs-review'] + counts.studio['needs-review'] + counts.campaign['needs-review']} waiting · ${items.length} in total`} flush>
      {items.length === 0 ? <Empty title="No work yet" /> : <div className="table-wrap"><table className="data stack-sm"><thead><tr><th>Work</th><th>Type</th><th>Status</th><th className="right">Updated</th></tr></thead><tbody>
        {byRecent(items).slice(0, 25).map(item => <tr key={`${item.kind}:${item.id}`} className="link" onClick={() => navigate(item.href)}>
          <td><Link to={item.href} className="title">{item.title}</Link></td><td data-label="Type">{KIND_LABEL[item.kind]}</td><td data-label="Status"><WorkStatus item={item} /></td><td className="right"><When value={item.updatedAt} /></td>
        </tr>)}
      </tbody></table></div>}
    </Panel>}

    {modal === 'workspace' && <Modal title="New delivery workspace" description={`Inside ${tenant.name}.`} onClose={() => setModal(null)}>
      <form onSubmit={createWorkspace} className="form-stack">
        <Field label="Workspace name" name="name" placeholder="Lagos delivery team" minLength={2} maxLength={120} required hint="2 to 120 characters." />
        <Submit busy={busy} label="Create workspace" />
      </form>
    </Modal>}

    {transition && <Confirm title={transition.to === 'offboarded' ? `Offboard ${tenant.name}?` : `Move ${tenant.name} to ${LIFECYCLE[transition.to].label.toLowerCase()}?`}
      confirmLabel={transition.to === 'offboarded' ? 'Offboard permanently' : 'Change status'} danger={transition.to === 'offboarded'} busy={busy || !transition.reason.trim()}
      onCancel={() => setTransition(null)} onConfirm={confirmTransition}>
      {transition.to === 'offboarded' && <Note tone="failed">Offboarding can't be undone: the business can never be made active again.</Note>}
      <label className="field" style={{ marginTop: 10 }}><span>Reason (kept in the audit trail)</span>
        <textarea rows={3} value={transition.reason} onChange={event => setTransition({ ...transition, reason: event.target.value })} required /></label>
      <p className="small muted" style={{ marginTop: 6 }}>{needs('transitionTenant')}. If someone else changed this business since you opened it, the change is refused and you'll be asked to refresh.</p>
    </Confirm>}
  </div>
}
