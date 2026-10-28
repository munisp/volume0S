import { useCallback, useEffect, useState, type FormEvent, type InputHTMLAttributes, type ReactNode } from 'react'
import {
  ArrowDownToLine, ArrowRight, BadgeDollarSign, CheckCircle2, CircleAlert,
  Clock3, FileCheck2, FileText, Landmark, Loader2, Plus, ReceiptText,
  RefreshCw, ShieldCheck, WalletCards,
} from 'lucide-react'
import type { VolumeOSApi } from '../lib/api'
import type {
  ArAgingReport, Balance, BillingCycleSnapshot, BillingDocument, Checkout, CollectionReceipt,
  ExportMetadata, PartnerStatement, TrialBalance,
} from '../lib/types'

type Notice = { kind: 'success' | 'error'; message: string } | null

function minorFromMajor(value: FormDataEntryValue | null): string {
  const text = String(value ?? '')
  const match = /^(0|[1-9]\d*)(?:\.(\d{1,2}))?$/.exec(text)
  if (!match) throw new Error('Enter a valid amount with no more than two decimal places.')
  const minor = BigInt(match[1]) * 100n + BigInt((match[2] ?? '').padEnd(2, '0') || '0')
  if (minor > 1_000_000_000_000n) throw new Error('The amount exceeds the billing API limit.')
  return minor.toString()
}

