import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { useSession, errorText } from '../state/session-context'
import { useOperations } from '../state/operations-context'
import { allowed } from '../lib/access'
import { ApiError } from '../lib/api'
import { byRecent } from '../lib/workflows'
import type { Balance, BillingDocument, Checkout } from '../lib/types'
import { Confirm, Empty, Field, Figure, Note, OfferMark, PageHead, Panel, RoleNote, Status, Submit, When, WorkStatus } from '../components/ui'
import { dateTime, money } from '../lib/format'

const LIFECYCLE: Record<string, string> = { prospect: 'Prospect', onboarding: 'Being set up', active: 'Active', paused: 'Paused', offboarded: 'Closed' }

/** Exact naira-to-kobo conversion (no floating point): "1250.5" -> "125050". */
function kobo(value: string): string {
  const match = /^(0|[1-9]\d*)(?:\.(\d{1,2}))?$/.exec(value.trim())
  if (!match) throw new Error('Enter an amount like 25000 or 25000.50.')
  const minor = BigInt(match[1]) * 100n + BigInt((match[2] ?? '').padEnd(2, '0') || '0')
  if (minor <= 0n || minor > 1_000_000_000_000n) throw new Error('Enter an amount above zero and within the limit.')
  return minor.toString()
}

function useBalance() {
  const { api } = useSession()
  const [state, setState] = useState<{ value: Balance | null; note: string | null; loading: boolean }>({ value: null, note: null, loading: true })
  useEffect(() => {
    let active = true
    api.balance('NGN').then(
      value => active && setState({ value, note: null, loading: false }),
      error => active && setState({ value: null, loading: false, note: error instanceof ApiError && error.code === 'money_service_unavailable' ? 'Billing isn\'t running yet, so your balance can\'t be shown.' : errorText(error) }),
    )
    return () => { active = false }
  }, [api])
  return state
}

export function ClientHomePage() {
  const { tenant, offerName } = useSession()
  const { feeds } = useOperations()
  const navigate = useNavigate()
  const balance = useBalance()
  const engagements = byRecent(feeds.engagement.items)
  const subscriptions = tenant?.subscriptions ?? []

  return <div className="page">
    <PageHead eyebrow={tenant ? LIFECYCLE[tenant.state] ?? tenant.state : undefined} title={tenant ? `Welcome, ${tenant.name}` : 'Welcome'}
      description="Your services, the work your agency is doing for you, and your prepaid balance." />
    <div className="figures">
      <Figure label="Services" value={subscriptions.length} hint="active subscriptions" />
      <Figure label="Engagements in progress" value={feeds.engagement.load === 'ok' ? engagements.filter(item => item.state === 'running' || item.state === 'needs-review').length : '—'} />
      {balance.value?.available_minor != null
        ? <Figure label="Available balance" value={money(balance.value.available_minor)} hint="Ready to spend" />
        : <Figure label="Available balance" value="Unavailable" tone="unknown" hint={balance.loading ? 'Loading' : balance.note ?? ''} />}
    </div>
    <div className="grid-2">
      <Panel title="Your services" actions={<Link className="button button-secondary small" to="/services">All services</Link>} flush>
        {subscriptions.length === 0 ? <Empty title="No services yet">Your account manager adds services to your account.</Empty>
          : <div className="table-wrap"><table className="data"><tbody>{subscriptions.map(sub => <tr key={sub.id}><td><OfferMark id={sub.offer} name={offerName(sub.offer)} /></td><td className="right" style={{ textTransform: 'capitalize' }}>{sub.tier}</td></tr>)}</tbody></table></div>}
      </Panel>
      <Panel title="Work for you" meta="Plans your agency is preparing" actions={<Link className="button button-secondary small" to="/engagements">All engagements</Link>} flush>
        {feeds.engagement.load === 'error' ? <div className="panel-body"><Note tone="failed">Your engagements couldn't be loaded: {feeds.engagement.error}</Note></div>
          : engagements.length === 0 ? <Empty title="No engagements yet" />
          : <div className="table-wrap"><table className="data"><tbody>{engagements.slice(0, 6).map(item => <tr key={item.id} className="link" onClick={() => navigate(item.href)}>
            <td><span className="title">{item.title}</span><span className="sub"><OfferMark id={item.offer} name={offerName(item.offer)} compact /></span></td>
            <td><WorkStatus item={item} /></td><td className="right muted"><When value={item.updatedAt} /></td></tr>)}</tbody></table></div>}
      </Panel>
    </div>
    <p className="small muted">Need something new or have a question? Contact your account manager: requests and messages to the agency aren't available in VolumeOS yet.</p>
  </div>
}

