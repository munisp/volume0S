import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { ArrowRight, Copy, Plus, RefreshCw } from 'lucide-react'
import { useSession, errorText } from '../state/session-context'
import type { Offer, Tenant, TenantSummary } from '../lib/types'
import { Empty, Field, Modal, Note, OfferMark, PageHead, Panel, SearchBox, Status, Submit } from '../components/ui'
import { dateTime } from '../lib/format'

const LIFECYCLE: Record<Tenant['state'], { label: string; state: 'running' | 'done' | 'stopped' | 'needs-review' }> = {
  prospect: { label: 'Prospect', state: 'needs-review' }, onboarding: { label: 'Onboarding', state: 'running' },
  active: { label: 'Active', state: 'done' }, paused: { label: 'Paused', state: 'stopped' }, offboarded: { label: 'Offboarded', state: 'stopped' },
}

/** Same rule as the catalogue's "Available to subscribe": offers 01–08 that have a price. */
const subscribable = (offer: Offer) => Number(offer.id) >= 1 && Number(offer.id) <= 8 && offer.tiers.length > 0

/** "Aster Foods Ltd" → "aster-foods-ltd": the business id and short name a new client gets unless changed. */
function slugify(name: string): string {
  return name.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 63)
}

/** The agency's client list and the way in to onboarding: platform operators only. */
export function ClientsPage() {
  const { api, actingTenant, switchTenant, notify } = useSession()
  const [rows, setRows] = useState<TenantSummary[] | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [onboarding, setOnboarding] = useState(false)

  const [version, setVersion] = useState(0)
  const load = () => setVersion(current => current + 1)
  useEffect(() => {
    let active = true
    api.tenants().then(
      result => { if (active) { setRows(result.tenants); setFailure(null) } },
      error => { if (active) setFailure(errorText(error)) },
    )
    return () => { active = false }
  }, [api, version])

  const shown = useMemo(() => (rows ?? []).filter(row => !query.trim()
    || `${row.name} ${row.id}`.toLowerCase().includes(query.trim().toLowerCase())), [rows, query])

  return <div className="page">
    <PageHead eyebrow="Agency" title="Clients" description="Every business VolumeOS works for. Open one to work on it; everything you see then is that business only."
      actions={<>
        <button className="button button-secondary" onClick={load}><RefreshCw size={15} /> Refresh</button>
        <button className="button button-primary" onClick={() => setOnboarding(true)}><Plus size={15} /> Onboard client</button>
      </>} />
    {failure && <Note tone="failed">The client list couldn't be loaded: {failure}</Note>}
    <Panel title="All clients" meta={rows ? `${rows.length} in total` : undefined} actions={<SearchBox value={query} onChange={setQuery} placeholder="Search by name or id" />} flush>
      {rows === null && !failure ? <Empty title="Loading clients" />
        : shown.length === 0 ? <Empty title={rows?.length ? 'No client matches' : 'No clients yet'}>{rows?.length ? 'Try another name.' : 'Onboard the first one.'}</Empty>
        : <div className="table-wrap"><table className="data stack-sm"><thead><tr><th>Client</th><th>Status</th><th>Services</th><th className="right">Workspaces</th><th>Since</th><th /></tr></thead><tbody>
          {shown.map(row => {
            const life = LIFECYCLE[row.state] ?? { label: row.state, state: 'running' as const }
            return <tr key={row.id} className="link" onClick={() => switchTenant(row.id)}>
              <td><button className="title text-button" onClick={event => { event.stopPropagation(); switchTenant(row.id) }}>{row.name}</button>
                <div className="small muted mono">{row.id}{row.id === actingTenant ? ' · open now' : ''}</div></td>
              <td data-label="Status"><Status state={life.state} label={life.label} />{row.outbound_suspended && <div className="small" style={{ marginTop: 4 }}><Status state="failed" label="Outbound suspended" /></div>}</td>
              <td data-label="Services">{row.offers.length ? <span className="row" style={{ gap: 4, flexWrap: 'wrap' }}>{row.offers.map(offer => <OfferMark key={offer} id={offer} compact />)}</span> : <span className="muted">None</span>}</td>
              <td data-label="Workspaces" className="right num">{row.workspaces}</td>
              <td data-label="Since">{dateTime(row.created_at).split(',')[0]}</td>
              <td className="right"><ArrowRight size={15} aria-hidden="true" /></td>
            </tr>
          })}
        </tbody></table></div>}
    </Panel>
    {onboarding && <OnboardClient existing={new Set((rows ?? []).map(row => row.id))} onClose={() => setOnboarding(false)}
      onDone={() => { load(); notify('success', 'Client onboarded.') }} />}
  </div>
}

type Step = 'business' | 'owner' | 'services' | 'done'
type Result = { tenantId: string; name: string; email: string; password: string; subscribed: string[]; warnings: string[] }

/** Business record → owner sign-in → services, in one go. Each call is made for the new business explicitly. */
function OnboardClient({ existing, onClose, onDone }: { existing: Set<string>; onClose: () => void; onDone: () => void }) {
  const { api, catalogue, switchTenant } = useSession()
  const [step, setStep] = useState<Step>('business')
  const [business, setBusiness] = useState({ name: '', id: '', idEdited: false })
  const [owner, setOwner] = useState({ first_name: '', last_name: '', email: '' })
  const [services, setServices] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [result, setResult] = useState<Result | null>(null)
  const sellable = catalogue.offers.filter(subscribable)
  const taken = existing.has(business.id)

  function nextFromBusiness(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!taken) setStep('owner')
  }

  async function finish() {
    setBusy(true); setFailure(null)
    const id = business.id
    let created: Tenant | null = null
    try {
      created = await api.createTenant(business.name.trim(), id, id)
    } catch (error) {
      setFailure(`The business record wasn't created, so nothing else was done. ${errorText(error)}`)
      setBusy(false); return
    }
    const warnings: string[] = []
    try {
      created = await api.transitionTenant('onboarding', created.version, 'Onboarded from the console', id)
    } catch (error) { warnings.push(`It stayed a prospect: ${errorText(error)}`) }
    let password = ''
    try {
      password = (await api.addPerson({ ...owner, email: owner.email.trim(), role: 'client-owner' }, id)).temporary_password
    } catch (error) { warnings.push(`The owner's sign-in wasn't created (add it under Client › People): ${errorText(error)}`) }
    const subscribed: string[] = []
    for (const [offer, tier] of Object.entries(services)) {
      try { await api.createSubscription(offer, tier, id); subscribed.push(offer) } catch (error) { warnings.push(`Offer ${offer} wasn't subscribed: ${errorText(error)}`) }
    }
    setResult({ tenantId: id, name: created.name, email: owner.email.trim().toLowerCase(), password, subscribed, warnings })
    setStep('done'); setBusy(false); onDone()
  }

  const signInUrl = window.location.origin
  const handover = result && result.password
    ? `Your VolumeOS account is ready.\nSign in: ${signInUrl}\nEmail: ${result.email}\nOne-time password: ${result.password}\nYou'll choose your own password when you first sign in.`
    : ''

  return <Modal title={step === 'done' ? `${result?.name ?? 'Client'} is set up` : 'Onboard a client'} wide
    description={step === 'done' ? undefined : `Step ${['business', 'owner', 'services'].indexOf(step) + 1} of 3`} onClose={onClose}>
    {step === 'business' && <form className="form-stack" onSubmit={nextFromBusiness}>
      <Field label="Business name" name="name" value={business.name} minLength={2} maxLength={120} required placeholder="e.g. Aster Foods Ltd"
        onChange={event => { const name = event.target.value; setBusiness(current => ({ ...current, name, id: current.idEdited ? current.id : slugify(name) })) }} />
      <Field label="Business id" name="id" value={business.id} required pattern="[a-z0-9][a-z0-9\-]{1,62}" title="Lowercase letters, digits and hyphens; 2 to 63 characters"
        hint="Lowercase letters, digits and hyphens. It can't be changed later: it is how every record and sign-in finds this business."
        onChange={event => setBusiness(current => ({ ...current, id: event.target.value.toLowerCase(), idEdited: true }))} />
      {taken && <Note tone="failed">There is already a client with the id <strong className="mono">{business.id}</strong>. Choose another.</Note>}
      <div className="row" style={{ justifyContent: 'flex-end' }}><button className="button button-primary" disabled={taken}>Next: the owner's sign-in <ArrowRight size={15} /></button></div>
    </form>}

    {step === 'owner' && <form className="form-stack" onSubmit={event => { event.preventDefault(); setStep('services') }}>
      <p className="body-copy">The person at {business.name} who runs their account: they top up the balance and see invoices, receipts and their engagements.</p>
      <div className="form-grid">
        <Field label="First name" name="first_name" value={owner.first_name} required maxLength={80} onChange={event => setOwner({ ...owner, first_name: event.target.value })} />
        <Field label="Last name" name="last_name" value={owner.last_name} required maxLength={80} onChange={event => setOwner({ ...owner, last_name: event.target.value })} />
      </div>
      <Field label="Email" name="email" type="email" value={owner.email} required onChange={event => setOwner({ ...owner, email: event.target.value })}
        hint="This is their sign-in. They get a one-time password from you and choose their own at first sign-in." />
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <button type="button" className="button button-secondary" onClick={() => setStep('business')}>Back</button>
        <button className="button button-primary">Next: services <ArrowRight size={15} /></button>
      </div>
    </form>}

    {step === 'services' && <form className="form-stack" onSubmit={event => { event.preventDefault(); void finish() }}>
      <p className="body-copy">Optional: subscribe {business.name} now. You can add services later from the catalogue.</p>
      {sellable.length === 0 ? <Note>No services are on sale yet.</Note> : <div className="stack">
        {sellable.map(offer => {
          const tiers = offer.tiers.map(tier => tier.id)
          const chosen = services[offer.id]
          return <label key={offer.id} className="row" style={{ gap: 10, alignItems: 'center' }}>
            <input type="checkbox" checked={Boolean(chosen)} onChange={event => setServices(current => {
              const next = { ...current }
              if (event.target.checked) next[offer.id] = tiers[0] ?? 'starter'; else delete next[offer.id]
              return next
            })} />
            <OfferMark id={offer.id} name={offer.name} />
            {chosen && tiers.length > 1 && <select aria-label={`${offer.name} tier`} value={chosen} onChange={event => setServices({ ...services, [offer.id]: event.target.value })}>
              {tiers.map(tier => <option key={tier} value={tier}>{tier}</option>)}
            </select>}
          </label>
        })}
      </div>}
      {failure && <Note tone="failed">{failure}</Note>}
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <button type="button" className="button button-secondary" onClick={() => setStep('owner')} disabled={busy}>Back</button>
        <Submit busy={busy} label={`Create ${business.name || 'client'}`} disabled={busy} />
      </div>
    </form>}

    {step === 'done' && result && <div className="form-stack">
      <dl className="props">
        <dt>Business id</dt><dd className="mono">{result.tenantId}</dd>
        <dt>Status</dt><dd>Onboarding</dd>
        <dt>Services</dt><dd>{result.subscribed.length ? result.subscribed.join(', ') : 'None yet'}</dd>
        {result.password && <><dt>Owner sign-in</dt><dd className="mono">{result.email}</dd>
          <dt>One-time password</dt><dd className="mono">{result.password}</dd></>}
      </dl>
      {result.password && <Note tone="attention">This password is shown once and isn't stored. Send it to the owner now; they replace it when they first sign in.</Note>}
      {result.warnings.map(warning => <Note key={warning} tone="failed">{warning}</Note>)}
      <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        {handover ? <button className="button button-secondary" onClick={() => void navigator.clipboard.writeText(handover)}><Copy size={15} /> Copy sign-in details</button> : <span />}
        <button className="button button-primary" onClick={() => switchTenant(result.tenantId)}>Open {result.name} <ArrowRight size={15} /></button>
      </div>
    </div>}
  </Modal>
}
