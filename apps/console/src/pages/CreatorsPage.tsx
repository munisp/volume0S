import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { Check, Circle, CircleDot, Clapperboard, Search, Download, ExternalLink, Link2, Mic, Play, Plus, RefreshCw, RotateCcw, Sparkles, Unlink, Upload, X } from 'lucide-react'
import { useSession, errorText } from '../state/session-context'
import { allowed } from '../lib/access'
import type { CreatorChannel, CreatorEvent, CreatorJob, CreatorLaunch, CreatorProject, CreatorResearch, CreatorScene, CreatorStage, CreatorVoice, CreatorYouTube } from '../lib/types'
import { Confirm, Empty, Field, Modal, Note, PageHead, RoleNote, Submit, When } from '../components/ui'

const DURATIONS = [5, 10, 15, 20, 30, 45]
const STATE_LABEL: Record<CreatorStage['state'], string> = {
  pending: 'Not started', running: 'Working…', needs_approval: 'Waiting for you', approved: 'Approved', done: 'Done',
  failed: 'Failed', skipped: 'Skipped',
}
const EVENT_LABEL: Record<string, string> = {
  created: 'Project created', title_set: 'Title chosen', script_saved: 'Script saved', approved: 'Approved',
  reopened: 'Reopened', brief_changed: 'Brief changed', job_started: 'Started', job_failed: 'Failed', voiceover_saved: 'Voiceover made', scenes_saved: 'Scenes planned', images_saved: 'Images made', thumbnails_saved: 'Thumbnails made', thumbnail_chosen: 'Thumbnail chosen', scene_edited: 'Scene edited', seo_saved: 'SEO package written', video_saved: 'Video made', research_saved: 'Research done', published: 'Uploaded to YouTube',
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
  const [search, setSearch] = useSearchParams()

  // Back from Google's sign-in: say how it went and return to the video the person started from.
  useEffect(() => {
    const result = search.get('youtube')
    if (!result) return
    const messages: Record<string, [string, 'success' | 'error']> = {
      connected: ['YouTube connected. Videos on this channel can now be uploaded from here.', 'success'],
      cancelled: ['YouTube wasn\'t connected: the Google sign-in was cancelled.', 'error'],
      failed: ['YouTube couldn\'t be connected. Try again; if it keeps failing, check the Google account owns the channel.', 'error'],
      invalid: ['That YouTube sign-in link expired or wasn\'t valid. Start it again from the video.', 'error'],
    }
    const [message, tone] = messages[result] ?? messages.invalid
    notify(tone, message)
    let back = ''
    try { back = window.sessionStorage.getItem('creators.youtube.return') ?? ''; window.sessionStorage.removeItem('creators.youtube.return') } catch { back = '' }
    if (back && /^[0-9a-f-]{36}$/.test(back)) navigate(`/services/creators/${back}`, { replace: true })
    else setSearch({}, { replace: true })
  }, [search, setSearch, navigate, notify])

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
  const [confirm, setConfirm] = useState<'research' | 'script' | 'voiceover' | 'scenes' | 'images' | 'thumbnail' | 'seo' | 'assembly' | null>(null)
  const [burn, setBurn] = useState(false)
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
  const JOB_MESSAGE = { research: 'Researching angles, questions and titles. About a minute.', script: 'Writing the script. It appears here when it is ready.', voiceover: 'Making the voiceover, chapter by chapter.',
    scenes: 'Planning the scenes from the narration.', images: 'Making the images, scene by scene.', thumbnail: 'Making three thumbnail options.',
    seo: 'Writing the SEO package.', assembly: 'Making the video on ghana.' }
  const startJob = (kind: 'research' | 'script' | 'voiceover' | 'scenes' | 'images' | 'thumbnail' | 'seo' | 'assembly', scenes: number[] = []) => act(async () => {
    const input = kind === 'voiceover' ? { kind, voice, speed, version: project.version } : kind === 'images' ? { kind, scenes, version: project.version }
      : kind === 'assembly' ? { kind, burn_subtitles: burn, version: project.version } : { kind, version: project.version }
    const r = await api.startCreatorJob(project.id, input)
    setConfirm(null); setJobs(current => [r.job, ...current]); return r.project
  }, JOB_MESSAGE[kind])

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
      <strong>1–2 · Research and title</strong>
      {stage('research').state === 'running' && <Note>Researching… {latest('research')?.progress || 'queued'}. This page updates by itself.</Note>}
      {stage('research').state === 'failed' && <Note tone="failed">The research didn't finish: {stage('research').note}. Try again, or type a title yourself.</Note>}
      {canEdit && !titleApproved && stage('research').state !== 'running' && <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="button button-secondary" disabled={busy} onClick={() => setConfirm('research')}><Search size={14} /> {project.record.research ? 'Research again' : 'Research this topic'}</button>
        <span className="small muted">Angles, viewer questions, points to cover, search keywords and scored title ideas. The script follows it.</span>
      </div>}
      {project.record.research && <ResearchPanel research={project.record.research} current={project.title} canPick={canEdit && !titleApproved && !busy}
        onPick={t => { setTitle(t); void act(() => api.setCreatorTitle(project.id, t, project.record.research!.titles.map(x => x.title), project.version), 'Title picked. Approve it to write the script.') }} />}
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

    {voiceStage.state === 'done' && <ScenesSection project={project} canEdit={canEdit} busy={busy} running={latest('scenes')?.progress || latest('images')?.progress || ''}
      onPlan={() => setConfirm('scenes')} onImages={() => setConfirm('images')} onRemake={n => void startJob('images', [n])}
      onEdit={(n, prompt, visual) => act(() => api.editCreatorScene(project.id, n, prompt, visual, project.version), 'Scene saved. Remake its image to see the change.')} />}

    {scriptApproved && <ThumbnailSection project={project} canEdit={canEdit} busy={busy} progress={latest('thumbnail')?.progress || ''} onMake={() => setConfirm('thumbnail')}
      onChoose={n => void act(() => api.chooseCreatorThumbnail(project.id, n, project.version), 'Thumbnail chosen.')} />}

    {voiceStage.state === 'done' && <SeoSection project={project} canEdit={canEdit} busy={busy} progress={latest('seo')?.progress || ''} onMake={() => setConfirm('seo')} />}

    {stage('images').state === 'done' && <VideoSection project={project} canEdit={canEdit} busy={busy} progress={latest('assembly')?.progress || ''}
      burn={burn} onBurn={setBurn} onMake={() => setConfirm('assembly')} />}

    {stage('final_review').state !== 'pending' && <div className="stack" aria-label="Final review">
      <strong>11 · Final review</strong>
      <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
        <li>Title: “{project.title}”</li>
        <li>Script: {project.record.script?.words.toLocaleString()} words, approved</li>
        <li>Voice: {project.record.voiceover?.voice} · {Math.round((project.record.voiceover?.total_seconds ?? 0) / 60 * 10) / 10} minutes</li>
        <li>Images: {(project.record.scenes ?? []).filter(s => s.image).length} of {project.record.scenes?.length ?? 0} scenes</li>
        <li>Thumbnail: {project.record.thumbnail?.chosen ? `option ${project.record.thumbnail.chosen} “${project.record.thumbnail.options[project.record.thumbnail.chosen - 1]?.caption}”` : 'not chosen yet'}</li>
        <li>SEO package: {project.record.seo ? `${project.record.seo.tags.length} tags, ${project.record.seo.chapters.length} chapters` : 'not written yet'}</li>
        <li>Video: {project.record.video ? `${Math.round(project.record.video.seconds / 6) / 10} minutes, 1080p` : '—'}</li>
      </ul>
      {stage('final_review').state === 'needs_approval' && canEdit && <div className="row" style={{ gap: 8 }}>
        <button className="button button-primary" disabled={busy || !project.record.thumbnail?.chosen || !project.record.seo}
          onClick={() => void approve('final_review', 'Approved. You can upload it to YouTube now.')}>Approve the final video</button>
        {(!project.record.thumbnail?.chosen || !project.record.seo) && <span className="small muted">Choose a thumbnail and write the SEO package first.</span>}
      </div>}
      {stage('final_review').state === 'needs_approval' && <LaunchPlan project={project} />}
    </div>}

    {stage('final_review').state === 'approved' && <PublishSection project={project} canEdit={canEdit} busy={busy} progress={latest('publish')?.progress || ''}
      onPublish={input => act(async () => { const r = await api.startCreatorJob(project.id, { kind: 'publish', ...input, version: project.version }); setJobs(current => [r.job, ...current]); return r.project },
        'Uploading to YouTube. The link appears here when it is done.')} />}

    <div className="stack" aria-label="History">
      <strong>History</strong>
      {events.length ? <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 4 }}>
        {events.map((event, index) => <li key={index} className="small"><When value={event.created_at} /> · {EVENT_LABEL[event.kind] ?? event.kind}{event.stage ? ` (${event.stage.replace('_', ' ')})` : ''}{event.actor.startsWith('worker:') ? ' · by the worker' : ''}</li>)}
      </ul> : <p className="small muted">Nothing yet.</p>}
    </div>

    {confirm === 'research' && <Confirm title="Research this topic?" confirmLabel="Research it" busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => void startJob('research')}>
      <p>The open-source model suggests angles, the questions viewers ask, points to cover, search keywords and eight titles, each scored. About a minute.
        It suggests only: you still pick or type the title. It isn't YouTube search data.</p>
    </Confirm>}
    {confirm === 'script' && <Confirm title="Write the script with AI?" confirmLabel="Write the script" busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => void startJob('script')}>
      <p>About {target.toLocaleString()} words in {model.chapters(project.duration_minutes)} chapters, following {channel?.name ?? 'the channel'}'s instructions. It takes about {scriptMinutes}–{scriptMinutes * 2} minutes on the open-source model; nothing is approved until you approve it.
        {project.record.script ? ' The current script is replaced.' : ''}</p>
    </Confirm>}
    {confirm === 'voiceover' && <Confirm title="Make the voiceover?" confirmLabel="Make the voiceover" busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => void startJob('voiceover')}>
      <p>Voice {voice || channel?.default_voice} at {speed.toFixed(2)}× reads the approved script, chapter by chapter (about {project.record.script?.estimated_minutes ?? project.duration_minutes} minutes of audio, a few minutes to make).</p>
    </Confirm>}
    {confirm === 'scenes' && <Confirm title="Plan the scenes?" confirmLabel="Plan the scenes" busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => void startJob('scenes')}>
      <p>The approved narration is cut into scenes of about 10 seconds (every word, in order), each with what is seen and an image prompt. Recurring characters get one fixed look. About a minute per chapter.</p>
    </Confirm>}
    {confirm === 'images' && <Confirm title={`Make ${project.record.scenes?.length ?? 0} images?`} confirmLabel="Make the images" busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => void startJob('images')}>
      <p>One 16:9 image per scene, in {channel?.name ?? 'the channel'}'s look: about {Math.max(1, Math.round((project.record.scenes?.length ?? 0) * 7 / 60))} minutes on ghana's GPU. Images already made are replaced.</p>
    </Confirm>}
    {confirm === 'seo' && <Confirm title="Write the SEO package?" confirmLabel="Write it" busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => void startJob('seo')}>
      <p>Description, keywords, tags, hashtags, pinned comment, playlist, end screen and cards, from the script itself; chapters from the recorded narration. About a minute.</p>
    </Confirm>}
    {confirm === 'assembly' && <Confirm title="Make the video?" confirmLabel="Make the video" busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => void startJob('assembly')}>
      <p>1080p with a slow zoom or pan on every scene, each on screen for exactly its narration{burn ? ', subtitles in the picture' : ''}, plus a captions file. A few minutes on ghana.</p>
    </Confirm>}
    {confirm === 'thumbnail' && <Confirm title="Make thumbnail options?" confirmLabel="Make thumbnails" busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => void startJob('thumbnail')}>
      <p>Three concepts from the title and script: each with a short caption that isn't the title, drawn large on a 1280×720 image. You choose one.</p>
    </Confirm>}
  </div>
}