export function WalletPage() {
  const { api, roles, tenant, notify } = useSession()
  const balance = useBalance()
  const [documents, setDocuments] = useState<{ items: BillingDocument[]; loaded: boolean; note: string | null }>({ items: [], loaded: false, note: null })
  const [checkout, setCheckout] = useState<Checkout | null>(null)
  const [pending, setPending] = useState<{ provider: string; reference: string; amount: string; email: string } | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.billingDocuments().then(
      result => setDocuments({ items: result.documents, loaded: true, note: null }),
      error => setDocuments({ items: [], loaded: false, note: errorText(error) }),
    )
  }, [api])

  function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    try {
      kobo(String(data.get('amount')))
      setPending({ provider: String(data.get('provider')), reference: String(data.get('reference')).trim(), amount: String(data.get('amount')).trim(), email: String(data.get('email')).trim() })
    } catch (error) { notify('error', errorText(error)) }
  }

  async function startTopUp() {
    if (!pending) return
    setBusy(true)
    try {
      const result = await api.startTopup(pending.provider, pending.reference, { amount_minor: kobo(pending.amount), currency: 'NGN', customer_email: pending.email, redirect_url: `${window.location.origin}/billing/callback` })
      setCheckout(result); setPending(null)
      notify('info', 'Checkout created. Complete the payment with the provider; the money appears only after they confirm it.')
    } catch (error) { notify('error', errorText(error)) } finally { setBusy(false) }
  }

  const value = balance.value
  const reserved = value ? BigInt(value.debits_pending || '0') : null
  return <div className="page">
    <PageHead eyebrow={tenant?.name} title="Wallet & billing" description="Your prepaid balance pays for approved work. Money is reserved when work is approved and charged when it's done; if it fails, the reservation is released." />
    {value ? <div className="figures">
      <Figure label="Available" value={value.available_minor === null ? '—' : money(value.available_minor)} hint="Ready to spend" />
      <Figure label="Reserved" value={reserved === null ? '—' : money(reserved.toString())} hint="Held for approved work in progress" />
      <Figure label="Total paid in" value={money(value.credits_posted)} hint="Confirmed top-ups" />
      <Figure label="Total charged" value={money(value.debits_posted)} hint="Finished work" />
    </div> : <Panel><Empty title="Your balance isn't available">{balance.loading ? 'Loading…' : balance.note}</Empty></Panel>}

    <div className="grid-2">
      <Panel title="Add funds" meta="Paid through the provider's checkout page">
        {!allowed(roles, 'topUp') ? <RoleNote action="topUp" /> : !value ? <Note>Funding opens when billing is running for your account.</Note> : <form onSubmit={review} className="form-stack">
          <div className="form-grid">
            <Field label="Amount (NGN)" name="amount" inputMode="decimal" placeholder="25000" required />
            <label className="field"><span>Pay with</span><select name="provider"><option value="paystack">Paystack</option><option value="flutterwave">Flutterwave</option><option value="moniepoint">Moniepoint</option></select></label>
          </div>
          <Field label="Your reference" name="reference" placeholder="topup-nov-01" pattern="[a-zA-Z0-9][a-zA-Z0-9._:\-]{0,127}" required hint="So you can recognise this payment later." />
          <Field label="Receipt email" name="email" type="email" required />
          <Submit busy={busy} label="Review top-up" />
        </form>}
        {checkout && <div className="note attention" style={{ marginTop: 12 }}><span>Checkout <strong>{checkout.reference}</strong> is {checkout.status}. <a href={checkout.checkout_url} target="_blank" rel="noreferrer">Open the payment page</a>. Your balance changes only once the provider confirms the payment.</span></div>}
      </Panel>
      <Panel title="Invoices & receipts" flush>
        {!documents.loaded ? <div className="panel-body"><Note>{documents.note ? `Your invoices couldn't be loaded, so none are listed: ${documents.note}` : 'Loading…'}</Note></div>
          : documents.items.length === 0 ? <Empty title="No invoices or receipts yet" />
          : <div className="table-wrap"><table className="data"><thead><tr><th>Document</th><th>Type</th><th className="right">Issued</th></tr></thead><tbody>
            {documents.items.map(doc => <tr key={doc.document_id}><td className="title">{doc.document_number}</td><td><Status state="done" label={doc.kind === 'invoice' ? 'Invoice' : 'Receipt'} /></td><td className="right">{dateTime(doc.created_at)}</td></tr>)}
          </tbody></table></div>}
      </Panel>
    </div>
    {pending && <Confirm title="Confirm top-up" confirmLabel="Continue to checkout" busy={busy} onCancel={() => setPending(null)} onConfirm={() => void startTopUp()}>
      <p>You're about to pay <strong>{money(kobo(pending.amount))}</strong> with {pending.provider[0].toUpperCase() + pending.provider.slice(1)} (reference <span className="mono">{pending.reference}</span>).</p>
      <p style={{ marginTop: 8 }}>The money becomes available only after the provider confirms the payment. Starting a checkout doesn't add anything to your balance.</p>
    </Confirm>}
  </div>
}

