import { useEffect, useState, type FormEvent } from 'react'
import { Copy, Plus } from 'lucide-react'
import { useSession, errorText } from '../state/session-context'
import { roleLabel } from '../lib/access'
import type { Person } from '../lib/types'
import { Confirm, Empty, Field, Modal, Note, Panel, Status, Submit } from '../components/ui'

/** What each role is for, in the words an operator picks from. */
const ROLE_HINT: Record<string, string> = {
  'client-owner': 'The client: tops up, sees invoices, receipts and engagements',
  delivery: 'Runs the work: campaigns, studio, offer jobs, engagements, top-ups',
  sales: 'Subscriptions and monthly billing',
  analyst: 'Reviews work; sees intelligence',
  finance: 'Reports and partner shares',
  auditor: 'Sees everything, changes nothing',
  partner: 'Partner organisation: sees everything, changes nothing',
  'super-operator': 'Everything in this business, incl. its people (platform operators only can grant it)',
}
const ASSIGNABLE = ['client-owner', 'delivery', 'sales', 'analyst', 'finance', 'auditor', 'partner']

/** The sign-ins of the business being viewed. Super operators only; the servers check it too. */
export function PeoplePanel({ businessName }: { businessName: string }) {
  const { api, platform, user, notify } = useSession()
  const [people, setPeople] = useState<Person[] | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [created, setCreated] = useState<{ email: string; password: string } | null>(null)
  const [disabling, setDisabling] = useState<Person | null>(null)
  const [busy, setBusy] = useState(false)
  const roles = platform ? [...ASSIGNABLE, 'super-operator'] : ASSIGNABLE
  const me = String(user?.profile.sub ?? '')

  const [version, setVersion] = useState(0)
  const load = () => setVersion(current => current + 1)
  useEffect(() => {
    let active = true
    api.people().then(
      result => { if (active) { setPeople(result.people); setFailure(null) } },
      error => { if (active) setFailure(errorText(error)) },
    )
    return () => { active = false }
  }, [api, version])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    try { await action() } catch (error) { notify('error', errorText(error)) } finally { setBusy(false) }
  }

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    void run(async () => {
      const result = await api.addPerson({
        email: String(data.get('email')).trim(), first_name: String(data.get('first_name')).trim(),
        last_name: String(data.get('last_name')).trim(), role: String(data.get('role')),
      })
      setAdding(false); setCreated({ email: result.person.email, password: result.temporary_password })
      load()
    })
  }

  function change(person: Person, patch: { role?: string; enabled?: boolean }) {
    void run(async () => {
      await api.updatePerson(person.id, patch)
      notify('success', patch.enabled === false ? `${person.email} can no longer sign in.` : patch.enabled ? `${person.email} can sign in again.` : `${person.email} is now ${roleLabel(patch.role ?? '')}.`)
      setDisabling(null); load()
    })
  }

  return <Panel title="People" meta={people ? `${people.length} with a sign-in` : undefined}
    actions={<button className="button button-secondary small" onClick={() => setAdding(true)}><Plus size={14} /> Add person</button>} flush>
    {failure && <div className="panel-body"><Note tone="failed">People couldn't be loaded: {failure}</Note></div>}
    {people === null && !failure ? <Empty title="Loading people" />
      : people && people.length === 0 ? <Empty title="Nobody can sign in to this business yet">Add its owner first: they fund the balance and see their invoices.</Empty>
      : people && <div className="table-wrap"><table className="data stack-sm"><thead><tr><th>Person</th><th>Role</th><th>Sign-in</th><th className="right" /></tr></thead><tbody>
        {people.map(person => {
          const role = person.roles[0] ?? ''
          const self = person.id === me
          return <tr key={person.id}>
            <td><div className="title">{`${person.first_name} ${person.last_name}`.trim() || person.username}</div><div className="small muted">{person.email || person.username}</div></td>
            <td data-label="Role">{self || (role === 'super-operator' && !platform)
              ? roleLabel(role || 'none')
              : <select aria-label={`Role for ${person.email}`} value={role} disabled={busy} onChange={event => change(person, { role: event.target.value })}>
                {!role && <option value="">No role</option>}
                {roles.map(option => <option key={option} value={option}>{roleLabel(option)}</option>)}
              </select>}</td>
            <td data-label="Sign-in">{!person.enabled ? <Status state="stopped" label="Turned off" />
              : person.must_set_password ? <Status state="needs-review" label="Hasn't signed in yet" /> : <Status state="done" label="Active" />}</td>
            <td className="right">{self ? <span className="small muted">You</span>
              : person.enabled ? <button className="button button-danger-quiet small" disabled={busy} onClick={() => setDisabling(person)}>Turn off</button>
              : <button className="button button-secondary small" disabled={busy} onClick={() => change(person, { enabled: true })}>Turn on</button>}</td>
          </tr>
        })}
      </tbody></table></div>}

    {adding && <Modal title={`Add a person to ${businessName}`} description="They get a one-time password from you and choose their own at first sign-in." onClose={() => setAdding(false)}>
      <form className="form-stack" onSubmit={add}>
        <div className="form-grid">
          <Field label="First name" name="first_name" required maxLength={80} />
          <Field label="Last name" name="last_name" required maxLength={80} />
        </div>
        <Field label="Email" name="email" type="email" required hint="This is their sign-in." />
        <label className="field"><span>Role</span>
          <select name="role" defaultValue="client-owner" required>
            {roles.map(option => <option key={option} value={option}>{roleLabel(option)} — {ROLE_HINT[option]}</option>)}
          </select>
        </label>
        <Submit busy={busy} label="Create sign-in" />
      </form>
    </Modal>}

    {created && <Modal title="Sign-in created" onClose={() => setCreated(null)}>
      <div className="form-stack">
        <dl className="props"><dt>Email</dt><dd className="mono">{created.email}</dd><dt>One-time password</dt><dd className="mono">{created.password}</dd></dl>
        <Note tone="attention">Shown once and not stored. Send it to them now; they replace it when they first sign in.</Note>
        <button className="button button-secondary" onClick={() => void navigator.clipboard.writeText(`Your VolumeOS account is ready.\nSign in: ${window.location.origin}\nEmail: ${created.email}\nOne-time password: ${created.password}\nYou'll choose your own password when you first sign in.`)}>
          <Copy size={15} /> Copy sign-in details</button>
      </div>
    </Modal>}

    {disabling && <Confirm title={`Turn off ${disabling.email}?`} confirmLabel="Turn off sign-in" danger busy={busy}
      onCancel={() => setDisabling(null)} onConfirm={() => change(disabling, { enabled: false })}>
      <p className="body-copy">They can't sign in until someone turns them back on. Nothing they did is removed.</p>
    </Confirm>}
  </Panel>
}
