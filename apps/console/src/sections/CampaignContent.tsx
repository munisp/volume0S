import { useEffect, useRef, useState } from 'react'
import { Trash2, Upload } from 'lucide-react'
import { useSession, errorText } from '../state/session-context'
import { allowed } from '../lib/access'
import { parseContactsCsv, type ParsedContacts } from '../lib/contacts'
import type { CampaignContact, CampaignDraft, CampaignResult } from '../lib/types'
import { Confirm, Empty, Note, Panel, RoleNote } from '../components/ui'

const STATUS_LABEL: Record<CampaignResult['status'], string> = {
  sent: 'Sent', test_address: 'Test address (not sent)', blocked: 'Not sent: suppressed or paused', failed: 'Failed',
}

/** What the approver is approving: the exact AI draft, then who it reached. Polls while the draft is being written. */
export function CampaignDraftPanel({ campaignId, status }: { campaignId: string; status: string }) {
  const { api } = useSession()
  const [draft, setDraft] = useState<CampaignDraft | null>(null)
  const [results, setResults] = useState<CampaignResult[]>([])
  const [failure, setFailure] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let active = true
    api.campaignDetail(campaignId).then(
      result => { if (active) { setDraft(result.draft); setResults(result.results); setFailure(null) } },
      error => { if (active) setFailure(errorText(error)) },
    )
    return () => { active = false }
  }, [api, campaignId, status, tick])

  // The local model takes ~15–60 s; keep looking until the draft exists.
  useEffect(() => {
    if (draft || status !== 'drafting') return
    const timer = window.setInterval(() => setTick(current => current + 1), 5000)
    return () => window.clearInterval(timer)
  }, [draft, status])

  if (failure) return <Note tone="failed">The draft couldn't be loaded: {failure}</Note>
  if (!draft) return status === 'drafting' || status === 'running'
    ? <Note>The AI is writing the draft (about a minute). It appears here for review before anything is sent.</Note>
    : null
  return <div className="stack" aria-label="Campaign draft">
    <div className="draft-preview">
      <span className="eyebrow">Email</span>
      <strong>{draft.subject}</strong>
      <p style={{ whiteSpace: 'pre-wrap' }}>{draft.email_body}</p>
    </div>
    <div className="draft-preview">
      <span className="eyebrow">WhatsApp</span>
      <p style={{ whiteSpace: 'pre-wrap' }}>{draft.whatsapp_text}</p>
    </div>
    <p className="small muted">{'{{first_name}}'} becomes each customer's first name. Written by {draft.model} on VolumeOS's own servers; customer details are never sent to it.</p>
    {results.length > 0 && <table className="data"><thead><tr><th>Channel</th><th>Result</th><th className="right">People</th></tr></thead><tbody>
      {results.map(row => <tr key={`${row.cycle_id}-${row.channel}-${row.status}`}><td style={{ textTransform: 'capitalize' }}>{row.channel}</td><td>{STATUS_LABEL[row.status]}</td><td className="right">{row.count}</td></tr>)}
    </tbody></table>}
  </div>
}

