import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Check, Circle, CircleDot, Clapperboard, Download, Mic, Play, Plus, RefreshCw, RotateCcw, Sparkles, X } from 'lucide-react'
import { useSession, errorText } from '../state/session-context'
import { allowed } from '../lib/access'
import type { CreatorChannel, CreatorEvent, CreatorJob, CreatorProject, CreatorStage, CreatorVoice } from '../lib/types'
import { Confirm, Empty, Field, Modal, Note, PageHead, RoleNote, Submit, When } from '../components/ui'

const DURATIONS = [5, 10, 15, 20, 30, 45]
const STATE_LABEL: Record<CreatorStage['state'], string> = {
  pending: 'Not started', running: 'Working…', needs_approval: 'Waiting for you', approved: 'Approved', done: 'Done',
  failed: 'Failed', skipped: 'Skipped',
}
const EVENT_LABEL: Record<string, string> = {
  created: 'Project created', title_set: 'Title chosen', script_saved: 'Script saved', approved: 'Approved',
  reopened: 'Reopened', brief_changed: 'Brief changed', job_started: 'Started', job_failed: 'Failed', voiceover_saved: 'Voiceover made',
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
  const [jobs, setJobs] = useState<CreatorJob[]>([])
  const [confirm, setConfirm] = useState<'script' | 'voiceover' | null>(null)
  const [voice, setVoice] = useState(project.record.voiceover?.voice ?? '')
  const [speed, setSpeed] = useState(1)
  const stage = (key: string) => project.stages.find(item => item.key === key)!
  const words = script.trim() ? script.trim().split(/\s+/).length : 0
  const running = project.stages.some(s => s.state === 'running')

  // The script box follows the saved script whenever a new one arrives (written by AI, or saved elsewhere).
  const savedScript = project.record.script?.updated_at
  useEffect(() => { const t = window.setTimeout(() => setScript(project.record.script?.text ?? ''), 0); return () => window.clearTimeout(t) }, [savedScript]) // eslint-disable-line react-hooks/exhaustive-deps

  const loadSide = useCallback(() => {
    api.creatorEvents(project.id).then(r => setEvents(r.events), () => setEvents([]))
    api.creatorJobs(project.id).then(r => setJobs(r.jobs), () => setJobs([]))
  }, [api, project.id])
  useEffect(() => { const t = window.setTimeout(loadSide, 0); return () => window.clearTimeout(t) }, [loadSide, project.version])

  // While the worker is making something, check every few seconds for progress and the result.
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => {
      api.creatorProject(project.id).then(updated => { if (updated.version !== project.version) onChange(updated) }, () => undefined)
      api.creatorJobs(project.id).then(r => setJobs(r.jobs), () => undefined)
    }, 5000)
    return () => window.clearInterval(timer)
  }, [api, running, project.id, project.version, onChange])

  async function act(action: () => Promise<CreatorProject>, message: string) {
    setBusy(true)
    try { const updated = await action(); onChange(updated); notify('success', message) }
    catch (error) { notify('error', errorText(error)) } finally { setBusy(false) }
  }
  const approve = (key: string, message: string) => act(() => api.creatorStage(project.id, key, 'approve', '', project.version), message)
  const reopen = (key: string) => act(async () => { const r = await api.creatorStage(project.id, key, 'reopen', reason.trim(), project.version); setReason(''); return r },
    'Reopened. Everything built on it goes back to not started.')
  const startJob = (kind: 'script' | 'voiceover') => act(async () => {
    const r = await api.startCreatorJob(project.id, kind === 'script' ? { kind, version: project.version } : { kind, voice, speed, version: project.version })
    setConfirm(null); setJobs(current => [r.job, ...current]); return r.project
  }, kind === 'script' ? 'Writing the script. It appears here when it is ready.' : 'Making the voiceover, chapter by chapter.')

  const titleStage = stage('title'), scriptStage = stage('script'), scriptApproval = stage('script_approval'), voiceStage = stage('voiceover')
  const titleApproved = titleStage.state === 'approved'
  const scriptApproved = scriptApproval.state === 'approved'
  const target = project.target_words
  const latest = (kind: string) => jobs.find(job => job.kind === kind)
  const scriptMinutes = Math.max(1, Math.round(target / 1500))

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
    </div>

    <div className="stack" aria-label="Script">
      <strong>3–4 · Script</strong>
      {!titleApproved ? <p className="small muted">Approve the title first; the script is written for it.</p> : <>
        {scriptStage.state === 'running' && <Note>Writing the script… {latest('script')?.progress || 'queued'}. Long scripts are written chapter by chapter; this page updates by itself.</Note>}
        {scriptStage.state === 'failed' && <Note tone="failed">The script couldn't be written: {scriptStage.note}</Note>}
        {canEdit && !scriptApproved && scriptStage.state !== 'running' && <div className="row" style={{ gap: 8 }}>
          <button className="button button-primary" disabled={busy} onClick={() => setConfirm('script')}><Sparkles size={14} /> {project.record.script ? 'Write it again with AI' : 'Write the script with AI'}</button>
          <span className="small muted">Uses {channel?.name ?? 'the channel'}'s writing instructions; you can edit everything after.</span>
        </div>}
        <label className="field"><span>Narration ({words.toLocaleString()} of about {target.toLocaleString()} words)</span>
          <textarea rows={14} value={script} readOnly={!canEdit || scriptApproved || scriptStage.state === 'running'} onChange={e => setScript(e.target.value)}
            placeholder={'# Hook\nOpen with the question your viewer is already asking…\n\n# Chapter 1: …'} /></label>
        <p className="small muted">Chapters start with a line beginning “#”, “Chapter” or “Scene”: the voiceover and the scenes follow them, in order.</p>
        {canEdit && !scriptApproved && scriptStage.state !== 'running' && <div className="row" style={{ gap: 8 }}>
          <button className="button button-secondary" disabled={busy || words < 50 || script === (project.record.script?.text ?? '')}
            onClick={() => void act(() => api.setCreatorScript(project.id, script, project.version), 'Script saved. Approve it when it reads right.')}>Save my edits</button>
          {scriptApproval.state === 'needs_approval' && <button className="button button-primary" disabled={busy || script !== (project.record.script?.text ?? '')}
            onClick={() => void approve('script_approval', 'Script approved. The voiceover is made from exactly this text.')}>Approve script</button>}
        </div>}
        {project.record.script && <p className="small muted">{project.record.script.sections.length} chapters ·
          about {project.record.script.estimated_minutes} minutes spoken · {project.record.script.source.startsWith('ai:') ? `written by ${project.record.script.source.slice(3)}` : 'written by hand'}{scriptApproved ? ' · approved' : ''}</p>}
      </>}
    </div>

    <div className="stack" aria-label="Voiceover">
      <strong>5 · Voiceover</strong>
      {!scriptApproved ? <p className="small muted">Approve the script first; the voice reads exactly the approved text.</p> : <>
        {voiceStage.state === 'running' && <Note>Making the voiceover… {latest('voiceover')?.progress || 'queued'}.</Note>}
        {voiceStage.state === 'failed' && <Note tone="failed">The voiceover couldn't be made: {voiceStage.note}</Note>}
        {canEdit && voiceStage.state !== 'running' && <VoicePicker voice={voice || (channel?.default_voice ?? '')} speed={speed} onVoice={setVoice} onSpeed={setSpeed}
          action={<button className="button button-primary" disabled={busy || !(voice || channel?.default_voice)} onClick={() => { if (!voice && channel?.default_voice) setVoice(channel.default_voice); setConfirm('voiceover') }}>
            <Mic size={14} /> {project.record.voiceover ? 'Make it again' : 'Make the voiceover'}</button>} />}
        {project.record.voiceover && voiceStage.state === 'done' && <Narration project={project} />}
      </>}
    </div>

    {canEdit && project.stages.some(s => s.kind === 'approval' && s.state === 'approved') && <div className="approval-form">
      <label className="field"><span>Change something already approved</span><input value={reason} maxLength={1000} onChange={e => setReason(e.target.value)} placeholder="Why (required)" /></label>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        {project.stages.filter(s => s.kind === 'approval' && s.state === 'approved').map(s =>
          <button key={s.key} className="button button-secondary" disabled={busy || !reason.trim()} onClick={() => void reopen(s.key)}><RotateCcw size={14} /> Reopen {s.label.toLowerCase()}</button>)}
      </div>
    </div>}

    {voiceStage.state === 'done' && <Note>Next: scenes, images, thumbnail, SEO and the assembled video. These arrive in the following releases of Creators Home.</Note>}

    <div className="stack" aria-label="History">
      <strong>History</strong>
      {events.length ? <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 4 }}>
        {events.map((event, index) => <li key={index} className="small"><When value={event.created_at} /> · {EVENT_LABEL[event.kind] ?? event.kind}{event.stage ? ` (${event.stage.replace('_', ' ')})` : ''}{event.actor.startsWith('worker:') ? ' · by the worker' : ''}</li>)}
      </ul> : <p className="small muted">Nothing yet.</p>}
    </div>

    {confirm === 'script' && <Confirm title="Write the script with AI?" confirmLabel="Write the script" busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => void startJob('script')}>
      <p>About {target.toLocaleString()} words in {model.chapters(project.duration_minutes)} chapters, following {channel?.name ?? 'the channel'}'s instructions. It takes about {scriptMinutes}–{scriptMinutes * 2} minutes on the open-source model; nothing is approved until you approve it.
        {project.record.script ? ' The current script is replaced.' : ''}</p>
    </Confirm>}
    {confirm === 'voiceover' && <Confirm title="Make the voiceover?" confirmLabel="Make the voiceover" busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => void startJob('voiceover')}>
      <p>Voice {voice || channel?.default_voice} at {speed.toFixed(2)}× reads the approved script, chapter by chapter (about {project.record.script?.estimated_minutes ?? project.duration_minutes} minutes of audio, a few minutes to make).</p>
    </Confirm>}
  </div>
}

