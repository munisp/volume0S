import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Ban, Check, CircleAlert, Clock3, FileCheck2, Loader2, Plus, RefreshCw, ShieldCheck } from 'lucide-react'
import { ApiError, VolumeOSApi } from '../lib/api'
import { Confirm, KindStatus } from '../components/ui'
import { dateTime } from '../lib/format'
import type { EngagementOffer, EngagementSnapshot } from '../lib/types'

type Props = {
  api: VolumeOSApi
  connected: boolean
  notify: (kind: 'success' | 'error' | 'info', message: string) => void
  /** Engagement to open first (from the page URL). */
  initialId?: string
  /** Called when the person opens another engagement, so the page URL can follow. */
  onSelect?: (engagementId: string) => void
  /** Whether this person's role may start / review engagements; the server checks again either way. */
  canStart?: boolean
  canReview?: boolean
  /** Shown instead of the start form or the review buttons when the role doesn't allow them. */
  roleNote?: (action: 'start' | 'review') => ReactNode
  /** How to name the person who decided (the review stores their account id). */
  reviewerLabel?: (reviewerId: string) => string
}

const offerNames: Record<EngagementOffer, string> = {
  '27': 'Website Makeover Studio',
  '28': 'Video Ad Packs',
  '29': 'Course Tutor',
  '30': 'AI Team Install',
  '31': 'Replacement Part Design',
  '32': 'Industry Audio Briefings',
  '33': 'Bid Radar',
  '34': 'Website Guard',
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'The operation could not be completed.'
}

/** The console-wide status vocabulary (lib/workflows): the same colour means the same thing on every screen. */
function Badge({ status }: { status: string }) {
  return <KindStatus kind="engagement" status={status} />
}

function formatTime(value: string) {
  if (!value) return 'Not yet recorded'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('en-NG', { dateStyle: 'medium', timeStyle: 'short' })
}