export function ProfilePage() {
  const { tenant, user } = useSession()
  if (!tenant) return <div className="page"><PageHead title="Business profile" /><Panel><Empty title="Your business record isn't set up yet">Your account manager creates it during onboarding.</Empty></Panel></div>
  return <div className="page">
    <PageHead title="Business profile" description="What your agency holds about your business. To change it, contact your account manager." />
    <div className="grid-2">
      <Panel title={tenant.name}>
        <dl className="props">
          <dt>Business name</dt><dd>{tenant.name}</dd>
          <dt>Short name</dt><dd className="mono">{tenant.slug}</dd>
          <dt>Status</dt><dd>{LIFECYCLE[tenant.state] ?? tenant.state}</dd>
          <dt>Customer since</dt><dd>{dateTime(tenant.created_at)}</dd>
          <dt>Outbound messages</dt><dd>{tenant.outbound_suspended ? 'Paused by your agency' : 'Allowed'}</dd>
        </dl>
      </Panel>
      <Panel title="Your sign-in">
        <dl className="props"><dt>Name</dt><dd>{String(user?.profile.name ?? '—')}</dd><dt>Email</dt><dd>{String(user?.profile.email ?? '—')}</dd></dl>
      </Panel>
    </div>
  </div>
}

/** Where the payment provider sends the payer back (BB-03). It confirms nothing: money counts only once the provider
 * has confirmed settlement to VolumeOS, which happens separately and is shown on the balance. */
export function PaymentReturnPage({ backTo, backLabel }: { backTo: string; backLabel: string }) {
  const params = new URLSearchParams(window.location.search)
  const reference = params.get('reference') ?? params.get('trxref') ?? params.get('tx_ref') ?? ''
  return <div className="page">
    <PageHead title="Payment submitted" description="Thanks. You've come back from the payment page." />
    <Panel>
      <div className="stack">
        {reference && <p className="body-copy">Reference <span className="mono">{reference}</span></p>}
        <p className="body-copy">If you completed the payment, it appears in the balance once the payment provider confirms it to us. That usually takes a few minutes. This page doesn't confirm the payment itself, so nothing has been added yet.</p>
        <p className="body-copy">If you cancelled or the payment failed, nothing has been charged.</p>
        <div className="row"><Link className="button button-primary" to={backTo}>{backLabel}</Link></div>
      </div>
    </Panel>
  </div>
}
