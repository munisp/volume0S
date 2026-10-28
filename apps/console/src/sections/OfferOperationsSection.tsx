import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Ban, CalendarCheck, Check, CircleAlert, Loader2, MessageSquareText, RefreshCw, Send, ShieldCheck } from 'lucide-react'
import { ApiError, VolumeOSApi } from '../lib/api'
import type { ConversationQueueItem, OfferJobSnapshot } from '../lib/types'

type Props = {
  api: VolumeOSApi
  connected: boolean
  conversationsConnected: boolean
  notify: (kind: 'success' | 'error' | 'info', message: string) => void
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'The operation could not be completed.'
}

function Badge({ status }: { status: string }) {
  const tone = ['released', 'sent', 'approved', 'active'].includes(status) ? 'status-active'
    : ['failed', 'rejected', 'suppressed'].includes(status) ? 'status-danger'
      : ['awaiting-review', 'requires-review'].includes(status) ? 'status-warning' : 'status-neutral'
  return <span className={`status-badge ${tone}`}>{status.replaceAll('-', ' ').replaceAll('_', ' ')}</span>
}

export function OfferOperationsSection({ api, connected, conversationsConnected, notify }: Props) {
  const [jobs, setJobs] = useState<OfferJobSnapshot[]>([])
  const [conversations, setConversations] = useState<ConversationQueueItem[]>([])
  const [selectedJobId, setSelectedJobId] = useState('')
  const [selectedConversationId, setSelectedConversationId] = useState('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [feedError, setFeedError] = useState<string | null>(null)
  const [reviewReason, setReviewReason] = useState('Reviewed against source and release requirements')
  const [draftReason, setDraftReason] = useState('Approved response checked by operator')
  const [slotId, setSlotId] = useState('')
  const [suppressionReason, setSuppressionReason] = useState('Customer withdrew WhatsApp service consent')

  const selectedJob = jobs.find(job => job.job_id === selectedJobId) ?? jobs[0]
  const selectedConversation = conversations.find(item => item.conversation.conversation_id === selectedConversationId) ?? conversations[0]

  const refresh = useCallback(async () => {
    if (!connected) return
    setLoading(true)
    try {
      const [jobFeed, conversationFeed] = await Promise.all([
        api.offerJobs(),
        conversationsConnected ? api.conversationQueue() : Promise.resolve({ conversations: [] }),
      ])
      setJobs(jobFeed.jobs)
      setConversations(conversationFeed.conversations)
      setSelectedJobId(current => jobFeed.jobs.some(job => job.job_id === current) ? current : jobFeed.jobs[0]?.job_id ?? '')
      setSelectedConversationId(current => conversationFeed.conversations.some(item => item.conversation.conversation_id === current)
        ? current : conversationFeed.conversations[0]?.conversation.conversation_id ?? '')
      setFeedError(null)
    } catch (error) {
      setFeedError(errorMessage(error))
    } finally {
      setLoading(false)
    }
  }, [api, connected, conversationsConnected])

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0)
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, 30_000)
    return () => { window.clearTimeout(initial); window.clearInterval(timer) }
  }, [refresh])

  async function action(operation: () => Promise<void>) {
    setBusy(true)
    try { await operation() } catch (error) { notify('error', errorMessage(error)) } finally { setBusy(false) }
  }

  async function startJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    await action(async () => {
      let payload: Record<string, unknown>
      try {
        const parsed = JSON.parse(String(form.get('payload')))
        if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('not an object')
        payload = parsed
      } catch {
        throw new ApiError('Provider payload must be a valid JSON object.')
      }
      payload.cost_estimate_minor = Math.round(Number(form.get('cost_estimate')) * 100)
      payload.job_ceiling_minor = Math.round(Number(form.get('job_ceiling')) * 100)
      const jobId = String(form.get('job_id'))
      await api.startOfferJob({
        job_id: jobId, offer: String(form.get('offer')),
        amount_minor: Math.round(Number(form.get('amount')) * 100), currency: 'NGN', payload,
      })
      await refresh()
      setSelectedJobId(jobId)
      formElement.reset()
      notify('success', 'Offer workflow accepted. The artifact remains human-review gated.')
    })
  }

  async function reviewJob(decision: 'approved' | 'rejected') {
    if (!selectedJob?.artifact) return
    await action(async () => {
      await api.reviewOfferJob(selectedJob.job_id, decision, reviewReason, selectedJob.artifact!.sha256)
      await refresh()
      notify('success', decision === 'approved' ? 'Exact-artifact approval signaled.' : 'Artifact rejection signaled; release is blocked.')
    })
  }

  async function reviewDraft(draftSha256: string, decision: 'approved' | 'rejected') {
    if (!selectedConversation) return
    const draft = selectedConversation.drafts.find(item => item.draft_sha256 === draftSha256)
    if (!draft) return
    await action(async () => {
      await api.reviewConversation(selectedConversation.conversation.conversation_id, draft, decision, draftReason)
      await refresh()
      notify('success', decision === 'approved' ? 'Exact reply approved; sending remains a separate action.' : 'Reply rejected.')
    })
  }

  async function sendDraft(draftSha256: string) {
    if (!selectedConversation) return
    await action(async () => {
      await api.sendConversation(selectedConversation.conversation.conversation_id, draftSha256)
      await refresh()
      notify('success', 'The approved reply was accepted by the configured WhatsApp provider.')
    })
  }

  async function book() {
    if (!selectedConversation || !slotId) return
    await action(async () => {
      await api.bookConversation(selectedConversation.conversation.conversation_id, slotId)
      setSlotId('')
      notify('success', 'Booking hold confirmed and recorded idempotently.')
    })
  }

  async function suppress() {
    if (!selectedConversation) return
    await action(async () => {
      await api.suppressConversation(selectedConversation.conversation.conversation_id, suppressionReason)
      await refresh()
      notify('success', 'Conversation suppressed; subsequent sends and AI processing fail closed.')
    })
  }

  return <div className="page offer-operations-page">
    <section className="page-heading split-heading"><div><p className="section-kicker">Delivery operations</p><h1 className="page-title">Review the artifact.<br />Release only the evidence.</h1><p className="heading-copy">Batch offers reserve prepaid value before execution and bind every review to an exact content digest. Front-desk replies require a separate approval before WhatsApp send.</p></div><div className="heading-actions"><button className="button button-secondary" onClick={() => void refresh()} disabled={!connected || loading}><RefreshCw className={loading ? 'spin' : ''} size={16} /> Refresh operations</button></div></section>
    {feedError && <div className="feed-error" role="alert"><CircleAlert size={17} /><span>Live operations refresh failed; review and release actions must not rely on stale data. {feedError}</span></div>}

    <section className="section-block">
      <div className="section-heading"><div><p className="section-kicker">Offers 03 · 05 · 07 · 08</p><h2>Prepaid batch workflows</h2></div><span className="data-label">Temporal · offers-v1</span></div>
      <div className="workspace-grid">
        <section className="queue-panel"><div className="panel-heading"><h2>Offer jobs</h2><span>{jobs.length} in view</span></div><div className="queue-list">{jobs.map(job => <button key={job.job_id} className={`queue-item ${job.job_id === selectedJob?.job_id ? 'selected' : ''}`} onClick={() => setSelectedJobId(job.job_id)}><span className="queue-item-top"><strong>{job.job_id}</strong><Badge status={job.status} /></span><span>Offer {job.offer} · {job.approval_count} review{job.approval_count === 1 ? '' : 's'}</span><small>{job.artifact ? `Artifact ${job.artifact.sha256.slice(0, 12)}…` : 'Artifact pending'}</small></button>)}</div>{!jobs.length && <div className="empty-review"><p>{connected ? 'No offer jobs are present in this tenant.' : 'Connect an authenticated tenant to load offer workflows.'}</p></div>}</section>
        <section className="review-panel"><div className="review-header"><div><span className="data-label">Exact-artifact review</span><h2>{selectedJob?.job_id ?? 'No job selected'}</h2><p>{selectedJob ? `Offer ${selectedJob.offer} · ${selectedJob.approval_count} recorded` : 'Start a job below or refresh the tenant queue.'}</p></div>{selectedJob && <Badge status={selectedJob.status} />}</div>
          {selectedJob && <><div className="release-facts"><div className="fact"><span>Prepaid reservation</span><strong>{new Intl.NumberFormat('en-NG', { style: 'currency', currency: selectedJob.currency }).format(selectedJob.amount_minor / 100)}</strong></div><div className="fact"><span>Required reviewers</span><strong>{selectedJob.offer === '08' ? '2 distinct analysts' : '1 reviewer'}</strong></div><div className="fact"><span>Object version</span><strong>{selectedJob.artifact ? `v${selectedJob.artifact.version}` : 'Pending'}</strong></div><div className="fact"><span>Release state</span><strong>{selectedJob.artifact?.status ?? 'Not created'}</strong></div></div><label className="field"><span>Current artifact SHA-256</span><input value={selectedJob.artifact?.sha256 ?? 'Artifact not yet available'} readOnly /></label><label className="field"><span>Review reason</span><textarea rows={3} value={reviewReason} onChange={event => setReviewReason(event.target.value)} /></label><div className="safety-actions"><button className="button button-primary" onClick={() => void reviewJob('approved')} disabled={!connected || busy || Boolean(feedError) || selectedJob.status !== 'awaiting-review' || !selectedJob.artifact || !reviewReason.trim()}><Check size={16} /> Approve exact artifact</button><button className="button button-danger-quiet" onClick={() => void reviewJob('rejected')} disabled={!connected || busy || Boolean(feedError) || selectedJob.status !== 'awaiting-review' || !selectedJob.artifact || !reviewReason.trim()}><Ban size={16} /> Reject artifact</button></div></>}
        </section>
      </div>
      <form onSubmit={startJob} className="approval-form"><div className="form-grid"><label className="field"><span>Offer</span><select name="offer" defaultValue="08" required><option value="03">03 · Call scorecard</option><option value="05">05 · Accounting export</option><option value="07">07 · Compliance paperwork</option><option value="08">08 · Funding pack</option></select></label><label className="field"><span>Job ID</span><input name="job_id" placeholder="funding-oct-01" pattern="[a-zA-Z0-9][a-zA-Z0-9._:-]*" required /></label></div><div className="form-grid"><label className="field"><span>Prepaid amount (NGN)</span><input name="amount" type="number" min="0.01" step="0.01" required /></label><label className="field"><span>Provider estimate (NGN)</span><input name="cost_estimate" type="number" min="0.01" step="0.01" required /></label><label className="field"><span>Job AI ceiling (NGN)</span><input name="job_ceiling" type="number" min="0.01" step="0.01" required /></label></div><label className="field"><span>Provider payload (JSON object; budget fields are added above)</span><textarea name="payload" rows={5} defaultValue="{}" required /></label><p className="body-copy">The worker validates offer-specific source keys, model versions, evidence, and registry fields. The API never accepts a tenant ID from this form.</p><button className="button button-primary" disabled={!connected || busy}>{busy ? <Loader2 className="spin" size={16} /> : <Check size={16} />} Start approval-gated offer job</button></form>
    </section>

    <section className="section-block">
      <div className="section-heading"><div><p className="section-kicker">Offer 04 · AI Front Desk</p><h2>Conversation review and booking</h2></div><span className="data-label">Signed intake · redacted AI · approved send</span></div>
      {!conversationsConnected && <div className="connection-note"><CircleAlert size={17} /><span>Set conversationsApiUrl to enable the live front-desk queue. No preview conversation data is substituted.</span></div>}
      <div className="workspace-grid">
        <section className="queue-panel"><div className="panel-heading"><h2>Conversations</h2><span>{conversations.length} in view</span></div><div className="queue-list">{conversations.map(item => <button key={item.conversation.conversation_id} className={`queue-item ${item.conversation.conversation_id === selectedConversation?.conversation.conversation_id ? 'selected' : ''}`} onClick={() => setSelectedConversationId(item.conversation.conversation_id)}><span className="queue-item-top"><strong>{item.conversation.contact_name || 'WhatsApp customer'}</strong><Badge status={item.conversation.status} /></span><span>{item.conversation.conversation_id}</span><small>{item.drafts.length} draft{item.drafts.length === 1 ? '' : 's'} · tenant-scoped</small></button>)}</div></section>
        <section className="review-panel"><div className="review-header"><div><span className="data-label">Human front-desk controls</span><h2>{selectedConversation?.conversation.contact_name || 'No conversation selected'}</h2><p>{selectedConversation?.conversation.conversation_id ?? 'Signed WhatsApp webhooks populate this queue.'}</p></div>{selectedConversation && <Badge status={selectedConversation.conversation.status} />}</div>
          {selectedConversation && <><label className="field"><span>Draft review reason</span><textarea rows={2} value={draftReason} onChange={event => setDraftReason(event.target.value)} /></label>{selectedConversation.drafts.map(draft => <article className="confirmation-block" key={draft.draft_sha256}><span>Response</span><strong>{draft.response_text}</strong><span>Confidence / model</span><strong>{Math.round(draft.confidence * 100)}% · {draft.model_version}</strong><span>Escalation</span><strong>{draft.escalation_reason ?? 'None'}</strong><span>Digest</span><strong>{draft.draft_sha256}</strong><div className="safety-actions"><Badge status={draft.status} /><button className="button button-secondary" onClick={() => void reviewDraft(draft.draft_sha256, 'approved')} disabled={!conversationsConnected || busy || Boolean(feedError) || draft.status !== 'requires-review' || !draftReason.trim()}><Check size={15} /> Approve</button><button className="button button-danger-quiet" onClick={() => void reviewDraft(draft.draft_sha256, 'rejected')} disabled={!conversationsConnected || busy || Boolean(feedError) || draft.status !== 'requires-review' || !draftReason.trim()}><Ban size={15} /> Reject</button><button className="button button-primary" onClick={() => void sendDraft(draft.draft_sha256)} disabled={!conversationsConnected || busy || Boolean(feedError) || draft.status !== 'approved' || selectedConversation.conversation.status !== 'active'}><Send size={15} /> Send approved reply</button></div></article>)}
            <div className="form-grid"><label className="field"><span>Booking slot ID</span><input value={slotId} onChange={event => setSlotId(event.target.value)} placeholder="slot-consult-01" /></label><label className="field"><span>Suppression reason</span><input value={suppressionReason} onChange={event => setSuppressionReason(event.target.value)} /></label></div><div className="safety-actions"><button className="button button-secondary" onClick={() => void book()} disabled={!conversationsConnected || busy || !slotId.trim() || selectedConversation.conversation.status !== 'active'}><CalendarCheck size={16} /> Hold and confirm booking</button><button className="button button-danger-quiet" onClick={() => void suppress()} disabled={!conversationsConnected || busy || !suppressionReason.trim() || selectedConversation.conversation.status !== 'active'}><ShieldCheck size={16} /> Suppress conversation</button></div></>}
        </section>
      </div>
    </section>
    <div className="connection-note"><MessageSquareText size={17} /><span>Source controls are verified locally. Actual Temporal execution, WhatsApp delivery, AI generation, booking, registry access, and object release still require their configured staging providers.</span></div>
  </div>
}
