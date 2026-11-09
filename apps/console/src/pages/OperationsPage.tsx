import { Fragment, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react'
import { useSession } from '../state/session-context'
import { useOperations } from '../state/operations-context'
import { byRecent, KIND_LABEL, type WorkItem, type WorkKind, type WorkState } from '../lib/workflows'
import { Empty, Note, OfferMark, PageHead, Panel, SearchBox, Segmented, StageSteps, StageTrack, When, WorkStatus } from '../components/ui'
import { dateTime, money } from '../lib/format'

const PREFIX: Record<WorkKind, string> = { campaign: 'campaign', studio: 'studio', 'offer-job': 'offer', engagement: 'engagement' }
type StateFilter = 'all' | WorkState
type KindFilter = 'all' | WorkKind

function duration(item: WorkItem): string {
  const start = new Date(item.startedAt).getTime(); const end = new Date(item.updatedAt).getTime()
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '—'
  const minutes = Math.round((end - start) / 60000)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.round(minutes / 60)
  return hours < 48 ? `${hours} h` : `${Math.round(hours / 24)} days`
}

/** Every workflow in this business, in plain words, with the technical record one click away. */
export function OperationsPage() {
  const { tenant, offerName } = useSession()
  const { items, feeds, refresh, refreshing, refreshedAt } = useOperations()
  const [params, setParams] = useSearchParams()
  const state = (params.get('state') ?? 'all') as StateFilter
  const kind = (params.get('kind') ?? 'all') as KindFilter
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const set = (key: string, value: string | null) => setParams(current => { const next = new URLSearchParams(current); if (value) next.set(key, value); else next.delete(key); return next }, { replace: true })

  const inKind = useMemo(() => items.filter(item => kind === 'all' || item.kind === kind), [items, kind])
  const shown = byRecent(inKind.filter(item => (state === 'all' || item.state === state)
    && (!query || `${item.title} ${item.id} ${item.status}`.toLowerCase().includes(query.toLowerCase()))))
  const count = (value: WorkState) => inKind.filter(item => item.state === value).length
  const failing = (Object.keys(feeds) as WorkKind[]).filter(k => feeds[k].load === 'error')

  return <div className="page">
    <PageHead eyebrow="Work" title="Operations" description="Every automated workflow in this business: where it is, how long it has run, and why it stopped if it did."
      actions={<button className="button button-secondary" onClick={() => void refresh()} disabled={refreshing}><RefreshCw size={14} className={refreshing ? 'spin' : ''} /> Refresh</button>} />
    {failing.map(k => <Note key={k} tone="failed">{KIND_LABEL[k]} list didn't load: {feeds[k].error} {feeds[k].items.length ? 'Showing the last list that did.' : ''}</Note>)}
    <Panel flush>
      <div className="toolbar">
        <Segmented label="State" value={state} onChange={value => set('state', value === 'all' ? null : value)} options={[
          { id: 'all', label: 'All', count: inKind.length }, { id: 'needs-review', label: 'Needs a person', count: count('needs-review') },
          { id: 'running', label: 'Running', count: count('running') }, { id: 'failed', label: 'Failed', count: count('failed') },
          { id: 'stopped', label: 'Stopped', count: count('stopped') }, { id: 'done', label: 'Done', count: count('done') }]} />
        <Segmented label="Type" value={kind} onChange={value => set('kind', value === 'all' ? null : value)} options={[
          { id: 'all', label: 'All types' }, ...(['engagement', 'offer-job', 'studio', 'campaign'] as WorkKind[]).map(k => ({ id: k, label: `${KIND_LABEL[k]}s` }))]} />
        <SearchBox value={query} onChange={setQuery} placeholder="Search name, id or status" />
        <span className="small muted" style={{ marginLeft: 'auto' }}>{refreshedAt ? `Updated ${refreshedAt.toLocaleTimeString('en-NG')}` : 'Loading'}</span>
      </div>
      {shown.length === 0 ? <Empty title="No workflows match" center>Change the filters, or start work from a service page.</Empty>
        : <div className="table-wrap"><table className="data">
          <thead><tr><th style={{ width: 28 }} /><th>Workflow</th><th>Type</th><th>Offer</th><th>Status</th><th>Progress</th><th className="right">Ran for</th><th className="right">Last activity</th></tr></thead>
          <tbody>{shown.map(item => {
            const key = `${item.kind}:${item.id}`
            const expanded = open === key
            return <Fragment key={key}>
              <tr>
                <td><button className="icon-button" style={{ width: 24, height: 24 }} onClick={() => setOpen(expanded ? null : key)} aria-expanded={expanded} aria-label={`${expanded ? 'Hide' : 'Show'} details for ${item.title}`}>{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button></td>
                <td><Link to={item.href} className="title">{item.title}</Link>{item.error && <span className="sub" style={{ color: 'var(--failed)' }}>{item.stalledAt ? `Stopped while ${item.stalledAt.replaceAll('-', ' ').replaceAll('_', ' ')}` : item.error}</span>}</td>
                <td>{KIND_LABEL[item.kind]}</td>
                <td><OfferMark id={item.offer} name={offerName(item.offer)} compact /></td>
                <td><WorkStatus item={item} /></td>
                <td><StageTrack item={item} /></td>
                <td className="right num">{duration(item)}</td>
                <td className="right"><When value={item.updatedAt} /></td>
              </tr>
              {expanded && <tr className="diag"><td /><td colSpan={7}>
                <div className="stack" style={{ padding: '6px 0' }}>
                  <StageSteps item={item} />
                  <dl className="props">
                    <dt>Workflow id</dt><dd className="mono">{PREFIX[item.kind]}:{tenant?.id ?? '…'}:{item.id}</dd>
                    <dt>Raw status</dt><dd className="mono">{item.status}{item.stalledAt ? ` (stalled at ${item.stalledAt})` : ''}</dd>
                    {item.error && <><dt>Error</dt><dd>{item.error}</dd></>}
                    <dt>Started</dt><dd>{dateTime(item.startedAt)}</dd>
                    <dt>Last activity</dt><dd>{dateTime(item.updatedAt)}</dd>
                    {item.amountMinor !== null && <><dt>Amount</dt><dd className="num">{money(item.amountMinor, item.currency)}</dd></>}
                  </dl>
                  <p className="small muted">Retries happen inside the workflow engine and aren't reported here. A failed workflow is not restarted automatically; start the work again from its service page.</p>
                </div>
              </td></tr>}
            </Fragment>
          })}</tbody>
        </table></div>}
    </Panel>
  </div>
}
