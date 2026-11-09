import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Image as ImageIcon, Plus, RefreshCw } from 'lucide-react'
import { useSession, errorText } from '../state/session-context'
import { useOperations } from '../state/operations-context'
import { allowed } from '../lib/access'
import { fromStudio } from '../lib/workflows'
import type { StudioAsset, StudioSnapshot } from '../lib/types'
import { Confirm, Empty, Field, Modal, Note, PageHead, RoleNote, StageSteps, Submit, When, WorkStatus } from '../components/ui'
import { dateTime } from '../lib/format'

const ID_PATTERN = '[a-zA-Z0-9][a-zA-Z0-9._:\\-]{0,127}'

/** Offer 06 Shopfront Studio: generated product images are QA'd by a person before conversion and delivery. */
export function StudioPage() {
  const { api, roles, notify } = useSession()
  const { refresh: refreshAll } = useOperations()
  const { id } = useParams()
  const navigate = useNavigate()
  const [packs, setPacks] = useState<StudioSnapshot[]>([])
  const [load, setLoad] = useState<{ state: 'loading' | 'ok' | 'error'; error: string | null }>({ state: 'loading', error: null })
  const [assets, setAssets] = useState<{ packId: string; manifest: string; items: StudioAsset[]; error: string | null; loading: boolean }>({ packId: '', manifest: '', items: [], error: null, loading: false })
  const [loaded, setLoaded] = useState<Record<string, boolean>>({})
  const [imageError, setImageError] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [modal, setModal] = useState(false)
  const [confirm, setConfirm] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const feed = await api.studioPacks()
      setPacks([...feed.packs].sort((a, b) => b.updated_at.localeCompare(a.updated_at)))
      setLoad({ state: 'ok', error: null })
    } catch (error) { setLoad({ state: 'error', error: errorText(error) }) }
  }, [api])
  useEffect(() => { const first = window.setTimeout(() => void refresh(), 0); return () => window.clearTimeout(first) }, [refresh])

  const pack = packs.find(item => item.pack_id === id) ?? packs[0]
  const item = pack ? fromStudio(pack) : null

  useEffect(() => {
    let active = true
    // Deferred like the other feeds: reset and load in one step when the selected pack or its manifest changes.
    const run = window.setTimeout(() => {
      if (!active) return
      setLoaded({}); setImageError(false)
      if (!pack || pack.status !== 'awaiting_qa' || !pack.manifest_sha256 || pack.assets <= 0) { setAssets({ packId: '', manifest: '', items: [], error: null, loading: false }); return }
      setAssets(current => ({ ...current, loading: true, error: null }))
      api.studioAssets(pack.pack_id).then(
        response => active && setAssets({ packId: response.pack_id, manifest: response.manifest_sha256, items: response.assets, error: null, loading: false }),
        error => active && setAssets({ packId: '', manifest: '', items: [], error: errorText(error), loading: false }),
      )
    }, 0)
    return () => { active = false; window.clearTimeout(run) }
  }, [api, pack])

  const manifestReady = Boolean(pack && assets.packId === pack.pack_id && assets.manifest === pack.manifest_sha256 && assets.items.length === pack.assets && assets.items.length > 0 && !assets.error)
  const reviewReady = manifestReady && !imageError && assets.items.every(asset => loaded[asset.id])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    try { await action() } catch (error) { notify('error', errorText(error)) } finally { setBusy(false) }
  }

  function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const packId = String(data.get('pack_id')).trim()
    void run(async () => {
      await api.startStudioPack(Object.fromEntries(['pack_id', 'product_id', 'tier', 'source_image', 'checkpoint', 'materials', 'dimensions', 'price_text'].map(key => [key, String(data.get(key) ?? '').trim()])))
      setModal(false); await refresh(); void refreshAll(); navigate(`/services/studio/${encodeURIComponent(packId)}`)
      notify('success', 'Studio pack started. Images will wait for QA before anything is delivered.')
    })
  }

  function review(decision: 'approve' | 'reject') {
    if (!pack) return
    void run(async () => {
      await api.reviewStudioPack(pack.pack_id, decision, reason.trim(), assets.manifest)
      setConfirm(false); setReason(''); await refresh(); void refreshAll()
      notify('success', decision === 'approve' ? 'QA approved. Conversion and delivery happen next; the manifest is checked again before delivery.' : 'Pack rejected. Nothing will be delivered.')
    })
  }

  return <div className="page">
    <PageHead eyebrow="Services · 06 Shopfront Studio" title="Shopfront Studio"
      description="Generated product images stay in QA storage until a person approves them. Approved images become 1080×1080 WhatsApp and 1000×1000 Jumia files."
      actions={<>
        <button className="button button-secondary" onClick={() => void refresh()}><RefreshCw size={14} /> Refresh</button>
        {allowed(roles, 'startStudioPack') && <button className="button button-primary" onClick={() => setModal(true)}><Plus size={15} /> New pack</button>}
      </>} />
    {load.state === 'error' && <Note tone="failed">The pack list didn't load: {load.error}</Note>}
    <div className="workspace-grid">
      <section className="queue-panel" aria-label="Studio packs">
        <div className="panel-heading"><h2>Packs</h2><span>{packs.length}</span></div>
        <div className="queue-list">{packs.map(snapshot => { const work = fromStudio(snapshot); return <button key={snapshot.pack_id} className={`queue-item ${snapshot.pack_id === pack?.pack_id ? 'selected' : ''}`} onClick={() => navigate(`/services/studio/${encodeURIComponent(snapshot.pack_id)}`)}>
          <span className="queue-item-top"><strong>{snapshot.product_id}</strong><WorkStatus item={work} /></span>
          <span style={{ textTransform: 'capitalize' }}>{snapshot.tier} · {snapshot.assets || 'no'} images</span><small><When value={snapshot.updated_at} /></small>
        </button> })}</div>
        {!packs.length && <Empty title={load.state === 'loading' ? 'Loading packs' : 'No packs yet'} />}
      </section>
      {pack && item ? <section className="review-panel" aria-label="Studio QA">
        <div className="review-header"><div><span className="eyebrow">Human QA</span><h2>{pack.product_id}</h2><p className="mono">{pack.pack_id}</p></div><WorkStatus item={item} /></div>
        <StageSteps item={item} />
        <div className="release-facts">
          <div className="fact"><span>Tier</span><strong style={{ textTransform: 'capitalize' }}>{pack.tier}</strong></div>
          <div className="fact"><span>Images</span><strong>{pack.assets || 'Not generated yet'}</strong></div>
          <div className="fact"><span>Started</span><strong>{dateTime(pack.started_at)}</strong></div>
          <div className="fact"><span>Manifest</span><strong className="mono">{pack.manifest_sha256 ? `${pack.manifest_sha256.slice(0, 12)}…` : '—'}</strong></div>
        </div>
        {assets.loading && <Note>Loading the current QA images…</Note>}
        {assets.error && <Note tone="failed">The QA images couldn't be loaded: {assets.error} Approval stays locked.</Note>}
        {assets.items.length > 0 && <div className="qa-gallery" aria-label="QA images">{assets.items.map((asset, index) => <a className="qa-asset" key={asset.id} href={asset.url} target="_blank" rel="noreferrer" aria-label={`Open image ${index + 1}`}>
          <img src={asset.url} alt={`${pack.product_id}, generated image ${index + 1}`} onLoad={() => setLoaded(current => ({ ...current, [asset.id]: true }))} onError={() => setImageError(true)} /><span>{String(index + 1).padStart(2, '0')}</span></a>)}</div>}
        {imageError && <Note tone="failed">At least one image didn't load in this browser, so the pack can't be approved from here. Rejecting is still possible.</Note>}
        {!assets.items.length && !assets.loading && !assets.error && <div className="asset-schema"><ImageIcon size={22} /><div><strong>{pack.status === 'generating' ? 'Images are being generated' : 'No images to review'}</strong><p>Images appear here when the pack reaches QA.</p></div></div>}
        {pack.status === 'awaiting_qa' && (allowed(roles, 'reviewStudioPack') ? <div className="approval-form">
          <label className="field"><span>Reason (required to reject)</span><textarea rows={2} value={reason} onChange={event => setReason(event.target.value)} maxLength={1000} placeholder="Describe a quality or brand issue" /></label>
          <div className="safety-actions">
            <button className="button button-primary" onClick={() => setConfirm(true)} disabled={busy || !reviewReady}>Approve images</button>
            <button className="button button-danger-quiet" onClick={() => review('reject')} disabled={busy || !manifestReady || !reason.trim()}>Reject pack</button>
            {!reviewReady && <span className="small muted">Approval unlocks once every image of the current manifest has loaded.</span>}
          </div>
        </div> : <RoleNote action="reviewStudioPack" />)}
      </section> : <section className="review-panel"><Empty title="Select a pack" /></section>}
    </div>

    {modal && <Modal title="New Studio pack" description="Generates images into QA storage only. Delivery needs a separate approval." onClose={() => setModal(false)} wide>
      <form onSubmit={start} className="form-stack">
        <div className="form-grid"><Field label="Pack id" name="pack_id" placeholder="pack-adire-tote" pattern={ID_PATTERN} required /><Field label="Product id" name="product_id" placeholder="adire-tote-01" pattern={ID_PATTERN} required /></div>
        <div className="form-grid">
          <label className="field"><span>Tier</span><select name="tier" defaultValue="growth"><option value="starter">Starter · 5 images</option><option value="growth">Growth · 7 images</option><option value="pro">Pro · 12 images</option></select></label>
          <Field label="Reviewed model checkpoint" name="checkpoint" placeholder="reviewed-model.safetensors" required />
        </div>
        <Field label="Source image key" name="source_image" placeholder="uploads/adire-tote.png" required />
        <div className="form-grid"><Field label="Materials" name="materials" placeholder="Adire cotton, leather handle" /><Field label="Dimensions" name="dimensions" placeholder="42 × 35 × 12 cm" /></div>
        <Field label="Price text" name="price_text" placeholder="₦28,500" />
        <Submit busy={busy} label="Start generation" />
      </form>
    </Modal>}
    {confirm && pack && <Confirm title={`Approve images for ${pack.product_id}?`} confirmLabel="Approve images" busy={busy} onCancel={() => setConfirm(false)} onConfirm={() => review('approve')}>
      <p>You checked all {pack.assets} images of manifest <span className="mono">{pack.manifest_sha256.slice(0, 16)}…</span>. They'll be converted and delivered next; the same manifest is checked again before delivery.</p>
    </Confirm>}
  </div>
}
