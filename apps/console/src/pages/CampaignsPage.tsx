import { useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Plus, RefreshCw } from 'lucide-react'
import { useSession, errorText } from '../state/session-context'
import { useOperations } from '../state/operations-context'
import { allowed } from '../lib/access'
import { byRecent } from '../lib/workflows'
import { Confirm, Empty, Field, Modal, Note, PageHead, RoleNote, StageSteps, Submit, When, WorkStatus } from '../components/ui'
import { dateTime, money } from '../lib/format'
import { CampaignDraftPanel, ContactsPanel, SocialPanel } from '../sections/CampaignContent'

const ID_PATTERN = '[a-zA-Z0-9][a-zA-Z0-9._:\\-]{0,127}'

/** Offers 01 Come-Back Engine and 02 Content Cadence run as campaign workflows. */
export function CampaignsPage() {
  const { api, roles, notify, tenant } = useSession()
  const { feeds, refresh, refreshing } = useOperations()
  const { id } = useParams()
  const navigate = useNavigate()
  const [modal, setModal] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [approvalId, setApprovalId] = useState('')
  const feed = feeds.campaign
  const campaigns = byRecent(feed.items)
  // A link to a campaign this business doesn't have says so; it never opens another campaign (BB-04).
  const selected = campaigns.find(item => item.id === id) ?? (id ? undefined : campaigns[0])
  const missing = Boolean(id && feed.load === 'ok' && !selected)

  async function run(action: () => Promise<void>) {
    setBusy(true)
    try { await action() } catch (error) { notify('error', errorText(error)) } finally { setBusy(false) }
  }

  function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const campaignId = String(data.get('campaign_id')).trim()
    void run(async () => {
      await api.startCampaign({ campaign_id: campaignId, cycle_id: String(data.get('cycle_id')).trim(), brief: String(data.get('brief')), amount_minor: Math.round(Number(data.get('amount')) * 100), currency: 'NGN' })
      setModal(false); await refresh(); navigate(`/services/campaigns/${encodeURIComponent(campaignId)}`)
      notify('success', 'Campaign started. It will wait for approval before any money is reserved or anything is sent.')
    })
  }

  function approve() {
    if (!selected) return
    void run(async () => {
      await api.approveCampaign(selected.id, approvalId.trim() || `approval-${selected.id}-01`)
      setConfirm(false); await refresh()
      notify('success', 'Approved. The workflow now reserves the funds, then sends; delivery is a later stage.')
    })
  }

  return <div className="page">
    <PageHead eyebrow="Services · 01 Come-Back Engine · 02 Content Cadence" title="Campaigns"
      description="Drafted campaigns wait for a person's approval. Only after approval are funds reserved and messages sent; suppression and the outbound kill switch are checked before every send."
      actions={<>
        <button className="button button-secondary" onClick={() => void refresh()} disabled={refreshing}><RefreshCw size={14} className={refreshing ? 'spin' : ''} /> Refresh</button>
        {allowed(roles, 'startCampaign') && <button className="button button-primary" onClick={() => setModal(true)} disabled={feed.load === 'error'} title={feed.load === 'error' ? 'Paused until the campaign list loads' : undefined}><Plus size={15} /> New campaign</button>}
      </>} />
    {!allowed(roles, 'startCampaign') && <RoleNote action="startCampaign" />}
    {tenant && tenant.state !== 'active' && <Note tone="attention">{tenant.name} is <strong>{tenant.state}</strong>: drafts can be written and reviewed, but nothing is sent to customers until the business is <strong>Active</strong> (Client › Lifecycle).</Note>}
    {missing && <Note tone="failed">There's no campaign “{id}” in this business. The link may be mistyped, or the campaign belongs to another business.</Note>}
    {feed.load === 'error' && <Note tone="failed">The campaign list didn't load: {feed.error} Actions are paused until it does.</Note>}
    <div className="workspace-grid">
      <section className="queue-panel" aria-label="Campaigns">
        <div className="panel-heading"><h2>Campaigns</h2><span>{campaigns.length}</span></div>
        <div className="queue-list">{campaigns.map(item => <button key={item.id} className={`queue-item ${item.id === selected?.id ? 'selected' : ''}`} onClick={() => navigate(`/services/campaigns/${encodeURIComponent(item.id)}`)}>
          <span className="queue-item-top"><strong>{item.title}</strong><WorkStatus item={item} /></span>
          <span>{money(item.amountMinor, item.currency)} reservation</span><small><When value={item.updatedAt} /></small>
        </button>)}</div>
        {!campaigns.length && <Empty title={feed.load === 'ok' ? 'No campaigns yet' : 'Loading campaigns'} />}
      </section>
      {selected ? <section className="review-panel" aria-label="Campaign">
        <div className="review-header"><div><span className="eyebrow">Campaign</span><h2>{selected.title}</h2><p className="mono">{selected.id}</p></div><WorkStatus item={selected} /></div>
        <StageSteps item={selected} />
        <div className="release-facts">
          <div className="fact"><span>Reservation</span><strong>{money(selected.amountMinor, selected.currency)}</strong></div>
          <div className="fact"><span>Started</span><strong>{dateTime(selected.startedAt)}</strong></div>
          <div className="fact"><span>Last activity</span><strong>{dateTime(selected.updatedAt)}</strong></div>
          <div className="fact"><span>Approval window</span><strong>24 hours</strong></div>
        </div>
        <CampaignDraftPanel campaignId={selected.id} status={selected.status} />
        {selected.error && <Note tone="failed">{selected.error}{selected.stalledAt ? ` (stopped while ${selected.stalledAt.replaceAll('_', ' ')})` : ''}</Note>}
        {selected.status === 'awaiting_approval' && (allowed(roles, 'approveCampaign')
          ? <div className="approval-form">
              <Field label="Approval reference" name="approval_id" value={approvalId} onChange={event => setApprovalId(event.target.value)} placeholder={`approval-${selected.id}-01`} pattern={ID_PATTERN} hint="Recorded with the approval. Leave blank to use the suggested one." />
              <div className="safety-actions"><button className="button button-primary" onClick={() => setConfirm(true)} disabled={busy || feed.load === 'error'}>Approve campaign</button></div>
            </div>
          : <RoleNote action="approveCampaign" />)}
        {selected.status === 'approved' && <Note>Approved, not yet delivered: the workflow reserves funds and sends next.</Note>}
      </section> : <section className="review-panel"><Empty title="Select a campaign" /></section>}
    </div>
    <ContactsPanel />
    <SocialPanel />

    {modal && <Modal title="New campaign" description="Starts a draft that waits for approval. Nothing is reserved or sent until a person approves it." onClose={() => setModal(false)} wide>
      <form onSubmit={start} className="form-stack">
        <div className="form-grid">
          <Field label="Campaign id" name="campaign_id" placeholder="winback-nov-01" pattern={ID_PATTERN} title="Letters, digits, dot, dash, colon or underscore; starts with a letter or digit" required hint="Letters, digits and - _ . : only." />
          <Field label="Billing cycle id" name="cycle_id" placeholder="cycle-2026-11" pattern={ID_PATTERN} required />
        </div>
        <label className="field"><span>Brief</span><textarea name="brief" rows={4} maxLength={4000} placeholder="Audience, offer, tone and the action you want customers to take. Contact details are redacted before AI drafting." required /></label>
        <Field label="Reservation (NGN)" name="amount" type="number" min="1" step="0.01" required hint="Reserved from the prepaid balance only after approval." />
        <Submit busy={busy} label="Start campaign draft" />
      </form>
    </Modal>}
    {confirm && selected && <Confirm title={`Approve ${selected.title}?`} confirmLabel="Approve and continue" busy={busy} onCancel={() => setConfirm(false)} onConfirm={approve}>
      <p>Approving sends the draft above to every contact who agreed to hear from this business, and charges <strong>{money(selected.amountMinor, selected.currency)}</strong> from the prepaid balance. If nothing can be sent, the charge is released.</p>
    </Confirm>}
  </div>
}
