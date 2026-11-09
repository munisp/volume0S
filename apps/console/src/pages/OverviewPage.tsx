import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { RefreshCw } from 'lucide-react'
import { useSession } from '../state/session-context'
import { useOperations } from '../state/operations-context'
import { byRecent, byWaiting, countBy, KIND_LABEL, type WorkKind, type WorkState } from '../lib/workflows'
import { allowed } from '../lib/access'
import type { Balance } from '../lib/types'
import { Empty, Figure, Note, OfferMark, PageHead, Panel, StageTrack, Status, When, WorkStatus } from '../components/ui'
import { money } from '../lib/format'
import { ApiError } from '../lib/api'

const KINDS: WorkKind[] = ['engagement', 'offer-job', 'studio', 'campaign']
const STATES: WorkState[] = ['needs-review', 'running', 'failed', 'stopped', 'done']
const STATE_HEAD: Record<WorkState, string> = { 'needs-review': 'Needs a person', running: 'Running', failed: 'Failed', stopped: 'Stopped', done: 'Done' }
const STATE_LABEL: Record<string, string> = { prospect: 'Prospect', onboarding: 'Onboarding', active: 'Active', paused: 'Paused', offboarded: 'Offboarded' }

export function OverviewPage() {
  const { tenant, roles, api, offerName } = useSession()
  const { feeds, items, refresh, refreshing, refreshedAt } = useOperations()
  const navigate = useNavigate()
  const [balance, setBalance] = useState<{ value: Balance | null; note: string | null }>({ value: null, note: null })

  useEffect(() => {
    let active = true
    api.balance('NGN').then(
      value => active && setBalance({ value, note: null }),
      error => active && setBalance({ value: null, note: error instanceof ApiError && error.status === 503 ? 'Billing isn\'t running on this deployment' : (error as Error).message }),
    )
    return () => { active = false }
  }, [api])

  const waiting = byWaiting(items.filter(item => item.state === 'needs-review'))
  const failed = byRecent(items.filter(item => item.state === 'failed'))
  const counts = countBy(items)
  const failingFeeds = KINDS.filter(kind => feeds[kind].load === 'error')
  const canReview = allowed(roles, 'reviewEngagement') || allowed(roles, 'approveCampaign')
  const unknown = (kind: WorkKind) => feeds[kind].load !== 'ok'

  return <div className="page">
    <PageHead eyebrow={tenant ? `${tenant.name} · ${STATE_LABEL[tenant.state] ?? tenant.state}` : undefined} title="Overview"
      description="What is happening in this business right now, and what is waiting on a person."
      actions={<button className="button button-secondary" onClick={() => void refresh()} disabled={refreshing}><RefreshCw size={14} className={refreshing ? 'spin' : ''} /> Refresh</button>} />

    <div className="figures" aria-label="At a glance">
      <Figure label="Waiting on a person" value={waiting.length} tone={waiting.length ? 'attention' : undefined} hint={failingFeeds.length ? 'Some queues failed to load' : 'Across all queues'} />
      <Figure label="Running" value={items.filter(item => item.state === 'running').length} hint="In progress, no action needed" />
      <Figure label="Failed" value={failed.length} tone={failed.length ? 'failed' : undefined} hint="Ended without finishing" />
      <Figure label="Services subscribed" value={tenant?.subscriptions?.length ?? 0} hint={`${tenant?.workspaces?.length ?? 0} delivery workspace${tenant?.workspaces?.length === 1 ? '' : 's'}`} />
      {balance.value
        ? <Figure label="Available prepaid (NGN)" value={balance.value.available_minor === null ? '—' : money(balance.value.available_minor)} hint="From the ledger" />
        : <Figure label="Available prepaid" value="Unavailable" tone="unknown" hint={balance.note ?? 'Loading'} />}
    </div>

    {failingFeeds.length > 0 && <Note tone="failed">
      {failingFeeds.map(kind => `${KIND_LABEL[kind]}s: ${feeds[kind].error}`).join(' · ')} Counts above leave these out, so they may be higher.
    </Note>}

    <div className="split">
      <Panel title="Needs a person" meta={canReview ? 'Oldest first. Nothing here is sent, charged or delivered until someone decides.' : 'You can see these; reviewing needs the Delivery or Analyst role.'}
        actions={waiting.length > 0 && canReview ? <Link className="button button-secondary small" to="/approvals">Open approvals</Link> : undefined} flush>
        {waiting.length === 0
          ? <Empty title="Nothing is waiting on a person">{failingFeeds.length ? 'Not every queue loaded, so this may be incomplete.' : 'Every queue that loaded is clear.'}</Empty>
          : <div className="table-wrap"><table className="data stack-sm"><thead><tr><th>Item</th><th>Type</th><th>Offer</th><th>Waiting</th><th className="right">Amount</th></tr></thead><tbody>
            {waiting.slice(0, 8).map(item => <tr key={`${item.kind}:${item.id}`} className="link" onClick={() => navigate(item.href)}>
              <td><Link to={item.href} className="title">{item.title}</Link><span className="sub">{item.label}</span></td>
              <td data-label="Type">{KIND_LABEL[item.kind]}</td>
              <td data-label="Offer"><OfferMark id={item.offer} name={offerName(item.offer)} compact /></td>
              <td data-label="Waiting"><When value={item.updatedAt} /></td>
              <td data-label="Amount" className="right num">{item.amountMinor === null ? '—' : money(item.amountMinor, item.currency)}</td>
            </tr>)}
          </tbody></table></div>}
      </Panel>

      <Panel title="Failed work" meta="Ended without finishing. Any reserved money is released by the workflow." flush>
        {failed.length === 0 ? <Empty title="No failed work" /> : <div className="table-wrap"><table className="data"><tbody>
          {failed.slice(0, 6).map(item => <tr key={`${item.kind}:${item.id}`} className="link" onClick={() => navigate(item.href)}>
            <td><Link to={item.href} className="title">{item.title}</Link><span className="sub">{KIND_LABEL[item.kind]}{item.stalledAt ? ` · stopped while ${item.stalledAt.replaceAll('-', ' ').replaceAll('_', ' ')}` : ''}</span></td>
            <td className="right"><When value={item.updatedAt} /></td>
          </tr>)}
        </tbody></table>{failed.length > 6 && <div className="panel-body"><Link to="/operations?state=failed">All {failed.length} failed</Link></div>}</div>}
      </Panel>
    </div>

    <Panel title="Work in progress" meta={refreshedAt ? `From the workflow engine · updated ${refreshedAt.toLocaleTimeString('en-NG')}` : 'Loading from the workflow engine'} flush>
      <div className="table-wrap"><table className="data"><thead><tr><th>Type</th>{STATES.map(state => <th key={state} className="right">{STATE_HEAD[state]}</th>)}<th /></tr></thead><tbody>
        {KINDS.map(kind => <tr key={kind}>
          <td className="title">{KIND_LABEL[kind]}s</td>
          {STATES.map(state => <td key={state} className="right num">{unknown(kind) ? <span className="muted" title={feeds[kind].error ?? 'Loading'}>—</span> : counts[kind][state]}</td>)}
          <td className="right">{feeds[kind].load === 'error' ? <Status state="failed" label="Couldn't load" /> : <Link to={`/operations?kind=${kind}`}>View</Link>}</td>
        </tr>)}
      </tbody></table></div>
    </Panel>

    <div className="grid-2">
      <Panel title="Recent activity" meta="Latest change on each piece of work" flush>
        {items.length === 0 ? <Empty title="No activity yet" /> : <div className="table-wrap"><table className="data"><tbody>
          {byRecent(items).slice(0, 8).map(item => <tr key={`${item.kind}:${item.id}`} className="link" onClick={() => navigate(item.href)}>
            <td><span className="title">{item.title}</span><span className="sub">{KIND_LABEL[item.kind]}</span></td>
            <td><StageTrack item={item} /></td>
            <td><WorkStatus item={item} /></td>
            <td className="right muted"><When value={item.updatedAt} /></td>
          </tr>)}
        </tbody></table></div>}
      </Panel>
      <Panel title="Across all clients">
        <div className="stack">
          <p className="body-copy">This sign-in is scoped to one business{tenant ? <> (<strong>{tenant.name}</strong>)</> : null}. Agency-wide totals such as active clients, revenue and tenant growth need a cross-tenant API, which VolumeOS doesn't have yet, so none are shown rather than estimated.</p>
          <Note>Payments, sign-ups and approvals by people aren't exposed as an event history either; activity here comes from workflow updates.</Note>
        </div>
      </Panel>
    </div>
  </div>
}
