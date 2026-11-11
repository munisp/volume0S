import { useEffect, useState, type FormEvent } from 'react'
import { Search } from 'lucide-react'
import { useSession, errorText } from '../state/session-context'
import { Empty, Figure, Note, Panel } from '../components/ui'
import type { ActivityRow, InsightMonth } from '../lib/types'

const naira = (minor: number) => `₦${(minor / 100).toLocaleString('en-NG', { maximumFractionDigits: 0 })}`
const monthLabel = (month: string) => new Date(`${month}-01T00:00:00`).toLocaleDateString('en-GB', { month: 'short', year: '2-digit' })

/** One series of monthly bars (one hue, no legend: the title names it), with a tooltip per bar and a table view. */
function MonthlyBars({ title, months, value, format }: { title: string; months: InsightMonth[]; value: (m: InsightMonth) => number; format: (n: number) => string }) {
  const [table, setTable] = useState(false)
  const rows = [...months].reverse() // oldest → newest, left → right
  const max = Math.max(1, ...rows.map(value))
  const [hover, setHover] = useState<number | null>(null)
  return <Panel title={title} actions={<button className="text-button small" onClick={() => setTable(current => !current)}>{table ? 'Show chart' : 'Show table'}</button>}>
    <div className="panel-body">
      {table
        ? <table className="data"><thead><tr><th>Month</th><th className="right">{title}</th></tr></thead><tbody>
            {rows.map(m => <tr key={m.month}><td>{monthLabel(m.month)}</td><td className="right">{format(value(m))}</td></tr>)}</tbody></table>
        : <div className="bars" role="img" aria-label={`${title} per month: ${rows.map(m => `${monthLabel(m.month)} ${format(value(m))}`).join(', ')}`}>
            {rows.map((m, index) => {
              const v = value(m)
              return <div key={m.month} className="bar-slot" onMouseEnter={() => setHover(index)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(index)} onBlur={() => setHover(null)} tabIndex={0}>
                {hover === index && <div className="bar-tip">{monthLabel(m.month)}<strong>{format(v)}</strong></div>}
                <div className="bar" style={{ height: `${Math.max(v ? 3 : 0, (v / max) * 100)}%` }} />
                <span className="bar-label">{monthLabel(m.month)}</span>
              </div>
            })}
          </div>}
    </div>
  </Panel>
}

/** Intelligence: the business's own figures, summarised every 10 minutes by the insights indexer. */
export function Intelligence() {
  const { api, tenant } = useSession()
  const [months, setMonths] = useState<InsightMonth[] | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [activity, setActivity] = useState<{ rows: ActivityRow[]; error: string | null; searched: string } | null>(null)
  const tenantId = tenant?.id ?? ''

  useEffect(() => {
    if (!tenantId) return
    let active = true
    api.insights(tenantId, 6).then(result => { if (active) { setMonths(result.months); setFailure(null) } }, error => { if (active) setFailure(errorText(error) || 'the figures service didn\'t answer') })
    return () => { active = false }
  }, [api, tenantId])

  function search(event: FormEvent) {
    event.preventDefault()
    const text = query.trim()
    if (!text || !tenantId) return
    const end = new Date(); const start = new Date(end.getTime() - 90 * 86400000)
    api.searchActivity(tenantId, text, start.toISOString().slice(0, 10), end.toISOString().slice(0, 10)).then(
      result => setActivity({ rows: result.rows, error: null, searched: text }),
      error => setActivity({ rows: [], error: errorText(error) || 'Search failed. Try again in a minute.', searched: text }),
    )
  }

  if (failure) return <Note tone="failed">The figures couldn't be loaded: {failure}</Note>
  if (!months) return <Empty title="Loading figures" />
  const current = months[0]
  const m = current?.metrics
  return <div className="stack">
    {!current
      ? <Empty title="No activity recorded yet">Figures appear here within 10 minutes of the first top-up, invoice, campaign or Studio pack.</Empty>
      : <>
        <p className="small muted">{current.title} · updated {current.updated_at ? new Date(current.updated_at).toLocaleString('en-GB') : '—'}</p>
        <div className="figures">
          <Figure label="Paid in" value={naira(m.paid_in_minor)} hint={`${m.topups ?? 0} top-up${m.topups === 1 ? '' : 's'}`} />
          <Figure label="Charged" value={naira(m.charged_minor)} hint={`${m.invoices} invoice${m.invoices === 1 ? '' : 's'}${m.campaign_spend_minor ? ` · ${naira(m.campaign_spend_minor)} campaigns` : ''}`} />
          <Figure label="People reached" value={(m.emails + m.whatsapp).toLocaleString()} hint={`${m.emails} email · ${m.whatsapp} WhatsApp · ${m.social_posts} social posts`} />
          <Figure label="Studio images" value={m.studio_images.toLocaleString()} hint={`${m.studio_packs} packs delivered`} />
        </div>
        <p className="body-copy">{current.summary}</p>
        <div className="chart-pair">
          <MonthlyBars title="Charged" months={months} value={row => row.metrics.charged_minor} format={naira} />
          <MonthlyBars title="People reached" months={months} value={row => row.metrics.emails + row.metrics.whatsapp} format={n => n.toLocaleString()} />
        </div>
      </>}
    <Panel title="Activity">
      <div className="panel-body stack">
        <form className="row field" style={{ gap: 8, flexDirection: 'row', alignItems: 'center' }} onSubmit={search}>
          <input aria-label="Search activity" placeholder="Search the last 90 days, e.g. invoice, campaign, top-up, Studio" value={query} onChange={event => setQuery(event.target.value)} style={{ flex: 1, minWidth: 0 }} maxLength={256} />
          <button className="button button-secondary small" disabled={!query.trim()}><Search size={14} /> Search</button>
        </form>
        {activity?.error && <Note tone="failed">{activity.error}</Note>}
        {activity && !activity.error && (activity.rows.length
          ? <table className="data stack-sm"><thead><tr><th>Date</th><th>What happened</th></tr></thead><tbody>
              {activity.rows.map(row => <tr key={row.trace_id}><td data-label="Date">{row.event_date}</td><td>{row.message}</td></tr>)}</tbody></table>
          : <p className="small muted">Nothing matched “{activity.searched}” in the last 90 days.</p>)}
      </div>
    </Panel>
  </div>
}
