import { useEffect, useRef, type InputHTMLAttributes, type ReactNode } from 'react'
import { ArrowRight, CircleAlert, CircleCheck, Info, Loader2, Lock, Search, X } from 'lucide-react'
import { describe, KIND_STAGES, STAGES, type Stage, type WorkItem, type WorkKind, type WorkState } from '../lib/workflows'
import { needs, type Action } from '../lib/access'
import { ago, dateTime } from '../lib/format'

/* ------------------------------------------------------------------ identity */
export function Mark() {
  return <span className="mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M4 5h3.4l4.6 11 4.6-11H20l-6.4 14h-3.2z" fill="currentColor" /></svg></span>
}

/** `01 / COME-BACK ENGINE` — the offer's number is its identity everywhere. */
export function OfferMark({ id, name, compact }: { id: string | null | undefined; name?: string; compact?: boolean }) {
  if (!id) return <span className="muted">—</span>
  return <span className="offer-mark" title={name ? `${id} / ${name}` : `Offer ${id}`}><b>{id}</b>{!compact && name && <><i>/</i><span>{name}</span></>}</span>
}

/* ------------------------------------------------------------------ status */
const STATE_LABEL: Record<WorkState, string> = { running: 'Running', 'needs-review': 'Needs a person', done: 'Done', stopped: 'Stopped', failed: 'Failed' }

export function Status({ state, label }: { state: WorkState; label?: string }) {
  return <span className={`status ${state}`}>{label ?? STATE_LABEL[state]}</span>
}

export function WorkStatus({ item }: { item: Pick<WorkItem, 'state' | 'label'> }) {
  return <Status state={item.state} label={item.label} />
}

export function KindStatus({ kind, status }: { kind: WorkKind; status: string }) {
  const mapping = describe(kind, status)
  return <Status state={mapping.state} label={mapping.label} />
}

function stageIndex(kind: WorkKind, stage: Stage) { return KIND_STAGES[kind].indexOf(stage) }

/** Compact progress: one bar per stage this kind of work goes through. */
export function StageTrack({ item }: { item: Pick<WorkItem, 'kind' | 'stage' | 'state'> }) {
  const stages = KIND_STAGES[item.kind]
  const at = stageIndex(item.kind, item.stage)
  const label = `${STAGES.find(s => s.id === item.stage)?.label ?? item.stage}, step ${at + 1} of ${stages.length}`
  return <span className="stage-track" role="img" aria-label={label} title={label}>{stages.map((stage, index) => {
    const done = index < at || (index === at && item.state === 'done')
    const current = index === at && item.state !== 'done'
    return <span key={stage} className={done ? 'done' : current ? `current ${item.state}` : ''} />
  })}</span>
}

/** Full progression with stage names: where this piece of work is between preparation and delivery. */
export function StageSteps({ item }: { item: Pick<WorkItem, 'kind' | 'stage' | 'state' | 'label'> }) {
  const stages = KIND_STAGES[item.kind]
  const at = stageIndex(item.kind, item.stage)
  return <ol className="stage-steps" aria-label="Progress">{stages.map((stage, index) => {
    const done = index < at || (index === at && item.state === 'done')
    const current = index === at && item.state !== 'done'
    const name = STAGES.find(s => s.id === stage)?.label ?? stage
    return <li key={stage} className={done ? 'done' : current ? `current ${item.state}` : ''} aria-current={current ? 'step' : undefined}>
      <strong>{name}</strong>{done ? 'Done' : current ? item.label : 'Not yet'}
    </li>
  })}</ol>
}

/* ------------------------------------------------------------------ structure */
export function PageHead({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: ReactNode; actions?: ReactNode }) {
  return <header className="page-head"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1>{description && <p>{description}</p>}</div>{actions && <div className="actions">{actions}</div>}</header>
}

export function Panel({ title, meta, actions, children, flush, id }: { title?: ReactNode; meta?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; id?: string }) {
  return <section className="panel" aria-labelledby={id}>
    {(title || actions) && <div className="panel-head"><div>{title && <h2 id={id}>{title}</h2>}{meta && <p>{meta}</p>}</div>{actions && <div className="row">{actions}</div>}</div>}
    {flush ? children : <div className="panel-body">{children}</div>}
  </section>
}

export function Figure({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: 'attention' | 'failed' | 'unknown' }) {
  return <div className={`figure ${tone ?? ''}`}><span>{label}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</div>
}