const model = { chapters: (minutes: number) => Math.max(3, Math.min(18, Math.round(minutes / 2.5))) }

function VoicePicker({ voice, speed, onVoice, onSpeed, action }: { voice: string; speed: number; onVoice: (v: string) => void; onSpeed: (s: number) => void; action: ReactNode }) {
  const { api, notify } = useSession()
  const [voices, setVoices] = useState<CreatorVoice[] | null>(null)
  const [failure, setFailure] = useState('')
  const [filter, setFilter] = useState<{ gender: string; accent: string }>({ gender: '', accent: '' })
  const [sample, setSample] = useState<string>('')
  const [loading, setLoading] = useState(false)
  useEffect(() => { let on = true; api.creatorVoices().then(r => on && setVoices(r.voices), e => on && setFailure(errorText(e))); return () => { on = false } }, [api])
  useEffect(() => () => { if (sample) URL.revokeObjectURL(sample) }, [sample])
  if (failure) return <Note tone="failed">The voice library didn't load: {failure}</Note>
  if (!voices) return <p className="small muted">Loading voices…</p>
  const shown = voices.filter(v => (!filter.gender || v.gender === filter.gender) && (!filter.accent || v.accent === filter.accent))
  async function listen() {
    if (!voice) return
    setLoading(true)
    try { const url = await api.previewCreatorVoice(voice, speed); setSample(url) } catch (error) { notify('error', errorText(error)) } finally { setLoading(false) }
  }
  return <div className="stack" aria-label="Voice library">
    <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
      <label className="field"><span>Voice</span><select value={voice} onChange={e => { onVoice(e.target.value); setSample('') }}>
        <option value="">Choose a voice…</option>
        {shown.map(v => <option key={v.id} value={v.id}>{v.name} · {v.gender} · {v.accent}</option>)}</select></label>
      <label className="field"><span>Show</span><select value={filter.gender} onChange={e => setFilter({ ...filter, gender: e.target.value })}>
        <option value="">All voices</option><option value="female">Female</option><option value="male">Male</option></select></label>
      <label className="field"><span>Accent</span><select value={filter.accent} onChange={e => setFilter({ ...filter, accent: e.target.value })}>
        <option value="">Any</option><option value="American">American</option><option value="British">British</option></select></label>
      <label className="field" style={{ minWidth: 160 }}><span>Speed {speed.toFixed(2)}×</span>
        <input type="range" min={0.7} max={1.3} step={0.05} value={speed} onChange={e => { onSpeed(Number(e.target.value)); setSample('') }} /></label>
    </div>
    <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <button className="button button-secondary" disabled={!voice || loading} onClick={() => void listen()}><Play size={14} /> {loading ? 'Making a sample…' : 'Listen to this voice'}</button>
      {sample && <audio controls autoPlay src={sample} aria-label="Voice sample" />}
      {action}
    </div>
  </div>
}