function useVisible<T extends Element>(): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const element = ref.current
    if (!element || visible) return
    const observer = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) setVisible(true) }, { rootMargin: '300px' })
    observer.observe(element)
    return () => observer.disconnect()
  }, [visible])
  return [ref, visible]
}

/** An image of this project, fetched with the user's token once it scrolls into view. */
function ProjectImage({ projectId, name, alt, stamp }: { projectId: string; name: string; alt: string; stamp: string }) {
  const { api } = useSession()
  const [ref, visible] = useVisible<HTMLDivElement>()
  const [url, setUrl] = useState('')
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!visible) return
    let active = true; let made = ''
    api.creatorImage(projectId, name).then(u => { made = u; if (active) setUrl(u); else URL.revokeObjectURL(u) }, () => active && setFailed(true))
    return () => { active = false; if (made) URL.revokeObjectURL(made) }
  }, [api, projectId, name, visible, stamp])
  return <div ref={ref} style={{ aspectRatio: '16 / 9', background: 'var(--surface-2, #f1f1ee)', borderRadius: 6, overflow: 'hidden', display: 'grid', placeItems: 'center' }}>
    {url ? <img src={url} alt={alt} style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <span className="small muted">{failed ? 'Not loaded' : 'Loading…'}</span>}
  </div>
}