export function Empty({ title, children, center }: { title: string; children?: ReactNode; center?: boolean }) {
  return <div className={`empty ${center ? 'center' : ''}`}><h3>{title}</h3>{children && <p>{children}</p>}</div>
}

export function Note({ tone, children }: { tone?: 'attention' | 'failed' | 'done'; children: ReactNode }) {
  const Icon = tone === 'failed' || tone === 'attention' ? CircleAlert : tone === 'done' ? CircleCheck : Info
  return <div className={`note ${tone ?? ''}`} role={tone === 'failed' ? 'alert' : undefined}><Icon size={15} /><span>{children}</span></div>
}

/** Why an action is unavailable to this person: shown in place of an enabled button they couldn't use. */
export function RoleNote({ action }: { action: Action }) {
  return <div className="note"><Lock size={15} /><span>{needs(action)}. You can see this, but the server would refuse the action.</span></div>
}

/* ------------------------------------------------------------------ values */
export function When({ value }: { value: string | null | undefined }) {
  return <time dateTime={value ?? undefined} title={dateTime(value)}>{ago(value)}</time>
}

/* ------------------------------------------------------------------ inputs */
export function Field({ label, hint, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string; name: string; hint?: string }) {
  return <label className="field"><span>{label}</span><input {...props} />{hint && <small>{hint}</small>}</label>
}

export function Submit({ busy, label, disabled, danger }: { busy: boolean; label: string; disabled?: boolean; danger?: boolean }) {
  return <button type="submit" className={`button ${danger ? 'button-danger' : 'button-primary'}`} disabled={busy || disabled}>
    {busy ? <Loader2 className="spin" size={15} /> : <ArrowRight size={15} />}{label}
  </button>
}

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { id: T; label: string; count?: number }[]; onChange: (value: T) => void; label: string }) {
  return <div className="segmented" role="group" aria-label={label}>{options.map(option =>
    <button key={option.id} type="button" aria-pressed={value === option.id} onClick={() => onChange(option.id)}>
      {option.label}{option.count !== undefined && <span className="n">{option.count}</span>}
    </button>)}</div>
}

export function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (value: string) => void; placeholder: string }) {
  return <label className="search"><Search size={14} /><span className="sr-only">{placeholder}</span><input type="search" value={value} onChange={event => onChange(event.target.value)} placeholder={placeholder} /></label>
}

/* ------------------------------------------------------------------ overlays */
export function Modal({ title, description, onClose, children, wide }: { title: string; description?: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const dialogRef = useRef<HTMLElement>(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose }, [onClose])
  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const focusable = () => Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [href], [tabindex]:not([tabindex="-1"])') ?? [])
    ;(focusable().find(element => !element.classList.contains('modal-close')) ?? focusable()[0])?.focus()
    const listener = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current()
      if (event.key !== 'Tab') return
      const elements = focusable()
      const first = elements[0]; const last = elements.at(-1)
      if (!first || !last) return
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', listener)
    return () => { document.removeEventListener('keydown', listener); document.body.style.overflow = previousOverflow; previouslyFocused?.focus() }
  }, [])
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <section ref={dialogRef} className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <button type="button" className="modal-close icon-button" onClick={onClose} aria-label="Close dialog"><X size={16} /></button>
      <h2 id="modal-title">{title}</h2>
      {description && <p className="modal-description">{description}</p>}
      {children}
    </section>
  </div>
}

/** A second, explicit step for consequential actions (money, delivery, outbound messaging). */
export function Confirm({ title, children, confirmLabel, danger, busy, onConfirm, onCancel }: {
  title: string; children: ReactNode; confirmLabel: string; danger?: boolean; busy?: boolean; onConfirm: () => void; onCancel: () => void
}) {
  return <Modal title={title} onClose={onCancel}>
    <div className="stack">
      <div className="body-copy">{children}</div>
      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button type="button" className="button button-secondary" onClick={onCancel}>Cancel</button>
        <button type="button" className={`button ${danger ? 'button-danger' : 'button-primary'}`} onClick={onConfirm} disabled={busy}>
          {busy && <Loader2 className="spin" size={15} />}{confirmLabel}
        </button>
      </div>
    </div>
  </Modal>
}

export function Loading({ label }: { label: string }) {
  return <div className="empty" role="status"><Loader2 className="spin" size={18} /><p>{label}</p></div>
}