function formatMinor(value: string | null | undefined, currency = 'NGN') {
  if (value == null || !/^-?\d+$/.test(value)) return 'Unavailable'
  const amount = BigInt(value)
  const negative = amount < 0n
  const absolute = negative ? -amount : amount
  const major = (absolute / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${negative ? '−' : ''}${currency} ${major}.${(absolute % 100n).toString().padStart(2, '0')}`
}

function isoToday() { return new Date().toISOString().slice(0, 10) }
function isoMonth() { return new Date().toISOString().slice(0, 7) }
function text(value: unknown, fallback = '—') { return typeof value === 'string' && value ? value : fallback }

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function BillingSection({ api, connected, platform }: { api: VolumeOSApi; connected: boolean; platform: boolean }) {
  const [balance, setBalance] = useState<Balance | null>(null)
  const [cycles, setCycles] = useState<BillingCycleSnapshot[]>([])
  const [documents, setDocuments] = useState<BillingDocument[]>([])
  const [aging, setAging] = useState<ArAgingReport | null>(null)
  const [asOf, setAsOf] = useState(isoToday())
  const [loading, setLoading] = useState(false)
  const [feedHealthy, setFeedHealthy] = useState(false)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState<Notice>(null)
  const [checkout, setCheckout] = useState<Checkout | null>(null)
  const [partner, setPartner] = useState<PartnerStatement | null>(null)
  const [trial, setTrial] = useState<TrialBalance | null>(null)
  const [suspense, setSuspense] = useState<CollectionReceipt[]>([])
  const [lastExport, setLastExport] = useState<{ kind: string; metadata: ExportMetadata } | null>(null)

  const refresh = useCallback(async () => {
    if (!connected) return
    setLoading(true)
    const results = await Promise.allSettled([
      api.balance('NGN'), api.billingCycles(), api.billingDocuments(), api.arAging(asOf),
      platform ? api.openCollectionSuspense() : Promise.resolve({ receipts: [] }),
    ])
    const failures: string[] = []
    results.forEach((result, index) => {
      if (result.status === 'rejected') {
        failures.push(result.reason instanceof Error ? result.reason.message : 'A billing feed could not be loaded.')
        return
      }
      if (index === 0) setBalance(result.value as Balance)
      if (index === 1) setCycles((result.value as { cycles: BillingCycleSnapshot[] }).cycles)
      if (index === 2) setDocuments((result.value as { documents: BillingDocument[] }).documents)
      if (index === 3) setAging(result.value as ArAgingReport)
      if (index === 4) setSuspense((result.value as { receipts: CollectionReceipt[] }).receipts)
    })
    setFeedHealthy(failures.length === 0)
    setNotice(failures.length ? { kind: 'error', message: `Some live billing feeds failed. Recovery actions are disabled until a clean refresh. ${failures[0]}` } : null)
    setLoading(false)
  }, [api, asOf, connected, platform])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (!connected) {
        setBalance(null); setCycles([]); setDocuments([]); setAging(null)
        setPartner(null); setTrial(null); setCheckout(null); setSuspense([]); setFeedHealthy(false)
        return
      }
      void refresh()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [connected, refresh])

  async function action(key: string, operation: () => Promise<void>) {
    setBusy(key); setNotice(null)
    try { await operation() } catch (error) {
      setNotice({ kind: 'error', message: error instanceof Error ? error.message : 'The billing action could not be completed.' })
    } finally { setBusy('') }
  }

  async function topup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    await action('topup', async () => {
      const reference = String(data.get('reference'))
      const result = await api.startTopup(String(data.get('provider')), reference, {
        amount_minor: minorFromMajor(data.get('amount')), currency: 'NGN',
        customer_email: String(data.get('customer_email')), redirect_url: String(data.get('redirect_url')),
      })
      setCheckout(result)
      setNotice({ kind: 'success', message: 'Provider checkout created. Funds are not available until signature verification and independent settlement confirmation complete.' })
    })
  }

  async function startCycle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    await action('cycle', async () => {
      const party = (prefix: string) => ({
        legal_name: String(data.get(`${prefix}_legal_name`)), tin: String(data.get(`${prefix}_tin`)),
        address: String(data.get(`${prefix}_address`)),
      })
      await api.startBillingCycle({
        subscription_id: String(data.get('subscription_id')), offer: String(data.get('offer')),
        cycle: String(data.get('cycle')), issue_date: String(data.get('issue_date')),
        retainer_minor: Number(minorFromMajor(data.get('retainer'))),
        setup_minor: Number(minorFromMajor(data.get('setup') || '0')),
        usage_minor: 0, tax_minor: Number(minorFromMajor(data.get('tax') || '0')),
        currency: 'NGN', seller: party('seller'), buyer: party('buyer'),
        billing_whatsapp: String(data.get('billing_whatsapp')),
      })
      setNotice({ kind: 'success', message: 'Monthly billing workflow started. Usage overages remain separate weekly rollup postings.' })
      await refresh()
    })
  }

  async function recover(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    await action('recovery', async () => {
      const [subscriptionId, cycle] = String(data.get('billing_cycle')).split('|')
      const result = await api.recoverBilling({
        subscription_id: subscriptionId, cycle, provider: String(data.get('provider')), reference: String(data.get('reference')),
      })
      setNotice({ kind: 'success', message: `Confirmed collection ${result.recovery_reference} was sent to the billing workflow. Refresh to verify settlement.` })
      await refresh()
    })
  }

  async function resolveSuspense(event: FormEvent<HTMLFormElement>, receipt: CollectionReceipt) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    await action(`resolution-${receipt.event_id}`, async () => {
      const result = await api.resolveCollectionSuspense(
        receipt, String(data.get('tenant_id')), String(data.get('reason')),
      )
      setNotice({ kind: 'success', message: `Suspense resolved to ${result.tenant_id}. The original provider settlement was not reused.` })
      await refresh()
    })
  }

  async function downloadDocument(document: BillingDocument) {
    await action(`document-${document.document_id}`, async () => {
      const blob = await api.documentPdf(document.kind, document.document_id)
      saveBlob(blob, `${document.document_number}.pdf`)
    })
  }

  async function loadPartner(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    await action('partner', async () => {
      const result = await api.partnerStatement(String(data.get('partner_id')), String(data.get('start')), String(data.get('end')))
      setPartner(result); setLastExport(null)
    })
  }

  async function exportPartner() {
    if (!partner) return
    await action('partner-export', async () => {
      const result = await api.exportPartnerStatement(partner.partner_id, partner.period_start, partner.period_end)
      setLastExport({ kind: 'partner-share', metadata: result.export })
      setNotice({ kind: 'success', message: `Partner statement sealed with SHA-256 ${result.export.sha256.slice(0, 12)}… under compliance retention.` })
    })
  }

  async function createContract(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    await action('contract', async () => {
      const effectiveFrom = new Date(String(data.get('effective_from')))
      const effectiveToText = String(data.get('effective_to') || '')
      await api.createPartnerContract({
        contract_id: String(data.get('contract_id')), partner_id: String(data.get('partner_id')),
        offer: String(data.get('offer')), basis_points: Number(data.get('basis_points')),
        effective_from: effectiveFrom.toISOString(), effective_to: effectiveToText ? new Date(effectiveToText).toISOString() : null,
      })
      setNotice({ kind: 'success', message: 'Append-only partner terms recorded. Existing terms were not modified.' })
    })
  }

  async function loadTrial(currency: 'NGN' | 'USD') {
    await action('trial', async () => { setTrial(await api.trialBalance(currency)); setLastExport(null) })
  }

  async function exportAging() {
    await action('aging-export', async () => {
      const result = await api.exportArAging(asOf)
      setAging(result.report); setLastExport({ kind: 'ar-aging', metadata: result.export })
      setNotice({ kind: 'success', message: 'AR aging exported as canonical, content-hashed JSON with compliance-mode retention.' })
    })
  }

  async function exportTrial() {
    if (!trial) return
    await action('trial-export', async () => {
      const result = await api.exportTrialBalance(trial.currency as 'NGN' | 'USD')
      setLastExport({ kind: 'trial-balance', metadata: result.export })
      setNotice({ kind: 'success', message: 'Balanced TigerBeetle trial balance sealed in immutable export storage.' })
    })
  }

  async function downloadExport() {
    if (!lastExport) return
    await action('export-download', async () => {
      const blob = await api.financialExport(lastExport.kind, lastExport.metadata.sha256)
      saveBlob(blob, `${lastExport.kind}-${lastExport.metadata.sha256.slice(0, 12)}.json`)
    })
  }

  const recoverable = cycles.filter(cycle => ['awaiting_funds', 'paused'].includes(cycle.state))
  const canAct = connected && feedHealthy && !loading

  return <div className="page billing-page">
    <section className="page-heading split-heading"><div><p className="section-kicker">Money & billing</p><h1 className="page-title">Every kobo, accounted for.</h1><p className="heading-copy">TigerBeetle holds balances. Postgres holds documents and workflow metadata. Collections credit prepaid funds only after provider settlement is independently confirmed.</p></div><div className="heading-actions"><button className="button button-secondary" onClick={() => void refresh()} disabled={!connected || loading}><RefreshCw className={loading ? 'spin' : ''} size={16} /> Refresh live books</button></div></section>
    <div className="data-label page-data-label">{connected ? feedHealthy ? 'Live tenant-scoped money feeds · no financial response is cached' : 'Waiting for a clean live billing refresh' : 'Sign in to access live balances and financial documents. No illustrative money records are shown.'}</div>
    {notice && <div className={`feed-error ${notice.kind === 'success' ? 'billing-success' : ''}`} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.kind === 'success' ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}<span>{notice.message}</span></div>}

    <section className="billing-pulse">
      <div className="balance-card"><div className="balance-card-top"><span className="data-label">Available prepaid · NGN</span><WalletCards size={23} /></div><strong>{formatMinor(balance?.available_minor, 'NGN')}</strong><p>Credits posted − debits posted − debits pending. Reserved funds cannot be spent twice.</p><div className="balance-ledger"><span>Pending reservations</span><b>{formatMinor(balance?.debits_pending)}</b></div></div>
      <div className="billing-metric"><ReceiptText size={21} /><span>Open billing cycles</span><strong>{cycles.length}</strong><p>{recoverable.length} need funding or recovery</p></div>
      <div className="billing-metric"><FileText size={21} /><span>Immutable documents</span><strong>{documents.length}</strong><p>Invoices and posted receipts</p></div>
      <div className="billing-metric"><ShieldCheck size={21} /><span>AR outstanding</span><strong>{aging ? Object.keys(aging.totals).length : '—'}</strong><p>{aging ? `Currencies as of ${aging.as_of}` : 'No current report'}</p></div>
    </section>

    <section className="billing-grid">
      <div className="section-block compact"><SectionTitle icon={<Landmark size={20} />} title="Fund prepaid balance" copy="Create a hosted provider checkout. A redirect is not proof of settlement." />
        <form className="form-stack" onSubmit={topup}><div className="form-grid"><Field label="Amount (NGN)" name="amount" inputMode="decimal" placeholder="125000.00" required /><Field label="Collection reference" name="reference" placeholder="topup-2026-001" pattern="[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}" required /></div><div className="form-grid"><label className="field"><span>Provider</span><select name="provider"><option value="paystack">Paystack</option><option value="flutterwave">Flutterwave</option><option value="moniepoint">Moniepoint</option></select></label><Field label="Customer email" name="customer_email" type="email" required /></div><Field label="Return URL" name="redirect_url" type="url" defaultValue={`${window.location.origin}/billing/callback`} pattern="https://.*" required /><Submit busy={busy === 'topup'} disabled={!canAct} label="Create settlement-verified checkout" /></form>
        {checkout && <a className="checkout-link" href={checkout.checkout_url} target="_blank" rel="noreferrer">Continue to {checkout.provider} checkout <ArrowRight size={16} /></a>}
      </div>
      <div className="section-block compact"><SectionTitle icon={<Clock3 size={20} />} title="Dunning & recovery" copy="Only a credited collection receipt can resume an awaiting or paused cycle." />
        <form className="form-stack" onSubmit={recover}><label className="field"><span>Billing cycle</span><select name="billing_cycle" required>{recoverable.map(cycle => <option key={`${cycle.subscription_id}-${cycle.cycle}`} value={`${cycle.subscription_id}|${cycle.cycle}`}>{cycle.subscription_id} · {cycle.cycle} · {cycle.state}</option>)}</select></label><div className="form-grid"><label className="field"><span>Provider</span><select name="provider"><option value="paystack">Paystack</option><option value="flutterwave">Flutterwave</option><option value="moniepoint">Moniepoint</option></select></label><Field label="Settled collection reference" name="reference" required /></div><Submit busy={busy === 'recovery'} disabled={!canAct || recoverable.length === 0} label="Verify receipt & resume cycle" /></form>
        <div className="cycle-list">{cycles.map(cycle => <div className="cycle-row" key={`${cycle.subscription_id}-${cycle.cycle}`}><div><strong>{cycle.subscription_id}</strong><span>{cycle.cycle} · {cycle.dunning_attempts} nudges</span></div><Status status={cycle.state} /></div>)}</div>
      </div>
    </section>

    <details className="billing-disclosure"><summary><Plus size={17} /> Start a monthly billing cycle</summary><div className="section-block compact"><p className="body-copy">Retainer reserve → post → receipt. Weekly metering handles usage overages separately; this form always submits usage as zero to prevent double charging.</p><form className="form-stack" onSubmit={startCycle}><div className="form-grid"><Field label="Subscription ID" name="subscription_id" required /><label className="field"><span>Offer</span><select name="offer">{['01','02','03','04','05','06','07','08'].map(id => <option key={id} value={id}>{id}</option>)}</select></label></div><div className="form-grid"><Field label="Cycle" name="cycle" type="month" defaultValue={isoMonth()} required /><Field label="Issue date" name="issue_date" type="date" defaultValue={isoToday()} required /></div><div className="form-grid"><Field label="Retainer (NGN)" name="retainer" inputMode="decimal" required /><Field label="Setup (NGN)" name="setup" inputMode="decimal" defaultValue="0" required /><Field label="Tax (NGN)" name="tax" inputMode="decimal" defaultValue="0" required /></div><PartyFields prefix="seller" title="Seller" /><PartyFields prefix="buyer" title="Buyer" /><Field label="Billing WhatsApp" name="billing_whatsapp" type="tel" placeholder="+2348012345678" required /><Submit busy={busy === 'cycle'} disabled={!canAct} label="Start durable billing workflow" /></form></div></details>

    <section className="section-block"><div className="section-heading"><div><p className="section-kicker">Financial documents</p><h2>Invoices & posted receipts</h2></div><span className="data-label">PDF hashes verified by the operator API</span></div><div className="billing-table-wrap"><table className="billing-table"><thead><tr><th>Document</th><th>Type</th><th>Amount</th><th>Issued</th><th><span className="sr-only">Download</span></th></tr></thead><tbody>{documents.map(document => {
      const amount = text(document.structured.total_minor ?? document.structured.amount_minor, '')
      const currency = text(document.structured.currency, 'NGN')
      return <tr key={document.document_id}><td><strong>{document.document_number}</strong><small>{document.document_id}</small></td><td><Status status={document.kind} /></td><td>{amount ? formatMinor(amount, currency) : 'Structured document'}</td><td>{new Date(document.created_at).toLocaleDateString('en-NG')}</td><td><button className="icon-button" onClick={() => void downloadDocument(document)} disabled={Boolean(busy)} aria-label={`Download ${document.document_number}`}><ArrowDownToLine size={17} /></button></td></tr>
    })}</tbody></table>{connected && documents.length === 0 && <Empty text="No invoice or receipt documents are available in this tenant." />}</div></section>

    <section className="report-grid">
      <div className="section-block compact"><SectionTitle icon={<BadgeDollarSign size={20} />} title="AR aging" copy="Prepaid invoices are due on issue. Later receipts are excluded from the selected point in time." /><div className="report-actions"><Field label="As of" name="as_of" type="date" value={asOf} onChange={event => setAsOf(event.target.value)} /><button className="button button-secondary" onClick={() => void refresh()} disabled={!connected || loading}>Load report</button><button className="button button-primary" onClick={() => void exportAging()} disabled={!canAct || !aging}>{busy === 'aging-export' ? <Loader2 className="spin" size={16} /> : <ShieldCheck size={16} />} Export immutable</button></div><AgingTable report={aging} /></div>
      <div className="section-block compact"><SectionTitle icon={<FileCheck2 size={20} />} title="Partner share" copy="Exact integer shares calculated from posted billing receipts and append-only commercial terms." /><form className="form-stack" onSubmit={loadPartner}><Field label="Partner ID" name="partner_id" defaultValue="partner-1" required /><div className="form-grid"><Field label="Period start" name="start" type="date" defaultValue={`${new Date().getFullYear()}-01-01`} required /><Field label="Period end" name="end" type="date" defaultValue={isoToday()} required /></div><Submit busy={busy === 'partner'} disabled={!canAct} label="Load posted-receipt statement" /></form>{partner && <div className="statement-result"><div><span className="data-label">{partner.receipt_count} posted receipts</span>{Object.entries(partner.totals).map(([currency, totals]) => <p key={currency}><strong>{formatMinor(totals.partner_share_minor, currency)}</strong><span>partner share on {formatMinor(totals.receipt_minor, currency)}</span></p>)}</div><button className="button button-secondary" onClick={() => void exportPartner()} disabled={Boolean(busy)}>Export immutable</button></div>}</div>
    </section>

    <details className="billing-disclosure"><summary><Plus size={17} /> Add append-only partner terms</summary><div className="section-block compact"><form className="form-stack" onSubmit={createContract}><div className="form-grid"><Field label="Contract ID" name="contract_id" required /><Field label="Partner ID" name="partner_id" required /><label className="field"><span>Offer</span><select name="offer">{['01','02','03','04','05','06','07','08'].map(id => <option key={id}>{id}</option>)}</select></label><Field label="Share (basis points)" name="basis_points" type="number" min="0" max="10000" defaultValue="2000" required /><Field label="Effective from" name="effective_from" type="datetime-local" required /><Field label="Effective to (optional)" name="effective_to" type="datetime-local" /></div><p className="body-copy">To change commercial terms, append a new contract with a later effective date. Existing contracts cannot be edited or deleted.</p><Submit busy={busy === 'contract'} disabled={!canAct} label="Append partner contract" /></form></div></details>

    {platform && <section className="section-block"><div className="section-heading"><div><p className="section-kicker">Platform finance · daily review</p><h2>Collection suspense</h2><p className="body-copy">Review unmatched or mismatched settled receipts. Resolution moves the exact isolated amount once and preserves an actor-bound immutable audit.</p></div><span className="data-label">{suspense.length} open</span></div><div className="cycle-list">{suspense.map(receipt => <form className="form-stack" key={receipt.event_id} onSubmit={event => void resolveSuspense(event, receipt)}><div className="cycle-row"><div><strong>{receipt.provider} · {receipt.reference}</strong><span>{receipt.event_id}</span></div><Status status={formatMinor(receipt.amount_minor, receipt.currency)} /></div><div className="form-grid"><Field label="Credit client tenant" name="tenant_id" pattern="[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}" required /><Field label="Review reason" name="reason" minLength={3} maxLength={1000} required /></div><Submit busy={busy === `resolution-${receipt.event_id}`} disabled={!canAct} label="Resolve exact suspense amount" /></form>)}</div>{suspense.length === 0 && <Empty text="No open collection suspense. New settled exceptions will appear here for platform-finance review." />}</section>}

    {platform && <section className="section-block"><div className="section-heading"><div><p className="section-kicker">Platform finance</p><h2>TigerBeetle trial balance</h2></div><div className="heading-actions"><button className="button button-secondary" onClick={() => void loadTrial('NGN')} disabled={!canAct}>Load NGN</button><button className="button button-secondary" onClick={() => void loadTrial('USD')} disabled={!canAct}>Load USD</button></div></div>{trial ? <><div className="trial-totals"><div><span>Posted debits</span><strong>{formatMinor(trial.total_debits_posted, trial.currency)}</strong></div><div><span>Posted credits</span><strong>{formatMinor(trial.total_credits_posted, trial.currency)}</strong></div><Status status={trial.balanced ? 'balanced' : 'failed'} /></div><div className="billing-table-wrap"><table className="billing-table"><thead><tr><th>Offer</th><th>Debits</th><th>Credits</th><th>Net credit</th></tr></thead><tbody>{trial.revenue_per_offer.map(row => <tr key={row.offer}><td>Offer {row.offer}</td><td>{formatMinor(row.debits_posted, trial.currency)}</td><td>{formatMinor(row.credits_posted, trial.currency)}</td><td>{formatMinor(row.net_credit_minor, trial.currency)}</td></tr>)}</tbody></table></div><button className="button button-primary" onClick={() => void exportTrial()} disabled={Boolean(busy) || !trial.balanced}>Export balanced report</button></> : <Empty text="Load a currency to query account balances directly from TigerBeetle. No balance rows are copied to PostgreSQL." />}</section>}

    {lastExport && <section className="export-receipt"><ShieldCheck size={22} /><div><strong>Compliance-locked financial export</strong><span>SHA-256 {lastExport.metadata.sha256}</span><small>Retained until {new Date(lastExport.metadata.retained_until).toLocaleString('en-NG')}</small></div><button className="button button-secondary" onClick={() => void downloadExport()} disabled={busy === 'export-download'}><ArrowDownToLine size={16} /> Download & verify</button></section>}
  </div>
}

function SectionTitle({ icon, title, copy }: { icon: ReactNode; title: string; copy: string }) {
  return <div className="settings-heading">{icon}<div><h2>{title}</h2><p>{copy}</p></div></div>
}

function PartyFields({ prefix, title }: { prefix: string; title: string }) {
  return <fieldset className="party-fields"><legend>{title} legal details</legend><div className="form-grid"><Field label="Legal name" name={`${prefix}_legal_name`} required /><Field label="TIN" name={`${prefix}_tin`} inputMode="numeric" pattern="[0-9]{8,14}" required /></div><Field label="Address" name={`${prefix}_address`} required /></fieldset>
}

function AgingTable({ report }: { report: ArAgingReport | null }) {
  if (!report) return <Empty text="Select a report date and load live invoice metadata." />
  return <div className="billing-table-wrap"><table className="billing-table"><thead><tr><th>Invoice</th><th>Due</th><th>Age</th><th>Outstanding</th></tr></thead><tbody>{report.invoices.map(invoice => <tr key={invoice.invoice_id}><td>{invoice.document_number}</td><td>{invoice.due_date}</td><td>{invoice.age_days} days</td><td>{formatMinor(invoice.outstanding_minor, invoice.currency)}</td></tr>)}</tbody></table>{report.invoices.length === 0 && <Empty text="No open AR at this point in time. This is the expected prepaid position." />}</div>
}

function Status({ status }: { status: string }) {
  const tone = ['settled', 'balanced', 'receipt'].includes(status) ? 'status-active' : ['failed', 'paused'].includes(status) ? 'status-danger' : ['awaiting_funds'].includes(status) ? 'status-warning' : 'status-neutral'
  return <span className={`status-badge ${tone}`}>{status.replaceAll('_', ' ')}</span>
}

function Empty({ text: copy }: { text: string }) { return <div className="billing-empty"><CircleAlert size={17} /><span>{copy}</span></div> }
function Field({ label, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string; name: string }) { return <label className="field"><span>{label}</span><input {...props} /></label> }
function Submit({ busy, label, disabled }: { busy: boolean; label: string; disabled?: boolean }) { return <button className="button button-primary" disabled={busy || disabled}>{busy ? <Loader2 className="spin" size={17} /> : <ArrowRight size={17} />}{label}</button> }