function Narration({ project }: { project: CreatorProject }) {
  const { api, notify } = useSession()
  const [urls, setUrls] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState('')
  const voice = project.record.voiceover!
  useEffect(() => () => Object.values(urls).forEach(url => URL.revokeObjectURL(url)), [urls])
  async function load(name: string) {
    setLoading(name)
    try { const url = await api.creatorAudio(project.id, name); setUrls(current => ({ ...current, [name]: url })) }
    catch (error) { notify('error', errorText(error)) } finally { setLoading('') }
  }
  const minutes = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`
  return <div className="stack" aria-label="Narration">
    <p className="small muted">Voice {voice.voice} at {voice.speed}× · {minutes(voice.total_seconds)} in total · made <When value={voice.updated_at} /></p>
    <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <strong className="small">Whole narration</strong>
      {urls.narration ? <><audio controls src={urls.narration} aria-label="Whole narration" /><a className="button button-secondary small" href={urls.narration} download={`${project.title || 'narration'}.wav`}><Download size={14} /> WAV</a></>
        : <button className="button button-secondary small" disabled={loading === 'narration'} onClick={() => void load('narration')}><Play size={14} /> {loading === 'narration' ? 'Loading…' : 'Load'}</button>}
    </div>
    <ol style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 6 }}>
      {voice.sections.map(section => { const name = `chapter-${String(section.index).padStart(2, '0')}`; return <li key={name} className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="small" style={{ minWidth: 220 }}>{section.index}. {section.heading} <span className="muted">({minutes(section.seconds)})</span></span>
        {urls[name] ? <audio controls src={urls[name]} aria-label={`Chapter ${section.index}`} />
          : <button className="button button-secondary small" disabled={loading === name} onClick={() => void load(name)}><Play size={14} /> {loading === name ? 'Loading…' : 'Play'}</button>}
      </li> })}
    </ol>
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