function ScenesSection({ project, canEdit, busy, running, onPlan, onImages, onRemake, onEdit }: {
  project: CreatorProject; canEdit: boolean; busy: boolean; running: string; onPlan: () => void; onImages: () => void
  onRemake: (n: number) => void; onEdit: (n: number, prompt: string, visual: string) => void
}) {
  const { api, notify } = useSession()
  const scenesStage = project.stages.find(s => s.key === 'scenes')!, imagesStage = project.stages.find(s => s.key === 'images')!
  const scenes = project.record.scenes ?? []
  const [editing, setEditing] = useState<{ n: number; prompt: string; visual: string } | null>(null)
  const working = scenesStage.state === 'running' || imagesStage.state === 'running'
  const total = scenes.reduce((sum, s) => sum + s.seconds, 0)
  async function zip() {
    try { const url = await api.creatorScenesZip(project.id); const a = document.createElement('a'); a.href = url; a.download = `${project.title || 'scenes'}-images.zip`; a.click(); window.setTimeout(() => URL.revokeObjectURL(url), 15000) }
    catch (error) { notify('error', errorText(error)) }
  }
  return <div className="stack" aria-label="Scenes and images">
    <strong>6–7 · Scenes and images</strong>
    {scenesStage.state === 'failed' && <Note tone="failed">The scenes couldn't be planned: {scenesStage.note}</Note>}
    {imagesStage.state === 'failed' && <Note tone="failed">The images couldn't be made: {imagesStage.note}</Note>}
    {working && <Note>{scenesStage.state === 'running' ? 'Planning the scenes' : 'Making the images'}… {running || 'queued'}. This page updates by itself.</Note>}
    {canEdit && !working && <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
      <button className="button button-primary" disabled={busy} onClick={onPlan}><Sparkles size={14} /> {scenes.length ? 'Plan the scenes again' : 'Plan the scenes'}</button>
      {scenes.length > 0 && <button className="button button-primary" disabled={busy} onClick={onImages}><Sparkles size={14} /> {scenes.some(s => s.image) ? 'Make all images again' : `Make ${scenes.length} images`}</button>}
      {scenes.some(s => s.image) && <button className="button button-secondary" onClick={() => void zip()}><Download size={14} /> All images (ZIP)</button>}
    </div>}
    {(project.record.characters?.length ?? 0) > 0 && <p className="small"><strong>Characters:</strong> {project.record.characters!.map(c => `${c.name} (${c.look})`).join(' · ')}</p>}
    {scenes.length > 0 && <p className="small muted">{scenes.length} scenes · {Math.floor(total / 60)}:{String(Math.round(total % 60)).padStart(2, '0')} · {imagesStage.note}</p>}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
      {scenes.map((scene: CreatorScene) => <div key={scene.number} className="stack" style={{ border: '1px solid var(--border, #e5e5e0)', borderRadius: 8, padding: 10, gap: 6 }}>
        {scene.image ? <ProjectImage projectId={project.id} name={`scene-${String(scene.number).padStart(3, '0')}`} alt={scene.visual} stamp={scene.image.made_at} />
          : <div style={{ aspectRatio: '16 / 9', background: 'var(--surface-2, #f1f1ee)', borderRadius: 6, display: 'grid', placeItems: 'center' }}><span className="small muted">No image yet</span></div>}
        <span className="small"><strong>Scene {scene.number}</strong> · chapter {scene.chapter} · {scene.seconds.toFixed(1)} s{scene.image?.stale ? ' · prompt changed, remake the image' : ''}</span>
        <span className="small muted">“{scene.narration}”</span>
        {editing?.n === scene.number ? <>
          <textarea rows={2} value={editing.visual} maxLength={600} onChange={e => setEditing({ ...editing, visual: e.target.value })} aria-label="What is seen" />
          <textarea rows={3} value={editing.prompt} maxLength={600} onChange={e => setEditing({ ...editing, prompt: e.target.value })} aria-label="Image prompt" />
          <div className="row" style={{ gap: 6 }}>
            <button className="button button-primary small" disabled={busy || editing.prompt.trim().length < 3} onClick={() => { onEdit(scene.number, editing.prompt.trim(), editing.visual.trim()); setEditing(null) }}>Save</button>
            <button className="button button-secondary small" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </> : <>
          <span className="small">{scene.visual} <span className="muted">({scene.camera}; {scene.lighting})</span></span>
          {canEdit && !working && <div className="row" style={{ gap: 6 }}>
            <button className="button button-secondary small" onClick={() => setEditing({ n: scene.number, prompt: scene.prompt, visual: scene.visual })}>Edit prompt</button>
            {scene.image && <button className="button button-secondary small" disabled={busy} onClick={() => onRemake(scene.number)}><RotateCcw size={12} /> Remake image</button>}
          </div>}
        </>}
      </div>)}
    </div>
  </div>
}

function ScoreBar({ score }: { score: number }) {
  const tone = score >= 75 ? 'var(--done)' : score >= 55 ? 'var(--attention)' : 'var(--failed)'
  return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }} aria-label={`score ${score} of 100`}>
    <span style={{ width: 64, height: 6, borderRadius: 3, background: 'var(--line)', overflow: 'hidden' }}>
      <span style={{ display: 'block', width: `${score}%`, height: '100%', background: tone }} /></span>
    <strong className="small">{score}</strong></span>
}

