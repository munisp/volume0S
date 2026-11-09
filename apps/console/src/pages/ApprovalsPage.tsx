import { useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { useSession } from '../state/session-context'
import { useOperations } from '../state/operations-context'
import { byRecent, byWaiting, KIND_LABEL, type WorkItem, type WorkKind } from '../lib/workflows'
import { Empty, Note, OfferMark, PageHead, Panel, SearchBox, Segmented, StageTrack, When, WorkStatus } from '../components/ui'
import { money } from '../lib/format'

type Tab = 'pending' | 'decided'
type KindFilter = 'all' | WorkKind

/** What a reviewer is deciding, per kind of work: approval is a decision, never a delivery. */
const DECISION: Record<WorkKind, string> = {
  engagement: 'Approve or reject the exact plan (bound to its SHA-256)',
  'offer-job': 'Approve or reject the exact artifact before release',
  studio: 'QA the generated images before conversion and delivery',
  campaign: 'Approve the campaign before funds are reserved and it is sent',
}

/** Statuses a piece of work can only reach after a person decided on it (or the decision window ran out). */
const AFTER_DECISION: Record<WorkKind, Set<string>> = {
  engagement: new Set(['approved', 'rejected', 'approval-expired']),
  'offer-job': new Set(['releasing', 'released', 'rejected']),
  studio: new Set(['approved', 'delivering', 'delivered', 'rejecting', 'rejected', 'qa_timed_out']),
  campaign: new Set(['approved', 'reserving', 'dispatching', 'settling', 'completed', 'approval_timed_out']),
}

export function ApprovalsPage() {
  const { offerName } = useSession()
  const { items, feeds } = useOperations()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') === 'decided' ? 'decided' : 'pending') as Tab
  const kind = (params.get('kind') ?? 'all') as KindFilter
  const [query, setQuery] = useState('')

  const pending = useMemo(() => byWaiting(items.filter(item => item.state === 'needs-review')), [items])
  const decided = useMemo(() => byRecent(items.filter(item => AFTER_DECISION[item.kind].has(item.status))), [items])
  const source = tab === 'pending' ? pending : decided
  const shown = source.filter(item => (kind === 'all' || item.kind === kind)
    && (!query || `${item.title} ${item.id} ${item.offer ?? ''}`.toLowerCase().includes(query.toLowerCase())))
  const failing = (Object.keys(feeds) as WorkKind[]).filter(k => feeds[k].load === 'error')
  const set = (key: string, value: string | null) => setParams(current => { const next = new URLSearchParams(current); if (value) next.set(key, value); else next.delete(key); return next }, { replace: true })
  const kinds: WorkKind[] = ['engagement', 'offer-job', 'studio', 'campaign']

  return <div className="page">
    <PageHead eyebrow="Work" title="Approvals"
      description="Everything automation has prepared that a person must decide on. Approving lets the work continue to its next stage; it is not delivery." />
    {failing.length > 0 && <Note tone="failed">Not shown because their queue didn't load: {failing.map(k => `${KIND_LABEL[k].toLowerCase()}s (${feeds[k].error})`).join('; ')}.</Note>}
    <Panel flush>
      <div className="toolbar">
        <Segmented label="Queue" value={tab} onChange={value => set('tab', value === 'pending' ? null : value)}
          options={[{ id: 'pending', label: 'Waiting', count: pending.length }, { id: 'decided', label: 'Decided', count: decided.length }]} />
        <Segmented label="Type" value={kind} onChange={value => set('kind', value === 'all' ? null : value)}
          options={[{ id: 'all', label: 'All' }, ...kinds.map(k => ({ id: k, label: `${KIND_LABEL[k]}s`, count: source.filter(item => item.kind === k).length }))]} />
        <SearchBox value={query} onChange={setQuery} placeholder="Search by name, id or offer" />
      </div>
      {shown.length === 0
        ? <Empty title={tab === 'pending' ? 'Nothing is waiting for a decision' : 'No decisions yet'} center>
            {tab === 'pending' ? 'New items appear here as soon as their preparation finishes.' : 'Approved, rejected and finished work appears here.'}
          </Empty>
        : <div className="table-wrap"><table className="data stack-sm">
          <thead><tr><th>Item</th><th>Type</th><th>Offer</th><th>{tab === 'pending' ? 'Decision needed' : 'Outcome'}</th><th>Progress</th><th className="right">Amount</th><th className="right">{tab === 'pending' ? 'Waiting' : 'Updated'}</th></tr></thead>
          <tbody>{shown.map((item: WorkItem) => <tr key={`${item.kind}:${item.id}`} className="link" onClick={() => navigate(item.href)}>
            <td><Link to={item.href} className="title">{item.title}</Link><span className="sub mono">{item.id}</span></td>
            <td data-label="Type">{KIND_LABEL[item.kind]}</td>
            <td data-label="Offer"><OfferMark id={item.offer} name={offerName(item.offer)} /></td>
            <td data-label={tab === 'pending' ? 'Decision' : 'Outcome'}>{tab === 'pending' ? <span className="small">{DECISION[item.kind]}</span> : <WorkStatus item={item} />}</td>
            <td data-label="Progress"><StageTrack item={item} /></td>
            <td data-label="Amount" className="right num">{item.amountMinor === null ? '—' : money(item.amountMinor, item.currency)}</td>
            <td data-label={tab === 'pending' ? 'Waiting' : 'Updated'} className="right"><When value={item.updatedAt} /></td>
          </tr>)}</tbody>
        </table></div>}
    </Panel>
    <p className="small muted">Assignment to a named reviewer and "request changes" aren't supported by the workflows yet: any person with the right role can decide, and a rejection ends that piece of work.</p>
  </div>
}