export function EngagementOperationsSection({ api, connected, notify, initialId, onSelect, canStart = true, canReview = true, roleNote, reviewerLabel = id => id }: Props) {
  const [engagements, setEngagements] = useState<EngagementSnapshot[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [selected, setSelected] = useState<EngagementSnapshot | null>(null)
  const [loading, setLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [feedError, setFeedError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [stalePlan, setStalePlan] = useState(false)
  const [reviewReason, setReviewReason] = useState('')
  const [confirming, setConfirming] = useState(false)
  // A deep link to an engagement this business doesn't have: say so, never open another one instead (BB-04).
  const [missingId, setMissingId] = useState<string | null>(null)
  const requestGeneration = useRef(0)
  const selectedIdRef = useRef('')
  const initialIdRef = useRef(initialId)

  const refresh = useCallback(async (preferredId?: string) => {
    if (!connected) {
      setEngagements([])
      setSelected(null)
      setSelectedId('')
      selectedIdRef.current = ''
      return
    }
    const generation = ++requestGeneration.current
    setLoading(true)
    setDetailLoading(true)
    try {
      const feed = await api.engagements()
      if (generation !== requestGeneration.current) return
      setEngagements(feed.engagements)
      const currentId = selectedIdRef.current
      const linked = initialIdRef.current
      const has = (id: string | undefined) => Boolean(id) && feed.engagements.some(item => item.engagement_id === id)
      const nextId = has(preferredId) ? preferredId!
        : has(currentId) ? currentId
          : !currentId && linked ? (has(linked) ? linked : '')
            : feed.engagements[0]?.engagement_id ?? ''
      setMissingId(!currentId && linked && !has(linked) ? linked : null)
      selectedIdRef.current = nextId
      setSelectedId(nextId)
      const snapshot = nextId ? await api.engagement(nextId) : null
      if (generation !== requestGeneration.current) return
      setSelected(snapshot)
      setFeedError(null)
      setDetailError(null)
    } catch (error) {
      if (generation === requestGeneration.current) setFeedError(errorMessage(error))
    } finally {
      if (generation === requestGeneration.current) {
        setLoading(false)
        setDetailLoading(false)
      }
    }
  }, [api, connected])

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(initialIdRef.current), 0)
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, 30_000)
    return () => {
      window.clearTimeout(initial)
      window.clearInterval(timer)
      requestGeneration.current += 1
    }
  }, [refresh])

  async function inspect(engagementId: string) {
    onSelect?.(engagementId)
    setMissingId(null)
    const generation = ++requestGeneration.current
    selectedIdRef.current = engagementId
    setSelectedId(engagementId)
    setSelected(null)
    setDetailLoading(true)
    setDetailError(null)
    setStalePlan(false)
    try {
      const snapshot = await api.engagement(engagementId)
      if (generation === requestGeneration.current) setSelected(snapshot)
    } catch (error) {
      if (generation === requestGeneration.current) setDetailError(errorMessage(error))
    } finally {
      if (generation === requestGeneration.current) setDetailLoading(false)
    }
  }

  async function startEngagement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    const engagementId = String(form.get('engagement_id'))
    setBusy(true)
    try {
      await api.startEngagement({
        engagement_id: engagementId,
        offer: String(form.get('offer')) as EngagementOffer,
        title: String(form.get('title')),
        scope: String(form.get('scope')),
        requested_outcome: String(form.get('requested_outcome')),
      })
      formElement.reset()
      await refresh(engagementId)
      notify('success', 'Engagement accepted. Planning is asynchronous; no charge or delivery action was started.')
    } catch (error) {
      notify('error', errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function review(decision: 'approved' | 'rejected') {
    if (!selected?.plan) return
    const engagementId = selected.engagement_id
    const planSha256 = selected.plan.sha256
    setStalePlan(false)
    setBusy(true)
    try {
      await api.reviewEngagement(engagementId, decision, reviewReason, planSha256)
      notify('success', decision === 'approved'
        ? 'Plan approval accepted. Delivery and commercial terms remain disabled.'
        : 'Plan rejection accepted. No external delivery action was started.')
      await refresh(engagementId)
    } catch (error) {
      if (error instanceof ApiError && error.code === 'stale_plan') {
        setStalePlan(true)
        await refresh(engagementId)
      }
      notify('error', errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  const awaiting = engagements.filter(item => item.status === 'awaiting-approval').length
  const terminal = engagements.filter(item => ['approved', 'rejected', 'approval-expired', 'failed', 'cancelled'].includes(item.status)).length
  const failedCount = engagements.filter(item => item.status === 'failed').length
  const reviewDisabled = !connected || busy || Boolean(feedError) || Boolean(detailError)
    || selected?.status !== 'awaiting-approval' || !selected.plan || !reviewReason.trim()

  return <div className="page engagement-operations-page">
    <section className="page-heading split-heading">
      <div>
        <p className="section-kicker">Work · Offers 27–34 · plan only</p>
        <h1 className="page-title">Engagements</h1>
        <p className="heading-copy">A service request becomes a generated plan that a person approves or rejects, bound to the plan's exact SHA-256. Engagements are unpriced: nothing is charged or delivered.</p>
      </div>
      <div className="heading-actions">
        <button className="button button-secondary" onClick={() => void refresh(selectedId || undefined)} disabled={!connected || loading}>
          <RefreshCw className={loading ? 'spin' : ''} size={16} /> Refresh engagements
        </button>
      </div>
    </section>

    {!connected && <div className="connection-note"><CircleAlert size={17} /><span>Connect a tenant and configure the orchestrator API to use engagements. No preview queue or mock plan is substituted.</span></div>}
    {feedError && <div className="feed-error" role="alert"><CircleAlert size={17} /><span>Live engagement refresh failed. Review controls are locked rather than relying on stale data. {feedError}</span></div>}

    <section className="section-block">
      <div className="section-heading">
        <div><h2>All engagements</h2></div>
      </div>
      <div className="operational-counts" aria-label="Engagement status summary">
        <span><strong>{engagements.length}</strong> engagements</span>
        <span><strong>{awaiting}</strong> awaiting approval</span>
        <span><strong>{terminal}</strong> finished</span>
        {failedCount > 0 && <span><strong>{failedCount}</strong> failed</span>}
      </div>
      <div className="workspace-grid">
        <section className="queue-panel" aria-label="Engagement queue">
          <div className="panel-heading"><h2>Service engagements</h2><span>{engagements.length} in view</span></div>
          <div className="queue-list">
            {engagements.map(item => <button
              key={item.engagement_id}
              className={`queue-item ${item.engagement_id === selectedId ? 'selected' : ''}`}
              onClick={() => void inspect(item.engagement_id)}
              aria-pressed={item.engagement_id === selectedId}
            >
              <span className="queue-item-top"><strong>{item.title}</strong><Badge status={item.status} /></span>
              <span>Offer {item.offer} · {offerNames[item.offer]}</span>
              <small>{item.engagement_id} · {item.plan ? `Plan ${item.plan.sha256.slice(0, 12)}…` : 'Plan pending'}</small>
            </button>)}
          </div>
          {loading && !engagements.length && <div className="empty-review" role="status"><Loader2 className="spin" size={18} /><p>Loading the authenticated engagement queue…</p></div>}
          {!loading && !engagements.length && <div className="empty-review"><FileCheck2 size={22} /><p>{connected ? 'No engagements are present in this tenant. Create the first plan below.' : 'An authenticated tenant is required.'}</p></div>}
        </section>

        <section className="review-panel" aria-label="Engagement plan review" aria-busy={detailLoading}>
          <div className="review-header">
            <div><span className="data-label">Digest-bound plan review</span><h2>{selected?.title ?? (detailLoading ? 'Loading engagement…' : 'No engagement selected')}</h2><p>{selected ? `Offer ${selected.offer} · ${offerNames[selected.offer]} · ${selected.engagement_id}` : 'Select a queue item or create an engagement below.'}</p></div>
            {selected && <Badge status={selected.status} />}
          </div>
          {missingId && !selected && <div className="feed-error" role="status"><CircleAlert size={17} /><span>There's no engagement “{missingId}” in this business. The link may be mistyped, or the engagement belongs to another business. Choose one from the list.</span></div>}
          {detailError && <div className="feed-error" role="alert"><CircleAlert size={17} /><span>{detailError}</span></div>}
          {stalePlan && <div className="feed-error" role="alert"><RefreshCw size={17} /><span>The plan changed before review. The current plan has been reloaded; inspect its new digest before deciding.</span></div>}
          {detailLoading && <div className="empty-review" role="status"><Loader2 className="spin" size={18} /><p>Reading the current workflow snapshot…</p></div>}
          {!detailLoading && selected && <>
            <div className="release-facts">
              <div className="fact"><span>Commercial terms</span><strong>Unpriced</strong></div>
              <div className="fact"><span>Delivery mode</span><strong>Plan only</strong></div>
              <div className="fact"><span>Charges</span><strong>{selected.charges_enabled ? 'Enabled' : 'Disabled'}</strong></div>
              <div className="fact"><span>External actions</span><strong>{selected.external_actions_enabled ? 'Enabled' : 'Disabled'}</strong></div>
            </div>
            <div className="engagement-timeline"><span><Clock3 size={14} /> Started {formatTime(selected.started_at)}</span><span>Updated {formatTime(selected.updated_at)}</span></div>
            {selected.plan ? <>
              <label className="field"><span>Current plan SHA-256</span><input value={selected.plan.sha256} readOnly aria-label="Current plan SHA-256" /></label>
              <div className="plan-object"><span>Version {selected.plan.version} · {selected.plan.media_type}</span><code>{selected.plan.object_key}</code></div>
            </> : <div className="connection-note"><Clock3 size={17} /><span>The workflow is {selected.status.replaceAll('-', ' ')}. Review controls unlock only after a plan and digest are returned.</span></div>}
            {selected.document && <details className="plan-document" open>
              <summary>Generated plan and mandatory controls</summary>
              <div className="plan-scope"><span>Scope</span><p>{selected.document.scope}</p>{selected.document.requested_outcome && <><span>Requested outcome</span><p>{selected.document.requested_outcome}</p></>}</div>
              <div className="control-columns">
                <div><h3>Mandatory controls</h3><ul>{selected.document.mandatory_controls.map(control => <li key={control}><Check size={14} />{control}</li>)}</ul></div>
                <div><h3>Prohibited actions</h3><ul>{selected.document.prohibited_actions.map(action => <li key={action}><Ban size={14} />{action}</li>)}</ul></div>
              </div>
              <details className="raw-plan"><summary>View exact plan JSON</summary><pre>{JSON.stringify(selected.document, null, 2)}</pre></details>
            </details>}
            {selected.review && <div className={`review-record ${selected.review.decision}`} role="status">
              <ShieldCheck size={18} /><div><strong>Plan {selected.review.decision} by {reviewerLabel(selected.review.reviewer)}</strong><p>{selected.review.reason}</p><span>{dateTime(selected.review.reviewed_at_unix)} · plan fingerprint {selected.review.plan_sha256.slice(0, 12)}…</span></div>
            </div>}
            {selected.status === 'failed' && <div className="feed-error" role="status"><CircleAlert size={17} /><span>This engagement failed{selected.stalled_status ? ` while ${selected.stalled_status.replaceAll('-', ' ')}` : ''} and won't continue. Create a new engagement to try again.</span></div>}
            {selected.status === 'approval-expired' && <div className="feed-error" role="status"><Clock3 size={17} /><span>The 30-day approval window expired. This plan cannot be reviewed; create a new engagement for a fresh plan.</span></div>}
            {selected.status === 'approved' && <div className="connection-note"><ShieldCheck size={17} /><span>Approval covers this plan only. It does not activate pricing, subscriptions, customer delivery, monitoring, procurement, or external integrations.</span></div>}
            {selected.status === 'awaiting-approval' && selected.plan && !canReview && roleNote?.('review')}
            {selected.status === 'awaiting-approval' && selected.plan && canReview && <>
              <label className="field"><span>Review reason</span><textarea rows={3} maxLength={1000} value={reviewReason} onChange={event => setReviewReason(event.target.value)} placeholder="In your own words: what you checked, and why you approve or reject" required /></label>
              <div className="safety-actions">
                <button className="button button-primary" onClick={() => setConfirming(true)} disabled={reviewDisabled}>{busy ? <Loader2 className="spin" size={16} /> : <Check size={16} />} Approve exact plan</button>
                <button className="button button-danger-quiet" onClick={() => void review('rejected')} disabled={reviewDisabled}><Ban size={16} /> Reject plan</button>
              </div>
            </>}
          </>}
        </section>
      </div>
    </section>

    <section className="section-block">
      <div className="section-heading"><div><p className="section-kicker">New service request</p><h2>Create a plan-only engagement</h2></div><span className="data-label">No pricing · no charges · no external delivery</span></div>
      {!canStart ? roleNote?.('start') : <form onSubmit={startEngagement} className="approval-form">
        <div className="form-grid">
          <label className="field"><span>Offer</span><select name="offer" defaultValue="27" aria-label="Offer" required>{Object.entries(offerNames).map(([id, name]) => <option key={id} value={id}>{id} · {name}</option>)}</select></label>
          <label className="field"><span>Engagement ID</span><input name="engagement_id" placeholder="makeover-aster-01" pattern="[a-zA-Z0-9][a-zA-Z0-9._:\-]*" maxLength={96} required /></label>
        </div>
        <label className="field"><span>Engagement title</span><input name="title" placeholder="Aster Foods website improvement plan" minLength={1} maxLength={160} required /></label>
        <label className="field"><span>Scope</span><textarea name="scope" rows={5} placeholder="Describe the customer context, requested work, source materials, constraints, and acceptance boundary." minLength={1} maxLength={10000} required /></label>
        <label className="field"><span>Requested outcome (optional)</span><textarea name="requested_outcome" rows={3} placeholder="State the outcome the customer asked for; do not include prices or promises of completed delivery." maxLength={5000} /></label>
        <p className="body-copy">Your business and your name as the requester come from your sign-in. Submission starts plan generation only; approval never activates commercial terms or production delivery.</p>
        <button className="button button-primary" disabled={!connected || busy || Boolean(feedError)}>{busy ? <Loader2 className="spin" size={16} /> : <Plus size={16} />} Create engagement plan</button>
      </form>}
    </section>
    {confirming && selected?.plan && <Confirm title="Approve this exact plan?" confirmLabel="Approve plan" busy={busy}
      onCancel={() => setConfirming(false)} onConfirm={() => { setConfirming(false); void review('approved') }}>
      <p>You're approving version {selected.plan.version} of “{selected.title}”, fingerprint <span className="mono">{selected.plan.sha256.slice(0, 12)}…</span>, with your reason:</p>
      <p style={{ marginTop: 6 }}><em>{reviewReason.trim()}</em></p>
      <p style={{ marginTop: 8 }}>Approval covers this plan only. It doesn't charge anything or start delivery.</p>
    </Confirm>}
  </div>
}