function ScoreParts({ parts }: { parts: { name: string; points: number; max: number; note: string }[] }) {
  return <ul className="small muted" style={{ margin: '4px 0 0', paddingLeft: 18 }}>
    {parts.map(p => <li key={p.name}>{p.name}: {p.points}/{p.max} · {p.note}</li>)}</ul>
}

function ResearchPanel({ research, current, canPick, onPick }: { research: CreatorResearch; current: string; canPick: boolean; onPick: (title: string) => void }) {
  const [open, setOpen] = useState<string | null>(null)
  return <div className="stack" aria-label="Research" style={{ gap: 10 }}>
    <p className="small muted">{research.note} Researched by {research.model} <When value={research.made_at} />.</p>
    <div className="form-grid">
      <div><span className="eyebrow">Angles</span><ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
        {research.angles.map(a => <li key={a.angle}><strong>{a.angle}</strong> · {a.why}</li>)}</ul></div>
      <div><span className="eyebrow">What viewers ask</span><ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
        {research.audience_questions.map(q => <li key={q}>{q}</li>)}</ul></div>
      <div><span className="eyebrow">Must cover</span><ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
        {research.must_cover.map(q => <li key={q}>{q}</li>)}</ul></div>
      <div><span className="eyebrow">Avoid</span><ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
        {research.avoid.map(q => <li key={q}>{q}</li>)}</ul>
        <span className="eyebrow">Search keywords</span><p className="small" style={{ margin: 0 }}>{[...research.keywords.primary, ...research.keywords.secondary].join(' · ')}</p></div>
    </div>
    <span className="eyebrow">Title ideas, best first</span>
    <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 6 }}>
      {research.titles.map(t => <li key={t.title} className="row" style={{ gap: 10, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <ScoreBar score={t.score} />
        <span style={{ flex: 1, minWidth: 220 }}>{t.title}{t.title.toLowerCase() === current.toLowerCase() && <span className="small muted"> · current</span>}
          <button className="button button-quiet small" style={{ marginLeft: 8 }} onClick={() => setOpen(open === t.title ? null : t.title)}>{open === t.title ? 'Hide why' : 'Why this score'}</button>
          {open === t.title && <>{t.why && <p className="small muted" style={{ margin: '2px 0 0' }}>{t.why}</p>}<ScoreParts parts={t.parts} /></>}</span>
        {canPick && t.title.toLowerCase() !== current.toLowerCase() && <button className="button button-secondary" onClick={() => onPick(t.title)}>Use this title</button>}
      </li>)}
    </ul>
    {research.current_title && <p className="small">Your own title “{research.current_title.title}” scores <strong>{research.current_title.score}</strong>{research.current_title.why ? ` · ${research.current_title.why}` : ''}</p>}
  </div>
}

function LaunchPlan({ project, onPickTime }: { project: CreatorProject; onPickTime?: (at: string) => void }) {
  const { api } = useSession()
  const [plan, setPlan] = useState<CreatorLaunch | null>(null)
  const [failed, setFailed] = useState('')
  useEffect(() => {
    let live = true
    api.creatorLaunch(project.id).then(p => { if (live) { setPlan(p); setFailed('') } }, e => { if (live) setFailed(errorText(e)) })
    return () => { live = false }
  }, [api, project.id, project.version])
  if (failed) return <p className="small muted">The launch plan didn't load: {failed}</p>
  if (!plan) return <p className="small muted">Loading the launch plan…</p>
  const ready = plan.checklist.filter(c => c.ok).length
  return <div className="stack" aria-label="Launch plan" style={{ gap: 8 }}>
    <span className="eyebrow">Launch plan · {ready} of {plan.checklist.length} ready</span>
    {plan.title_score && <div><span className="small">Title score </span><ScoreBar score={plan.title_score.score} />
      {!plan.title_score.rated_by_model && <span className="small muted"> · checks only; research the topic for the model's clarity and curiosity ratings</span>}
      <ScoreParts parts={plan.title_score.parts} /></div>}
    <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 2 }}>
      {plan.checklist.map(c => <li key={c.key} className="small">{c.ok ? <Check size={13} /> : <X size={13} />} {c.label} <span className="muted">· {c.note}</span></li>)}
    </ul>
    <div><span className="small">Good times to publish for {plan.times.audience}:</span>
      <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
        {plan.times.slots.map(s => onPickTime
          ? <button key={s.at} className="button button-secondary" title={s.why} onClick={() => onPickTime(s.at)}>{s.local}</button>
          : <span key={s.at} className="small" title={s.why} style={{ border: '1px solid var(--line)', borderRadius: 6, padding: '2px 8px' }}>{s.local}</span>)}
      </div><p className="small muted" style={{ margin: '4px 0 0' }}>{plan.times.note}{onPickTime ? ' Click one to schedule it.' : ''}</p></div>
    {plan.baseline ? <p className="small">Your last {plan.baseline.videos} videos: typically {plan.baseline.typical_range[0].toLocaleString()}–{plan.baseline.typical_range[1].toLocaleString()} views
      (median {plan.baseline.median_views.toLocaleString()}, about {plan.baseline.median_views_per_day.toLocaleString()} a day); best “{plan.baseline.best.title}” with {plan.baseline.best.views.toLocaleString()}.
      <span className="muted"> {plan.baseline.note}</span></p>
      : <p className="small muted">{plan.baseline_note}</p>}
  </div>
}

