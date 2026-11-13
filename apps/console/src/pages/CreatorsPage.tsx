import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Check, Circle, CircleDot, Clapperboard, Plus, RefreshCw, RotateCcw, X } from 'lucide-react'
import { useSession, errorText } from '../state/session-context'
import { allowed } from '../lib/access'
import type { CreatorChannel, CreatorEvent, CreatorProject, CreatorStage } from '../lib/types'
import { Empty, Field, Modal, Note, PageHead, RoleNote, Submit, When } from '../components/ui'

const DURATIONS = [5, 10, 15, 20, 30, 45]
const STATE_LABEL: Record<CreatorStage['state'], string> = {
  pending: 'Not started', running: 'Working…', needs_approval: 'Waiting for you', approved: 'Approved', done: 'Done',
  failed: 'Failed', skipped: 'Skipped',
}
const EVENT_LABEL: Record<string, string> = {
  created: 'Project created', title_set: 'Title chosen', script_saved: 'Script saved', approved: 'Approved',
  reopened: 'Reopened', brief_changed: 'Brief changed',
}

/** Offer 35 Creators Home: YouTube channels and their video projects, each moving through 12 production stages. */
export function CreatorsPage() {
  const { api, roles, notify } = useSession()
  const { id } = useParams()
  const navigate = useNavigate()
  const canEdit = allowed(roles, 'manageCreators')
  const [channels, setChannels] = useState<CreatorChannel[]>([])
  const [projects, setProjects] = useState<CreatorProject[]>([])
  const [channelId, setChannelId] = useState('')
  const [load, setLoad] = useState<{ state: 'loading' | 'ok' | 'error'; error: string | null }>({ state: 'loading', error: null })
  const [modal, setModal] = useState<'channel' | 'project' | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [c, p] = await Promise.all([api.creatorChannels(), api.creatorProjects()])
      setChannels(c.channels); setProjects(p.projects)
      setLoad({ state: 'ok', error: null })
    } catch (error) { setLoad({ state: 'error', error: errorText(error) }) }
  }, [api])
  useEffect(() => { const first = window.setTimeout(() => void refresh(), 0); return () => window.clearTimeout(first) }, [refresh])

  const project = projects.find(item => item.id === id) ?? (id ? undefined : undefined)
  const missing = Boolean(id && load.state === 'ok' && !project)
  const shown = useMemo(() => projects.filter(item => !channelId || item.channel_id === channelId), [projects, channelId])
  const channelName = (cid: string) => channels.find(item => item.id === cid)?.name ?? 'Unknown channel'

  async function run(action: () => Promise<void>) {
    setBusy(true)
    try { await action() } catch (error) { notify('error', errorText(error)) } finally { setBusy(false) }
  }

  function createChannel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const value = (key: string) => String(data.get(key) ?? '').trim()
    void run(async () => {
      const created = await api.createCreatorChannel({
        name: value('name'), youtube_handle: value('youtube_handle'), niche: value('niche'), audience: value('audience'),
        writing_instructions: value('writing_instructions'), visual_preset: value('visual_preset'),
      })
      setModal(null); await refresh(); setChannelId(created.id)
      notify('success', `Channel “${created.name}” added. Its writing instructions are used for every script on it.`)
    })
  }

  function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const value = (key: string) => String(data.get(key) ?? '').trim()
    void run(async () => {
      const created = await api.createCreatorProject({
        channel_id: value('channel_id'), topic: value('topic'), title: value('title'), duration_minutes: Number(value('duration_minutes')),
        style: value('style'), audience: value('audience'),
      })
      setModal(null); await refresh(); navigate(`/services/creators/${created.id}`)
      notify('success', `Project started: about ${created.target_words.toLocaleString()} words of narration for ${created.duration_minutes} minutes.`)
    })
  }

  return <div className="page">
    <PageHead eyebrow="Services · 35 Creators Home" title="Creators Home"
      description="YouTube videos from topic to upload: research, title, script, voice, scenes, thumbnail, SEO and assembly, with your approval at every gate."
      actions={<>
        <button className="button button-secondary" onClick={() => void refresh()}><RefreshCw size={14} /> Refresh</button>
        {canEdit && <button className="button button-secondary" onClick={() => setModal('channel')}><Plus size={15} /> Channel</button>}
        {canEdit && channels.length > 0 && <button className="button button-primary" onClick={() => setModal('project')}><Plus size={15} /> New video</button>}
      </>} />
    {!canEdit && <RoleNote action="manageCreators" />}
    {missing && <Note tone="failed">There's no project “{id}” in this business. The link may be mistyped, or it belongs to another business.</Note>}
    {load.state === 'error' && <Note tone="failed">Creators Home didn't load: {load.error}</Note>}
    <div className="workspace-grid">
      <section className="queue-panel" aria-label="Video projects">
        <div className="panel-heading"><h2>Videos</h2><span>{shown.length}</span></div>
        {channels.length > 0 && <label className="field" style={{ margin: '0 0 8px' }}><span className="sr-only">Channel</span>
          <select value={channelId} onChange={event => setChannelId(event.target.value)}>
            <option value="">All channels</option>
            {channels.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select></label>}
        <div className="queue-list">{shown.map(item => {
          const stage = item.stages.find(s => s.key === item.current_stage)
          return <button key={item.id} className={`queue-item ${item.id === project?.id ? 'selected' : ''}`} onClick={() => navigate(`/services/creators/${item.id}`)}>
            <span className="queue-item-top"><strong>{item.title || item.topic}</strong></span>
            <span>{channelName(item.channel_id)} · {item.duration_minutes} min</span>
            <small>{stage ? `${stage.label}: ${STATE_LABEL[stage.state]}` : 'Finished'} · <When value={item.updated_at} /></small>
          </button>
        })}</div>
        {!shown.length && <Empty title={load.state === 'loading' ? 'Loading' : channels.length ? 'No videos yet' : 'Add a channel first'} />}
      </section>
      <section className="review-panel">
        {project ? <ProjectWorkspace key={project.id} project={project} channel={channels.find(c => c.id === project.channel_id)} canEdit={canEdit}
          onChange={updated => setProjects(current => current.map(item => item.id === updated.id ? updated : item))} />
          : <HowItWorks canEdit={canEdit} hasChannel={channels.length > 0} onChannel={() => setModal('channel')} onProject={() => setModal('project')} />}
      </section>
    </div>

    {modal === 'channel' && <Modal title="Add a YouTube channel" description="Each channel keeps its own writing instructions and look; every script and image on it follows them." onClose={() => setModal(null)} wide>
      <form onSubmit={createChannel} className="form-stack">
        <div className="form-grid"><Field label="Channel name" name="name" placeholder="Money Files Decoded" required maxLength={80} /><Field label="YouTube handle" name="youtube_handle" placeholder="@MoneyFilesDecoded" maxLength={31} /></div>
        <div className="form-grid"><Field label="Niche" name="niche" placeholder="Personal finance education" maxLength={200} /><Field label="Audience" name="audience" placeholder="Young professionals, 22–35" maxLength={500} /></div>
        <label className="field"><span>Writing instructions</span><textarea name="writing_instructions" rows={5} maxLength={8000}
          placeholder="e.g. Finance education with relatable examples, engaging explanations and one practical lesson per section. Friendly, never preachy." />
          <small>Used for every script on this channel: tone, structure, what to always include.</small></label>
        <label className="field"><span>Visual preset (optional)</span><textarea name="visual_preset" rows={2} maxLength={2000} placeholder="e.g. warm cinematic light, realistic photography, muted earth tones" /></label>
        <Submit busy={busy} label="Add channel" />
      </form>
    </Modal>}

    {modal === 'project' && <Modal title="New video" description="Start from a topic, or a title you already have. Nothing costly runs without your approval." onClose={() => setModal(null)} wide>
      <form onSubmit={createProject} className="form-stack">
        <label className="field"><span>Channel</span><select name="channel_id" defaultValue={channelId || channels[0]?.id} required>
          {channels.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="field"><span>Topic</span><textarea name="topic" rows={2} required minLength={3} maxLength={2000} placeholder="Why your salary disappears before the month ends" /></label>
        <Field label="Title (optional)" name="title" maxLength={100} hint="Leave empty to research and choose one." />
        <div className="form-grid">
          <label className="field"><span>Length</span><select name="duration_minutes" defaultValue="15">{DURATIONS.map(d => <option key={d} value={d}>{d} minutes · about {(d * 150).toLocaleString()} words</option>)}</select></label>
          <Field label="Audience (optional)" name="audience" maxLength={1000} placeholder="People who earn but never save" />
        </div>
        <label className="field"><span>Style (optional)</span><textarea name="style" rows={2} maxLength={1000} placeholder="Storytelling with one real-life example per section" /></label>
        <Submit busy={busy} label="Start the video" />
      </form>
    </Modal>}
  </div>
}

function StageList({ stages }: { stages: CreatorStage[] }) {
  return <ol aria-label="Production stages" style={{ listStyle: 'none', padding: 0, margin: '12px 0', display: 'grid', gap: 4 }}>
    {stages.map((stage, index) => {
      const icon = stage.state === 'approved' || stage.state === 'done' ? <Check size={14} />
        : stage.state === 'failed' ? <X size={14} /> : stage.state === 'needs_approval' || stage.state === 'running' ? <CircleDot size={14} /> : <Circle size={14} />
      const tone = stage.state === 'needs_approval' ? 'var(--attention, #b45309)' : stage.state === 'failed' ? 'var(--failed, #b91c1c)'
        : stage.state === 'approved' || stage.state === 'done' ? 'var(--done, #15803d)' : 'var(--muted, #6b7280)'
      return <li key={stage.key} className="row" style={{ gap: 8, alignItems: 'center' }}>
        <span className="mono small" style={{ minWidth: 20, color: 'var(--muted, #6b7280)' }}>{String(index + 1).padStart(2, '0')}</span>
        <span style={{ color: tone, display: 'inline-flex' }}>{icon}</span>
        <span style={{ flex: 1 }}>{stage.label}{stage.kind === 'approval' && <span className="small muted"> · your approval</span>}</span>
        <span className="small" style={{ color: tone }}>{STATE_LABEL[stage.state]}</span>
      </li>
    })}
  </ol>
}

function ProjectWorkspace({ project, channel, canEdit, onChange }: { project: CreatorProject; channel?: CreatorChannel; canEdit: boolean; onChange: (p: CreatorProject) => void }) {
  const { api, notify } = useSession()
  const [busy, setBusy] = useState(false)
  const [title, setTitle] = useState(project.record.title?.chosen ?? project.title)
  const [script, setScript] = useState(project.record.script?.text ?? '')
  const [reason, setReason] = useState('')
  const [events, setEvents] = useState<CreatorEvent[]>([])
  const stage = (key: string) => project.stages.find(item => item.key === key)!
  const words = script.trim() ? script.trim().split(/\s+/).length : 0

  const loadEvents = useCallback(() => { api.creatorEvents(project.id).then(r => setEvents(r.events), () => setEvents([])) }, [api, project.id])
  useEffect(() => { const t = window.setTimeout(loadEvents, 0); return () => window.clearTimeout(t) }, [loadEvents, project.version])

  async function act(action: () => Promise<CreatorProject>, message: string) {
    setBusy(true)
    try { const updated = await action(); onChange(updated); notify('success', message) }
    catch (error) { notify('error', errorText(error)) } finally { setBusy(false) }
  }
  const approve = (key: string, message: string) => act(() => api.creatorStage(project.id, key, 'approve', '', project.version), message)
  const reopen = (key: string) => act(async () => { const r = await api.creatorStage(project.id, key, 'reopen', reason.trim(), project.version); setReason(''); return r },
    'Reopened. Everything built on it goes back to not started.')

  const titleStage = stage('title'), scriptStage = stage('script'), scriptApproval = stage('script_approval')
  const titleApproved = titleStage.state === 'approved'
  const target = project.target_words

  return <div className="stack">
    <div className="review-header"><div><span className="eyebrow">{channel?.name ?? 'Channel'} · {project.duration_minutes} minutes</span>
      <h2>{project.title || project.topic}</h2><p className="small muted">{project.topic}</p></div></div>
    <StageList stages={project.stages} />

    <div className="stack" aria-label="Title">
      <strong>1–2 · Title</strong>
      {canEdit && !titleApproved && <div className="row" style={{ gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <label className="field" style={{ flex: 1, minWidth: 260 }}><span>Video title</span>
          <input value={title} maxLength={100} onChange={e => setTitle(e.target.value)} placeholder="7 Signs God Is Preparing Your Spouse Right Now" /></label>
        <button className="button button-secondary" disabled={busy || title.trim().length < 3}
          onClick={() => void act(() => api.setCreatorTitle(project.id, title.trim(), [title.trim()], project.version), 'Title saved. Approve it to write the script.')}>Save title</button>
        {titleStage.state === 'needs_approval' && <button className="button button-primary" disabled={busy}
          onClick={() => void approve('title', 'Title approved.')}>Approve title</button>}
      </div>}
      {titleApproved && <p>“{project.title}” <span className="small muted">approved <When value={project.record.approvals?.title?.at} /></span></p>}
      {!canEdit && !titleApproved && <p className="small muted">{project.title ? `“${project.title}”, waiting for approval` : 'No title yet.'}</p>}
      <p className="small muted">AI title research from real YouTube data comes in a later step; for now type the title you want.</p>
    </div>

    <div className="stack" aria-label="Script">
      <strong>3–4 · Script</strong>
      {!titleApproved ? <p className="small muted">Approve the title first; the script is written for it.</p> : <>
        <label className="field"><span>Narration ({words.toLocaleString()} of about {target.toLocaleString()} words)</span>
          <textarea rows={14} value={script} readOnly={!canEdit || scriptApproval.state === 'approved'} onChange={e => setScript(e.target.value)}
            placeholder={'# Hook\nOpen with the question your viewer is already asking…\n\n# Chapter 1: …'} /></label>
        <p className="small muted">Start a chapter with a line beginning “#”, “Chapter” or “Scene”: the voiceover and the scenes follow these chapters, in order.
          AI script writing (using {channel?.name ?? 'the channel'}'s instructions) comes in the next step.</p>
        {canEdit && scriptApproval.state !== 'approved' && <div className="row" style={{ gap: 8 }}>
          <button className="button button-secondary" disabled={busy || words < 50}
            onClick={() => void act(() => api.setCreatorScript(project.id, script, project.version), 'Script saved. Approve it when it reads right.')}>Save script</button>
          {scriptApproval.state === 'needs_approval' && <button className="button button-primary" disabled={busy}
            onClick={() => void approve('script_approval', 'Script approved. The voiceover is made from exactly this text.')}>Approve script</button>}
        </div>}
        {project.record.script && <p className="small muted">{project.record.script.sections.length} chapter{project.record.script.sections.length === 1 ? '' : 's'} ·
          about {project.record.script.estimated_minutes} minutes spoken · {scriptStage.state === 'done' ? 'saved' : ''} {scriptApproval.state === 'approved' ? '· approved' : ''}</p>}
      </>}
    </div>

    {canEdit && project.stages.some(s => s.kind === 'approval' && s.state === 'approved') && <div className="approval-form">
      <label className="field"><span>Change something already approved</span><input value={reason} maxLength={1000} onChange={e => setReason(e.target.value)} placeholder="Why (required)" /></label>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        {project.stages.filter(s => s.kind === 'approval' && s.state === 'approved').map(s =>
          <button key={s.key} className="button button-secondary" disabled={busy || !reason.trim()} onClick={() => void reopen(s.key)}><RotateCcw size={14} /> Reopen {s.label.toLowerCase()}</button>)}
      </div>
    </div>}

    {scriptApproval.state === 'approved' && <Note>Next: voiceover, scenes, images, thumbnail, SEO and the assembled video. These arrive in the following releases of Creators Home.</Note>}

    <div className="stack" aria-label="History">
      <strong>History</strong>
      {events.length ? <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 4 }}>
        {events.map((event, index) => <li key={index} className="small"><When value={event.created_at} /> · {EVENT_LABEL[event.kind] ?? event.kind}{event.stage ? ` (${event.stage.replace('_', ' ')})` : ''}</li>)}
      </ul> : <p className="small muted">Nothing yet.</p>}
    </div>
  </div>
}

function HowItWorks({ canEdit, hasChannel, onChannel, onProject }: { canEdit: boolean; hasChannel: boolean; onChannel: () => void; onProject: () => void }) {
  const steps = [
    ['Add your channel', 'Give it its own writing instructions and look, e.g. biblical references and prophetic storytelling, or finance lessons with relatable examples.'],
    ['Start a video', 'Enter a topic (or a title you have) and the length: 5 to 45 minutes. The narration target follows from the length, about 150 words a minute.'],
    ['Approve as it goes', 'Title, script, and later the images, final video and publishing each wait for your approval; nothing costly runs without it.'],
    ['Publish', 'The finished package (video, thumbnail, description, tags, chapters) goes to your YouTube channel once you approve it.'],
  ]
  return <div className="stack" aria-label="How Creators Home works">
    <span className="eyebrow"><Clapperboard size={14} /> How Creators Home works</span>
    <ol style={{ display: 'grid', gap: 12, paddingLeft: 0, listStyle: 'none' }}>
      {steps.map(([title, text], index) => <li key={title} className="row" style={{ gap: 12, alignItems: 'flex-start' }}>
        <strong className="mono" style={{ minWidth: 22 }}>{index + 1}</strong>
        <div><strong>{title}</strong><p className="small muted" style={{ margin: '2px 0 0' }}>{text}</p></div>
      </li>)}
    </ol>
    {canEdit && <div className="row" style={{ gap: 8 }}>
      {!hasChannel && <button className="button button-primary" onClick={onChannel}><Plus size={15} /> Add your first channel</button>}
      {hasChannel && <button className="button button-primary" onClick={onProject}><Plus size={15} /> Start a video</button>}
    </div>}
  </div>
}