/** The business's customer list for campaigns. Consent is explicit: from the file's columns or the operator's confirmation. */
export function ContactsPanel() {
  const { api, roles, notify } = useSession()
  const [list, setList] = useState<{ contacts: CampaignContact[]; total: number; reachable: { email?: number; whatsapp?: number } } | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const [pending, setPending] = useState<ParsedContacts & { name: string; text: string } | null>(null)
  const [consentForAll, setConsentForAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const [removing, setRemoving] = useState<CampaignContact | null>(null)
  const file = useRef<HTMLInputElement>(null)
  const canManage = allowed(roles, 'manageContacts')

  useEffect(() => {
    if (!canManage) return
    let active = true
    api.contacts(50, 0).then(
      result => { if (active) { setList(result); setFailure(null) } },
      error => { if (active) setFailure(errorText(error)) },
    )
    return () => { active = false }
  }, [api, version, canManage])

  async function choose(event: React.ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0]
    event.target.value = ''
    if (!chosen) return
    try {
      const text = await chosen.text()
      setConsentForAll(false)
      setPending({ ...parseContactsCsv(text, false), name: chosen.name, text })
    } catch (error) { notify('error', errorText(error)) }
  }

  const preview = pending ? parseContactsCsv(pending.text, consentForAll) : null
  const reachable = preview ? preview.rows.filter(row => row.email_consent || row.whatsapp_consent).length : 0

  async function upload() {
    if (!preview) return
    setBusy(true)
    try {
      const result = await api.importContacts(preview.rows)
      notify('success', `${result.added} added, ${result.updated} updated.`)
      setPending(null); setVersion(current => current + 1)
    } catch (error) { notify('error', errorText(error)) } finally { setBusy(false) }
  }

  async function remove(contact: CampaignContact) {
    setBusy(true)
    try { await api.deleteContact(contact.id); setRemoving(null); setVersion(current => current + 1) }
    catch (error) { notify('error', errorText(error)) } finally { setBusy(false) }
  }

  if (!canManage) return <Panel title="Contacts"><div className="panel-body"><RoleNote action="manageContacts" /></div></Panel>
  return <Panel title="Contacts" meta={list ? `${list.total} · ${list.reachable.email ?? 0} by email · ${list.reachable.whatsapp ?? 0} by WhatsApp` : undefined}
    actions={<><input ref={file} type="file" accept=".csv,text/csv" hidden onChange={event => void choose(event)} />
      <button className="button button-secondary small" onClick={() => file.current?.click()}><Upload size={14} /> Import CSV</button></>} flush>
    {failure && <div className="panel-body"><Note tone="failed">Contacts couldn't be loaded: {failure}</Note></div>}
    {list === null && !failure ? <Empty title="Loading contacts" />
      : list && list.total === 0 ? <Empty title="No contacts yet">Campaigns go to the customers you import here. Upload a CSV with a header row: first_name, email, phone (and optionally email_consent, whatsapp_consent).</Empty>
      : list && <div className="table-wrap"><table className="data stack-sm"><thead><tr><th>Name</th><th>Email</th><th>WhatsApp</th><th className="right" /></tr></thead><tbody>
        {list.contacts.map(contact => <tr key={contact.id}>
          <td>{contact.first_name || <span className="muted">—</span>}</td>
          <td data-label="Email">{contact.email ? <>{contact.email}{!contact.email_consent && <span className="small muted"> · no consent</span>}</> : '—'}</td>
          <td data-label="WhatsApp">{contact.phone ? <>{contact.phone}{!contact.whatsapp_consent && <span className="small muted"> · no consent</span>}</> : '—'}</td>
          <td className="right"><button className="icon-button" aria-label={`Remove ${contact.email ?? contact.phone}`} onClick={() => setRemoving(contact)} disabled={busy}><Trash2 size={14} /></button></td>
        </tr>)}
      </tbody></table>{list.total > list.contacts.length && <p className="small muted panel-body">Showing the newest {list.contacts.length} of {list.total}.</p>}</div>}

    {pending && preview && <Confirm title={`Import ${pending.name}?`} confirmLabel={`Import ${preview.rows.length} contacts`} busy={busy} onCancel={() => setPending(null)} onConfirm={() => void upload()}>
      <p>{preview.rows.length} contacts found{preview.skipped ? `, ${preview.skipped} rows skipped (no email or phone)` : ''}.</p>
      {preview.consentColumns
        ? <p>Consent is taken from the file's consent columns: <strong>{reachable}</strong> of them can be contacted.</p>
        : <label className="row" style={{ gap: 8, alignItems: 'flex-start' }}><input type="checkbox" checked={consentForAll} onChange={event => setConsentForAll(event.target.checked)} />
            <span>Everyone in this file agreed to hear from us by email and/or WhatsApp. Without this, they're imported but never messaged.</span></label>}
    </Confirm>}
    {removing && <Confirm title={`Remove ${removing.email ?? removing.phone}?`} confirmLabel="Remove contact" danger busy={busy} onCancel={() => setRemoving(null)} onConfirm={() => void remove(removing)}>
      <p>They won't get future campaigns. To stop someone ever being messaged again, add them to the suppression list instead.</p>
    </Confirm>}
  </Panel>
}