function PublishSection({ project, canEdit, busy, progress, onPublish }: {
  project: CreatorProject; canEdit: boolean; busy: boolean; progress: string
  onPublish: (input: { privacy: 'private' | 'unlisted' | 'public'; publish_at: string; made_for_kids: boolean }) => Promise<void>
}) {
  const { api, notify } = useSession()
  const [link, setLink] = useState<CreatorYouTube | null>(null)
  const [privacy, setPrivacy] = useState<'private' | 'unlisted' | 'public'>('private')
  const [when, setWhen] = useState('')
  const [tooSoon, setTooSoon] = useState(false)
  const [kids, setKids] = useState<'' | 'yes' | 'no'>('')
  const [confirm, setConfirm] = useState(false)
  const [working, setWorking] = useState(false)
  const stage = project.stages.find(s => s.key === 'publishing')!
  const done = project.record.youtube
  const channelId = project.channel_id

  const load = useCallback(() => { api.creatorYouTube(channelId).then(setLink, () => setLink(null)) }, [api, channelId])
  useEffect(() => { const t = window.setTimeout(load, 0); return () => window.clearTimeout(t) }, [load])

  async function connect() {
    setWorking(true)
    try {
      const { url } = await api.connectCreatorYouTube(channelId)
      try { window.sessionStorage.setItem('creators.youtube.return', project.id) } catch { /* only used to come back to this video */ }
      window.location.assign(url)
    } catch (error) { notify('error', errorText(error)); setWorking(false) }
  }
  async function disconnect() {
    setWorking(true)
    try { await api.disconnectCreatorYouTube(channelId); notify('success', 'YouTube disconnected; Google\'s access for VolumeOS is revoked.'); load() }
    catch (error) { notify('error', errorText(error)) } finally { setWorking(false) }
  }
  const scheduled = when ? new Date(when) : null
  const badTime = Boolean(scheduled && (Number.isNaN(scheduled.getTime()) || tooSoon))
  const info = link?.youtube
  const pickTime = (at: string) => {
    const d = new Date(at), pad = (n: number) => String(n).padStart(2, '0')
    setWhen(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`); setTooSoon(false)
  }

  return <div className="stack" aria-label="Publishing">
    <strong>12 · YouTube publishing</strong>
    {!done && <LaunchPlan project={project} onPickTime={info && canEdit && stage.state !== 'running' ? pickTime : undefined} />}
    {done && <Note tone="done">Uploaded to {done.channel} as {done.privacy}{done.publish_at ? `, goes public ${new Date(done.publish_at).toLocaleString()}` : ''}.{' '}
      <a href={done.url} target="_blank" rel="noreferrer">{done.url} <ExternalLink size={12} /></a> · <a href={done.studio_url} target="_blank" rel="noreferrer">Open in YouTube Studio</a>
      {done.notes.length > 0 && <> · Not everything went through: {done.notes.join('; ')}</>}</Note>}
    {stage.state === 'running' && <Note>Uploading… {progress || 'queued'}.</Note>}
    {stage.state === 'failed' && <Note tone="failed">The upload didn't finish: {stage.note}</Note>}
    {link && !link.configured && <p className="small muted">Uploading to YouTube isn't switched on for this workspace yet. Download the MP4, captions and description above and upload them in YouTube Studio meanwhile.</p>}
    {link?.configured && !link.connected && <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      {canEdit ? <button className="button button-primary" disabled={working} onClick={() => void connect()}><Link2 size={14} /> Connect YouTube</button> : null}
      <span className="small muted">Sign in with the Google account that owns the channel. VolumeOS can then upload videos, set thumbnails and add captions; nothing is posted without your click.</span>
    </div>}
    {info && <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <span>Linked to <strong>{info.title}</strong>{info.handle ? ` (${info.handle})` : ''}{info.subscribers != null ? ` · ${info.subscribers.toLocaleString()} subscribers` : ''}</span>
      {canEdit && <button className="button button-secondary" disabled={working} onClick={() => void disconnect()}><Unlink size={14} /> Disconnect</button>}
    </div>}
    {info && canEdit && !done && stage.state !== 'running' && <>
      <div className="form-grid">
        <label className="field"><span>Visibility</span>
          <select value={privacy} disabled={Boolean(when)} onChange={e => setPrivacy(e.target.value as typeof privacy)}>
            <option value="private">Private (only you)</option><option value="unlisted">Unlisted (anyone with the link)</option><option value="public">Public</option>
          </select>{when && <small>Scheduled videos stay private until the time, then go public.</small>}</label>
        <label className="field"><span>Schedule (optional)</span><input type="datetime-local" value={when} onChange={e => { setWhen(e.target.value); setTooSoon(Boolean(e.target.value) && new Date(e.target.value).getTime() < Date.now() + 15 * 60000) }} />
          {badTime && <small>Pick a time at least 15 minutes from now.</small>}</label>
      </div>
      <fieldset className="field"><span>Is this video made for kids? (YouTube requires an answer)</span>
        <label className="row" style={{ gap: 6 }}><input type="radio" name="kids" checked={kids === 'no'} onChange={() => setKids('no')} /> No, it's not made for kids</label>
        <label className="row" style={{ gap: 6 }}><input type="radio" name="kids" checked={kids === 'yes'} onChange={() => setKids('yes')} /> Yes, it's made for kids</label>
      </fieldset>
      <div className="row" style={{ gap: 8 }}>
        <button className="button button-primary" disabled={busy || !kids || badTime} onClick={() => setConfirm(true)}><Upload size={14} /> Upload to YouTube</button>
      </div>
    </>}
    {confirm && <Confirm title="Upload to YouTube?" confirmLabel="Upload" busy={busy} onCancel={() => setConfirm(false)}
      onConfirm={() => void onPublish({ privacy: when ? 'private' : privacy, publish_at: scheduled && !badTime ? scheduled.toISOString() : '', made_for_kids: kids === 'yes' }).then(() => setConfirm(false))}>
      <p>“{project.title}” goes to {info?.title} as {when ? `private, public at ${scheduled?.toLocaleString()}` : privacy}, with the chosen thumbnail, the description, tags and chapters, and the captions.
        {project.record.video ? ` About ${Math.max(1, Math.round(project.record.video.bytes / 1e6 / 20))} minutes to upload.` : ''}</p>
    </Confirm>}
  </div>
}

function Copy({ text, label = 'Copy' }: { text: string; label?: string }) {
  const { notify } = useSession()
  return <button className="button button-secondary small" onClick={() => { void navigator.clipboard.writeText(text).then(() => notify('success', 'Copied.'), () => notify('error', "Couldn't copy.")) }}>{label}</button>
}

function SeoSection({ project, canEdit, busy, progress, onMake }: { project: CreatorProject; canEdit: boolean; busy: boolean; progress: string; onMake: () => void }) {
  const stage = project.stages.find(s => s.key === 'seo')!
  const seo = project.record.seo
  return <div className="stack" aria-label="SEO package">
    <strong>9 · SEO package</strong>
    {stage.state === 'failed' && <Note tone="failed">The SEO package couldn't be written: {stage.note}</Note>}
    {stage.state === 'running' && <Note>Writing the SEO package… {progress || 'queued'}.</Note>}
    {canEdit && stage.state !== 'running' && <div><button className="button button-primary" disabled={busy} onClick={onMake}><Sparkles size={14} /> {seo ? 'Write it again' : 'Write the SEO package'}</button></div>}
    {seo && <div className="stack" style={{ gap: 10 }}>
      <div className="stack" style={{ gap: 4 }}><span className="small"><strong>Description</strong> (with chapters and hashtags) <Copy text={seo.full_description} /></span>
        <pre className="small" style={{ whiteSpace: 'pre-wrap', margin: 0, padding: 10, background: 'var(--surface-2, #f1f1ee)', borderRadius: 6 }}>{seo.full_description}</pre></div>
      <span className="small"><strong>Tags</strong> ({seo.tags.join(', ').length} of 500 characters) <Copy text={seo.tags.join(', ')} /><br />{seo.tags.join(', ')}</span>
      <span className="small"><strong>Hashtags</strong> {seo.hashtags.join(' ')}</span>
      <span className="small"><strong>Keywords</strong> — primary: {seo.primary_keywords.join(', ')}; secondary: {seo.secondary_keywords.join(', ')}; long-tail: {seo.long_tail_keywords.join(', ')}. <span className="muted">{seo.keyword_note}</span></span>
      <span className="small"><strong>Pinned comment</strong> {seo.pinned_comment} <Copy text={seo.pinned_comment} /></span>
      <span className="small"><strong>Playlist</strong> {seo.playlist} · <strong>End screen</strong> {seo.end_screen}</span>
      {seo.cards.length > 0 && <span className="small"><strong>Cards</strong> {seo.cards.map(c => `${c.at}: ${c.suggest}`).join(' · ')}</span>}
    </div>}
  </div>
}

function VideoSection({ project, canEdit, busy, progress, burn, onBurn, onMake }: {
  project: CreatorProject; canEdit: boolean; busy: boolean; progress: string; burn: boolean; onBurn: (b: boolean) => void; onMake: () => void
}) {
  const { api, notify } = useSession()
  const stage = project.stages.find(s => s.key === 'assembly')!
  const video = project.record.video
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(false)
  useEffect(() => () => { if (url) URL.revokeObjectURL(url) }, [url])
  async function load() {
    setLoading(true)
    try { setUrl(await api.creatorVideo(project.id, 'video.mp4')) } catch (error) { notify('error', errorText(error)) } finally { setLoading(false) }
  }
  async function captions() {
    try { const u = await api.creatorVideo(project.id, 'captions.srt'); const a = document.createElement('a'); a.href = u; a.download = `${project.title || 'captions'}.srt`; a.click(); window.setTimeout(() => URL.revokeObjectURL(u), 10000) }
    catch (error) { notify('error', errorText(error)) }
  }
  return <div className="stack" aria-label="Video">
    <strong>10 · Video</strong>
    {stage.state === 'failed' && <Note tone="failed">The video couldn't be made: {stage.note}</Note>}
    {stage.state === 'running' && <Note>Making the video… {progress || 'queued'}.</Note>}
    {canEdit && stage.state !== 'running' && <div className="row" style={{ gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
      <button className="button button-primary" disabled={busy} onClick={onMake}><Clapperboard size={14} /> {video ? 'Make the video again' : 'Make the video'}</button>
      <label className="small row" style={{ gap: 6, alignItems: 'center' }}><input type="checkbox" checked={burn} onChange={e => onBurn(e.target.checked)} /> Subtitles in the picture (a captions file is always made)</label>
    </div>}
    {video && stage.state === 'done' && <div className="stack" style={{ gap: 8 }}>
      <span className="small muted">{Math.floor(video.seconds / 60)}:{String(Math.round(video.seconds % 60)).padStart(2, '0')} · 1920×1080 · {(video.bytes / 1e6).toFixed(0)} MB{video.burned_subtitles ? ' · subtitles in the picture' : ''} · made <When value={video.made_at} /></span>
      {url ? <video controls src={url} style={{ width: '100%', maxWidth: 960, borderRadius: 8, background: '#000' }} aria-label="The finished video" />
        : <div><button className="button button-secondary" disabled={loading} onClick={() => void load()}><Play size={14} /> {loading ? 'Loading the video…' : 'Watch the video'}</button></div>}
      <div className="row" style={{ gap: 8 }}>
        {url && <a className="button button-secondary small" href={url} download={`${project.title || 'video'}.mp4`}><Download size={14} /> MP4</a>}
        <button className="button button-secondary small" onClick={() => void captions()}><Download size={14} /> Captions (.srt)</button>
      </div>
    </div>}
  </div>
}

function ThumbnailSection({ project, canEdit, busy, progress, onMake, onChoose }: {
  project: CreatorProject; canEdit: boolean; busy: boolean; progress: string; onMake: () => void; onChoose: (n: number) => void
}) {
  const stage = project.stages.find(s => s.key === 'thumbnail')!
  const thumb = project.record.thumbnail
  return <div className="stack" aria-label="Thumbnail">
    <strong>8 · Thumbnail</strong>
    {stage.state === 'failed' && <Note tone="failed">The thumbnails couldn't be made: {stage.note}</Note>}
    {stage.state === 'running' && <Note>Making thumbnails… {progress || 'queued'}.</Note>}
    {canEdit && stage.state !== 'running' && <div><button className="button button-primary" disabled={busy} onClick={onMake}><Sparkles size={14} /> {thumb ? 'Make new options' : 'Make thumbnail options'}</button></div>}
    {thumb && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
      {thumb.options.map((option, index) => <div key={option.key} className="stack" style={{ border: `2px solid ${thumb.chosen === index + 1 ? 'var(--done, #15803d)' : 'var(--border, #e5e5e0)'}`, borderRadius: 8, padding: 8, gap: 6 }}>
        <ProjectImage projectId={project.id} name={`thumb-${index + 1}`} alt={option.caption} stamp={thumb.made_at} />
        <span className="small"><strong>“{option.caption}”</strong> · {option.angle}</span>
        {thumb.chosen === index + 1 ? <span className="small" style={{ color: 'var(--done, #15803d)' }}><Check size={12} /> Chosen</span>
          : canEdit && <button className="button button-secondary small" disabled={busy} onClick={() => onChoose(index + 1)}>Choose this one</button>}
      </div>)}
    </div>}
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
