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

  // A link to a pack this business doesn't have says so; it never opens another pack (BB-04).
  const pack = packs.find(item => item.pack_id === id) ?? (id ? undefined : packs[0])
  const missing = Boolean(id && load.state === 'ok' && !pack)
  const item = pack ? fromStudio(pack) : null

  useEffect(() => {
    let active = true
    // Deferred like the other feeds: reset and load in one step when the selected pack or its manifest changes.
    const run = window.setTimeout(() => {
      if (!active) return
      setLoaded({}); setImageError(false)
      if (!pack || pack.status !== 'awaiting_qa' || !pack.manifest_sha256 || pack.assets <= 0) { setAssets({ packId: '', manifest: '', items: [], error: null, loading: false }); return }
      setAssets(current => ({ ...current, loading: true, error: null }))
      // The images come from this API (the storage server isn't public): fetch each with the user's token.
      api.studioAssets(pack.pack_id)
        .then(async response => ({ response, items: await Promise.all(response.assets.map(async asset => ({ ...asset, url: await api.blobUrl(asset.url) }))) }))
        .then(
          ({ response, items }) => {
            if (!active) { items.forEach(item => URL.revokeObjectURL(item.url)); return }
            blobs.push(...items.map(item => item.url))
            setAssets({ packId: response.pack_id, manifest: response.manifest_sha256, items, error: null, loading: false })
          },
          error => active && setAssets({ packId: '', manifest: '', items: [], error: errorText(error), loading: false }),
        )
    }, 0)
    const blobs: string[] = []
    return () => { active = false; window.clearTimeout(run); blobs.forEach(url => URL.revokeObjectURL(url)) }
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
    const photo = data.get('photo')
    void run(async () => {
      if (!(photo instanceof File) || !photo.size) throw new Error('Choose the product photo to start from.')
      if (photo.size > 10_000_000) throw new Error('The photo is larger than 10 MB.')
      const { source_image } = await api.uploadStudioPhoto(photo)
      await api.startStudioPack({ ...Object.fromEntries(['pack_id', 'product_id', 'tier', 'materials', 'dimensions', 'price_text', 'style'].map(key => [key, String(data.get(key) ?? '').trim()])), source_image })
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
    {!allowed(roles, 'startStudioPack') && <RoleNote action="startStudioPack" />}
    {missing && <Note tone="failed">There's no Studio pack “{id}” in this business. The link may be mistyped, or the pack belongs to another business.</Note>}
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
        {pack.status === 'delivered' && <DeliveredFiles packId={pack.pack_id} />}
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
      </section> : <section className="review-panel">{packs.length ? <Empty title="Select a pack" /> : <StudioHowItWorks canStart={allowed(roles, 'startStudioPack')} onStart={() => setModal(true)} />}</section>}
    </div>

    {modal && <Modal title="New Studio pack" description="Generates images into QA storage only. Delivery needs a separate approval." onClose={() => setModal(false)} wide>
      <form onSubmit={start} className="form-stack">
        <div className="form-grid"><Field label="Pack id" name="pack_id" placeholder="pack-adire-tote" pattern={ID_PATTERN} required /><Field label="Product id" name="product_id" placeholder="adire-tote-01" pattern={ID_PATTERN} required /></div>
        <div className="form-grid">
          <label className="field"><span>Tier</span><select name="tier" defaultValue="growth"><option value="starter">Starter · 5 images</option><option value="growth">Growth · 7 images</option><option value="pro">Pro · 12 images</option></select></label>
        </div>
        <label className="field"><span>Product photo</span><input type="file" name="photo" accept="image/png,image/jpeg,image/webp" required />
          <small>A clear photo of the product (PNG, JPEG or WebP, up to 10 MB). The pack keeps the product and changes the scene.</small></label>
        <label className="field"><span>Style notes (optional)</span><textarea name="style" rows={2} maxLength={300} placeholder="e.g. on a wooden table, warm morning light, plants in the background" />
          <small>How the photos should look. The product itself isn't changed.</small></label>
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

/** The approved pack's files: WhatsApp (1080 px) and Jumia (1000 px, white background) JPEGs, downloaded with the user's token. */
function DeliveredFiles({ packId }: { packId: string }) {
  const { api, notify } = useSession()
  const [files, setFiles] = useState<{ path: string; url: string }[] | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    api.studioDelivered(packId).then(result => { if (active) setFiles(result.files) }, error => { if (active) setFailure(errorText(error)) })
    return () => { active = false }
  }, [api, packId])
  async function download(file: { path: string; url: string }) {
    try {
      const url = await api.blobUrl(file.url)
      const link = document.createElement('a')
      link.href = url; link.download = `${packId}-${file.path.replace('/', '-')}`; link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 10000)
    } catch (error) { notify('error', errorText(error)) }
  }
  if (failure) return <Note tone="failed">The delivered files couldn't be listed: {failure}</Note>
  if (!files) return <Note>Loading the delivered files…</Note>
  return <div className="stack" aria-label="Delivered files">
    <strong>Delivered: {files.length} files</strong>
    {(['whatsapp', 'jumia'] as const).map(channel => <div key={channel} className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
      <span className="small muted" style={{ minWidth: 150 }}>{channel === 'whatsapp' ? 'WhatsApp (1080 px)' : 'Jumia (1000 px, white)'}</span>
      {files.filter(file => file.path.startsWith(channel + '/')).map((file, index) => <button key={file.path} className="button button-secondary small" onClick={() => void download(file)}>Image {index + 1}</button>)}
    </div>)}
  </div>
}

/** Shown before the first pack: the whole workflow, so nobody faces an empty page wondering what Studio does. */
function StudioHowItWorks({ canStart, onStart }: { canStart: boolean; onStart: () => void }) {
  const steps = [
    ['Upload a product photo', 'A clear photo of one product (PNG, JPEG or WebP, up to 10 MB), with optional style notes such as "on a wooden table, warm light".'],
    ['Images are generated', 'Studio keeps the product and restyles the photo: 5, 7 or 12 images depending on the tier. On this server it takes about 7–10 minutes.'],
    ['A person checks them', 'The images wait here for review. Approve the set, or reject it with a reason; nothing reaches the client before approval.'],
    ['Download the files', 'Approved images become 1080×1080 WhatsApp and 1000×1000 white-background Jumia JPEGs, ready to download.'],
  ]
  return <div className="stack" aria-label="How Studio works">
    <span className="eyebrow">How Shopfront Studio works</span>
    <ol className="plain-list" style={{ display: 'grid', gap: 12, paddingLeft: 0, listStyle: 'none' }}>
      {steps.map(([title, text], index) => <li key={title} className="row" style={{ gap: 12, alignItems: 'flex-start' }}>
        <strong className="mono" style={{ minWidth: 22 }}>{index + 1}</strong>
        <div><strong>{title}</strong><p className="small muted" style={{ margin: '2px 0 0' }}>{text}</p></div>
      </li>)}
    </ol>
    {canStart && <div><button className="button button-primary" onClick={onStart}><Plus size={15} /> Start your first pack</button></div>}
  </div>
}
