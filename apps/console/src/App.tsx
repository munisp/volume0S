import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type InputHTMLAttributes, type ReactNode } from 'react'
import type { User } from 'oidc-client-ts'
import {
  Activity, ArrowRight, BadgeCheck, Ban, Check, CheckCircle2, ChevronRight,
  CircleAlert, Clock3, CloudOff, Download, FileCheck2, Image as ImageIcon,
  LayoutDashboard, Loader2, LockKeyhole, LogIn, LogOut, PackageCheck, Plus,
  RefreshCw, Rocket, Send, Settings, ShieldCheck, ShoppingBag, Store,
  UserRoundCheck, Wifi, X, type LucideIcon,
} from 'lucide-react'
import { VolumeOSApi, ApiError } from './lib/api'
import { VolumeOSAuth } from './lib/auth'
import { defaultConfig, loadRuntimeConfig, oidcConfigured, type RuntimeConfig } from './lib/config'
import { previewBundles, previewCampaigns, previewOffers, previewPacks, previewTenant } from './lib/demo'
import type { Bundle, Campaign, Offer, PageId, StudioAsset, StudioPack, Tenant } from './lib/types'
import { usePwa } from './hooks/usePwa'

const navigation: { id: PageId; label: string; icon: LucideIcon }[] = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'onboarding', label: 'Onboarding', icon: UserRoundCheck },
  { id: 'campaigns', label: 'Campaigns', icon: Send },
  { id: 'studio', label: 'Studio QA', icon: ImageIcon },
  { id: 'offers', label: 'Offers', icon: ShoppingBag },
]

type ModalId = 'tenant' | 'workspace' | 'campaign' | 'pack' | 'subscription' | 'suspension' | 'suppression' | null
type Toast = { kind: 'success' | 'error' | 'info'; message: string }

function money(minor: number, currency = 'NGN') {
  return new Intl.NumberFormat('en-NG', { style: 'currency', currency, maximumFractionDigits: 0 }).format(minor / 100)
}

function titleCase(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase())
}

function formatWorkflowTime(value: string) {
  const timestamp = new Date(value)
  if (Number.isNaN(timestamp.getTime())) return 'Time unavailable'
  return new Intl.DateTimeFormat('en-NG', { dateStyle: 'medium', timeStyle: 'short' }).format(timestamp)
}

function App() {
  const [config, setConfig] = useState<RuntimeConfig>(defaultConfig)
  const [auth, setAuth] = useState<VolumeOSAuth | null>(null)
  const [user, setUser] = useState<User | null>(null)
  const [liveTenant, setLiveTenant] = useState<Tenant | null>(null)
  const [offers, setOffers] = useState<Offer[]>(previewOffers)
  const [bundles, setBundles] = useState<Bundle[]>(previewBundles)
  const [offersLive, setOffersLive] = useState(false)
  const [page, setPage] = useState<PageId>('overview')
  const [campaigns, setCampaigns] = useState<Campaign[]>(previewCampaigns)
  const [packs, setPacks] = useState<StudioPack[]>(previewPacks)
  const [selectedCampaign, setSelectedCampaign] = useState(previewCampaigns[0].id)
  const [selectedPack, setSelectedPack] = useState(previewPacks[0].id)
  const [modal, setModal] = useState<ModalId>(null)
  const [selectedOffer, setSelectedOffer] = useState<{ offer: string; tier: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)
  const [toast, setToast] = useState<Toast | null>(null)
  const [feedsLoading, setFeedsLoading] = useState(false)
  const [feedsError, setFeedsError] = useState<string | null>(null)
  const [qaAssets, setQaAssets] = useState<StudioAsset[]>([])
  const [qaPackId, setQaPackId] = useState('')
  const [qaManifest, setQaManifest] = useState('')
  const [qaLoading, setQaLoading] = useState(false)
  const [qaError, setQaError] = useState<string | null>(null)
  const [qaImageError, setQaImageError] = useState<string | null>(null)
  const [loadedQaAssets, setLoadedQaAssets] = useState<Record<string, boolean>>({})
  const [qaReloadToken, setQaReloadToken] = useState(0)
  const pwa = usePwa()

  const api = useMemo(() => new VolumeOSApi(
    config.coreApiUrl,
    config.orchestratorApiUrl,
    () => auth?.accessToken() ?? Promise.resolve(null),
  ), [config, auth])

  const tenant = liveTenant ?? (!user ? previewTenant : null)
  const authenticated = Boolean(user && config.coreApiUrl)
  const tenantConnected = Boolean(authenticated && liveTenant)
  const workflowFeedReady = tenantConnected && api.orchestratorConfigured && !feedsError
  const awaitingApprovals = campaigns.filter(item => item.status === 'awaiting_approval').length
    + packs.filter(item => item.status === 'awaiting_qa').length
  const currentPack = packs.find(item => item.id === selectedPack) ?? packs[0]
  const qaTargetId = currentPack?.id ?? ''
  const qaTargetManifest = currentPack?.manifestSha256 ?? ''
  const qaTargetAssetCount = currentPack?.assets ?? 0
  const qaManifestReady = Boolean(authenticated && currentPack && qaPackId === currentPack.id && qaManifest
    && qaManifest === currentPack.manifestSha256 && qaAssets.length === currentPack.assets && qaAssets.length > 0 && !qaError)
  const qaReviewReady = Boolean(qaManifestReady && !qaImageError && qaAssets.every(asset => loadedQaAssets[asset.id]))

  const refreshWorkflows = useCallback(async () => {
    if (!user || !liveTenant || !api.orchestratorConfigured) return
    setFeedsLoading(true)
    try {
      const [campaignFeed, studioFeed] = await Promise.all([api.campaigns(), api.studioPacks()])
      const nextCampaigns: Campaign[] = campaignFeed.campaigns.map(snapshot => ({
        id: snapshot.campaign_id, name: snapshot.campaign_id, client: liveTenant.name,
        cycle: snapshot.cycle_id, status: snapshot.status, channels: snapshot.channels,
        budgetMinor: snapshot.amount_minor, currency: snapshot.currency,
        updated: formatWorkflowTime(snapshot.updated_at),
      }))
      const nextPacks: StudioPack[] = studioFeed.packs.map(snapshot => ({
        id: snapshot.pack_id, product: snapshot.product_id, client: liveTenant.name,
        tier: snapshot.tier, status: snapshot.status, assets: snapshot.assets,
        manifestSha256: snapshot.manifest_sha256, updated: formatWorkflowTime(snapshot.updated_at),
      }))
      setCampaigns(nextCampaigns)
      setPacks(nextPacks)
      setSelectedCampaign(current => nextCampaigns.some(item => item.id === current) ? current : nextCampaigns[0]?.id ?? '')
      setSelectedPack(current => nextPacks.some(item => item.id === current) ? current : nextPacks[0]?.id ?? '')
      setFeedsError(null)
    } catch (error) {
      setFeedsError(errorMessage(error))
    } finally {
      setFeedsLoading(false)
    }
  }, [api, liveTenant, user])

  useEffect(() => {
    if (!tenantConnected || !api.orchestratorConfigured) return
    const initial = window.setTimeout(() => void refreshWorkflows(), 0)
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible' && pwa.online) void refreshWorkflows()
    }, 30_000)
    return () => {
      window.clearTimeout(initial)
      window.clearInterval(timer)
    }
  }, [tenantConnected, api.orchestratorConfigured, refreshWorkflows, pwa.online])

  useEffect(() => {
    let active = true
    async function bootstrap() {
      const runtime = await loadRuntimeConfig()
      if (!active) return
      setConfig(runtime)
      const identity = new VolumeOSAuth(runtime)
      setAuth(identity)
      try {
        const currentUser = await identity.completeSignIn()
        if (!active) return
        setUser(currentUser)
        if (currentUser) {
          setCampaigns([])
          setPacks([])
        }
        const client = new VolumeOSApi(runtime.coreApiUrl, runtime.orchestratorApiUrl, () => identity.accessToken())
        if (runtime.coreApiUrl) {
          try {
            const catalog = await client.offers()
            if (active) {
              setOffers(catalog.offers)
              setBundles(catalog.bundles)
              setOffersLive(true)
            }
          } catch (error) {
            if (active) setToast({ kind: 'error', message: `Live offer catalog unavailable; showing reference data. ${errorMessage(error)}` })
          }
          if (currentUser) {
            try {
              const currentTenant = await client.tenant()
              if (active) setLiveTenant(currentTenant)
            } catch (error) {
              if (!(error instanceof ApiError && error.status === 404) && active) {
                setToast({ kind: 'error', message: errorMessage(error) })
              }
            }
          }
        }
      } catch (error) {
        if (active) setToast({ kind: 'error', message: `Sign-in could not be completed. ${errorMessage(error)}` })
      } finally {
        if (active) setReady(true)
      }
    }
    void bootstrap()
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 6500)
    return () => window.clearTimeout(timer)
  }, [toast])

  useEffect(() => {
    let active = true
    const resetAndLoad = window.setTimeout(() => {
      if (!active) return
      setQaAssets([])
      setQaPackId('')
      setQaManifest('')
      setQaError(null)
      setQaImageError(null)
      setLoadedQaAssets({})
      if (!tenantConnected || !api.orchestratorConfigured || !qaTargetId || !qaTargetManifest || qaTargetAssetCount <= 0) {
        setQaLoading(false)
        return
      }
      setQaLoading(true)
      void api.studioAssets(qaTargetId).then(response => {
        if (!active) return
        setQaAssets(response.assets)
        setQaPackId(response.pack_id)
        setQaManifest(response.manifest_sha256)
      }).catch(error => {
        if (active) setQaError(errorMessage(error))
      }).finally(() => {
        if (active) setQaLoading(false)
      })
    }, 0)
    return () => {
      active = false
      window.clearTimeout(resetAndLoad)
    }
  }, [tenantConnected, api, qaTargetId, qaTargetManifest, qaTargetAssetCount, qaReloadToken])

  function navigate(next: PageId) {
    setPage(next)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  async function signIn() {
    try { await auth?.signIn() } catch (error) { setToast({ kind: 'error', message: errorMessage(error) }) }
  }

  async function signOut() {
    await auth?.signOut()
    setUser(null)
    setLiveTenant(null)
    setCampaigns(previewCampaigns)
    setPacks(previewPacks)
    setSelectedCampaign(previewCampaigns[0].id)
    setSelectedPack(previewPacks[0].id)
    setFeedsError(null)
    setQaAssets([])
    setQaManifest('')
    setToast({ kind: 'info', message: 'Signed out. Tenant-scoped API access has been cleared from this tab.' })
  }

  async function install() {
    try {
      const result = await pwa.install()
      if (result === 'accepted') setToast({ kind: 'success', message: 'VolumeOS was added to this device.' })
    } catch (error) { setToast({ kind: 'info', message: errorMessage(error) }) }
  }

  async function submitTenant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    await runAction(async () => {
      const created = await api.createTenant(String(data.get('name')), String(data.get('slug')))
      setLiveTenant(created)
      setModal(null)
      setToast({ kind: 'success', message: `${created.name} was created in the authenticated tenant scope.` })
    })
  }

  async function submitWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    await runAction(async () => {
      await api.createWorkspace(String(data.get('name')))
      setModal(null)
      setToast({ kind: 'success', message: 'Workspace created through the VolumeOS Core API.' })
    })
  }

  async function submitCampaign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const campaignId = String(data.get('campaign_id'))
    await runAction(async () => {
      await api.startCampaign({
        campaign_id: campaignId,
        cycle_id: String(data.get('cycle_id')),
        brief: String(data.get('brief')),
        amount_minor: Math.round(Number(data.get('amount')) * 100),
        currency: 'NGN',
      })
      await refreshWorkflows()
      setSelectedCampaign(campaignId)
      setModal(null)
      setToast({ kind: 'success', message: 'Campaign workflow submitted. Provider dispatch remains approval-gated.' })
    })
  }

  async function approveCampaign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    await runAction(async () => {
      await api.approveCampaign(selectedCampaign, String(data.get('approval_id')))
      await refreshWorkflows()
      setToast({ kind: 'success', message: 'Approval signal accepted; the queue has been refreshed from Temporal.' })
    })
  }

  async function submitPack(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const packId = String(data.get('pack_id'))
    await runAction(async () => {
      await api.startStudioPack({
        pack_id: packId,
        product_id: String(data.get('product_id')),
        tier: String(data.get('tier')),
        source_image: String(data.get('source_image')),
        checkpoint: String(data.get('checkpoint')),
        materials: String(data.get('materials')),
        dimensions: String(data.get('dimensions')),
        price_text: String(data.get('price_text')),
      })
      await refreshWorkflows()
      setSelectedPack(packId)
      setModal(null)
      setToast({ kind: 'success', message: 'Studio workflow submitted. Assets must pass human QA before delivery.' })
    })
  }

  async function reviewPack(decision: 'approve' | 'reject', event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    if (!qaManifest || (decision === 'approve' && !qaReviewReady)) {
      setToast({ kind: 'error', message: 'Load and inspect the current QA assets before reviewing this pack.' })
      return
    }
    await runAction(async () => {
      await api.reviewStudioPack(selectedPack, decision, String(data.get('reason') ?? ''), qaManifest)
      await refreshWorkflows()
      setToast({ kind: 'success', message: decision === 'approve' ? 'QA approval sent; delivery status now comes from the Studio workflow.' : 'Rejection sent; the refreshed workflow remains outside client delivery.' })
    })
  }

  async function subscribe(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedOffer) return
    await runAction(async () => {
      await api.createSubscription(selectedOffer.offer, selectedOffer.tier)
      setModal(null)
      setSelectedOffer(null)
      setToast({ kind: 'success', message: 'Subscription created against the reviewed offer/tier contract.' })
    })
  }

  async function submitSuspension(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const suspended = data.get('suspended') === 'true'
    await runAction(async () => {
      const updated = await api.setOutboundSuspension(suspended, String(data.get('reason')))
      setLiveTenant(updated)
      setModal(null)
      setToast({ kind: 'success', message: suspended ? 'Outbound suspension recorded. New provider calls must now fail closed.' : 'Outbound access restored with an audit reason.' })
    })
  }

  async function submitSuppression(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    await runAction(async () => {
      await api.setSuppression(String(data.get('channel')), String(data.get('recipient')), String(data.get('reason')), data.get('suppressed') === 'true')
      setModal(null)
      setToast({ kind: 'success', message: 'Suppression policy updated. Recipient data is stored only as an HMAC digest.' })
    })
  }

  async function runAction(action: () => Promise<void>) {
    setBusy(true)
    try { await action() } catch (error) { setToast({ kind: 'error', message: errorMessage(error) }) } finally { setBusy(false) }
  }

  if (!ready) return <LoadingScreen />

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside className="side-rail">
        <button className="brand" onClick={() => navigate('overview')} aria-label="VolumeOS overview">
          <Logo />
          <span><strong>VolumeOS</strong><small>M1 operations</small></span>
        </button>
        <nav className="desktop-nav" aria-label="Primary navigation">
          <p className="nav-label">Workspace</p>
          {navigation.map(item => <NavButton key={item.id} item={item} active={page === item.id} onClick={() => navigate(item.id)} />)}
        </nav>
        <div className="rail-bottom">
          <div className="m1-scope"><span className="live-dot" /> M1 delivery scope<small>Onboarding · Cadence · Studio</small></div>
          <NavButton item={{ id: 'settings', label: 'Settings & safety', icon: Settings }} active={page === 'settings'} onClick={() => navigate('settings')} />
        </div>
      </aside>

      <header className="mobile-header">
        <button className="brand" onClick={() => navigate('overview')} aria-label="VolumeOS overview"><Logo /><strong>VolumeOS</strong></button>
        <button className="icon-button" onClick={() => navigate('settings')} aria-label="Settings and safety"><Settings size={20} /></button>
      </header>

      <main id="main-content" className="main-content">
        <div className="topbar">
          <div className="connection-summary">
            <span className={`connection-dot ${authenticated ? 'connected' : ''}`} />
            {authenticated ? 'OIDC tenant session' : 'Product preview'}
            <span className="topbar-divider" />
            <span>{offersLive ? 'Live offer catalog' : 'Reference price book'}</span>
          </div>
          <div className="topbar-actions">
            {!pwa.online && <span className="offline-label"><CloudOff size={15} /> Offline</span>}
            <button className="button button-quiet install-button" onClick={install}><Download size={16} /> Install app</button>
            {user ? (
              <button className="tenant-chip" onClick={() => navigate('settings')}><span>{user.profile.name ?? 'Tenant user'}</span><ChevronRight size={15} /></button>
            ) : (
              <button className="button button-primary" onClick={signIn} disabled={!oidcConfigured(config)} title={!oidcConfigured(config) ? 'Configure OIDC in runtime-config.json' : undefined}><LogIn size={16} /> Sign in</button>
            )}
          </div>
        </div>

        {!authenticated && (
          <div className="preview-banner" role="status">
            <CircleAlert size={18} />
            <div><strong>Explore the M1 console safely.</strong><span>Names, queues, and statuses below are illustrative preview data. No provider call or business mutation is possible until you connect and sign in.</span></div>
            <button onClick={() => navigate('settings')}>View connection setup <ArrowRight size={15} /></button>
          </div>
        )}

        {page === 'overview' && <Overview tenant={tenant} awaitingApprovals={awaitingApprovals} campaigns={campaigns} packs={packs} onNavigate={navigate} onCampaign={id => { setSelectedCampaign(id); navigate('campaigns') }} onPack={id => { setSelectedPack(id); navigate('studio') }} />}
        {page === 'onboarding' && <Onboarding tenant={tenant} connected={tenantConnected} canCreateTenant={authenticated} busy={busy} onModal={setModal} />}
        {page === 'campaigns' && <Campaigns campaigns={campaigns} selected={selectedCampaign} onSelect={setSelectedCampaign} connected={workflowFeedReady} busy={busy || feedsLoading} loading={feedsLoading} feedError={feedsError} onRefresh={refreshWorkflows} onNew={() => setModal('campaign')} onApprove={approveCampaign} />}
        {page === 'studio' && <Studio packs={packs} selected={selectedPack} onSelect={setSelectedPack} connected={workflowFeedReady} busy={busy || feedsLoading} loading={feedsLoading} feedError={feedsError} onRefresh={refreshWorkflows} onNew={() => setModal('pack')} onReview={reviewPack} qaAssets={qaAssets} qaLoading={qaLoading} qaError={qaError} qaImageError={qaImageError} qaManifestReady={qaManifestReady} qaReviewReady={qaReviewReady} onQaLoad={id => setLoadedQaAssets(current => ({ ...current, [id]: true }))} onQaError={() => setQaImageError('One or more QA images could not be loaded. Approval is disabled; refresh the signed asset URLs or reject the pack.')} onQaRefresh={() => setQaReloadToken(current => current + 1)} />}
        {page === 'offers' && <Offers offers={offers} bundles={bundles} live={offersLive} connected={tenantConnected} onSubscribe={(offer, tier) => { setSelectedOffer({ offer, tier }); setModal('subscription') }} />}
        {page === 'settings' && <SettingsPage config={config} user={user} tenant={tenant} connected={tenantConnected} pwa={pwa} busy={busy} onInstall={install} onSignIn={signIn} onSignOut={signOut} onModal={setModal} />}
      </main>

      <nav className="mobile-nav" aria-label="Mobile navigation">
        {navigation.map(item => <NavButton key={item.id} item={item} active={page === item.id} onClick={() => navigate(item.id)} />)}
      </nav>

      {modal === 'tenant' && <Modal title="Create tenant record" description="The tenant ID comes from your OIDC claim; it is never accepted from the form." onClose={() => setModal(null)}>
        <form onSubmit={submitTenant} className="form-stack"><Field label="Business name" name="name" placeholder="e.g. Aster Foods" required /><Field label="URL-safe slug" name="slug" placeholder="aster-foods" pattern="[a-z0-9-]+" required /><Submit busy={busy} label="Create tenant" /></form>
      </Modal>}
      {modal === 'workspace' && <Modal title="Create workspace" description="Creates a delivery workspace inside the authenticated tenant." onClose={() => setModal(null)}>
        <form onSubmit={submitWorkspace} className="form-stack"><Field label="Workspace name" name="name" placeholder="Lagos delivery team" required /><Submit busy={busy} label="Create workspace" /></form>
      </Modal>}
      {modal === 'campaign' && <Modal title="Start Content Cadence" description="Starts an approval-gated workflow. A draft is not permission to send." onClose={() => setModal(null)} wide>
        <form onSubmit={submitCampaign} className="form-stack"><div className="form-grid"><Field label="Campaign ID" name="campaign_id" placeholder="camp-oct-winback" required /><Field label="Cycle ID" name="cycle_id" placeholder="cycle-2026-w43" required /></div><label className="field"><span>Campaign brief</span><textarea name="brief" rows={4} placeholder="Audience, offer, tone, and desired action. Direct contact details are redacted before AI drafting." required /></label><Field label="Reservation amount (NGN)" name="amount" type="number" min="1" step="0.01" placeholder="85000" required /><ConnectionNote enabled={tenantConnected && api.orchestratorConfigured} text="Requires an authenticated tenant and a deployed orchestrator API." /><Submit busy={busy} label="Start approval-gated workflow" disabled={!tenantConnected || !api.orchestratorConfigured} /></form>
      </Modal>}
      {modal === 'pack' && <Modal title="Start Shopfront Studio pack" description="Generated assets enter QA storage only; delivery requires a separate human approval." onClose={() => setModal(null)} wide>
        <form onSubmit={submitPack} className="form-stack"><div className="form-grid"><Field label="Pack ID" name="pack_id" placeholder="pack-adire-tote" required /><Field label="Product ID" name="product_id" placeholder="adire-tote-01" required /></div><div className="form-grid"><label className="field"><span>Tier</span><select name="tier" defaultValue="growth"><option value="starter">Starter · 5 images</option><option value="growth">Growth · 7 images</option><option value="pro">Pro · 12 images</option></select></label><Field label="Reviewed model checkpoint" name="checkpoint" placeholder="reviewed-model.safetensors" required /></div><Field label="Source image key" name="source_image" placeholder="tenant/uploads/adire-tote.png" required /><div className="form-grid"><Field label="Materials" name="materials" placeholder="Adire cotton, leather handle" /><Field label="Dimensions" name="dimensions" placeholder="42 × 35 × 12 cm" /></div><Field label="Price text" name="price_text" placeholder="₦28,500" /><ConnectionNote enabled={tenantConnected && api.orchestratorConfigured} text="Requires an authenticated tenant, orchestrator API, object storage, and a reviewed ComfyUI runtime." /><Submit busy={busy} label="Submit for generation and QA" disabled={!tenantConnected || !api.orchestratorConfigured} /></form>
      </Modal>}
      {modal === 'subscription' && selectedOffer && <Modal title="Confirm subscription" description="This creates a commercial subscription record; it does not create an M2 billing posting." onClose={() => setModal(null)}>
        <form onSubmit={subscribe} className="form-stack"><div className="confirmation-block"><span>Offer</span><strong>{selectedOffer.offer}</strong><span>Tier</span><strong>{selectedOffer.tier}</strong></div><ConnectionNote enabled={tenantConnected} text="Requires an authenticated tenant with the sales role." /><Submit busy={busy} label="Create subscription" disabled={!tenantConnected} /></form>
      </Modal>}
      {modal === 'suspension' && tenant && <Modal title={tenant.outbound_suspended ? 'Restore outbound messaging' : 'Suspend outbound messaging'} description="Every new provider dispatch checks this persistent tenant-level policy. An audit reason is mandatory." onClose={() => setModal(null)}>
        <form onSubmit={submitSuspension} className="form-stack"><input type="hidden" name="suspended" value={String(!tenant.outbound_suspended)} /><label className="field"><span>Audit reason</span><textarea name="reason" rows={3} placeholder={tenant.outbound_suspended ? 'Incident resolved and operator approved restoration' : 'Incident INC-42: stop all new provider calls'} required /></label><ConnectionNote enabled={tenantConnected} text="Requires an authenticated super-operator session." /><Submit busy={busy} danger={!tenant.outbound_suspended} label={tenant.outbound_suspended ? 'Restore with audit reason' : 'Suspend all new outbound calls'} disabled={!tenantConnected} /></form>
      </Modal>}
      {modal === 'suppression' && <Modal title="Update recipient suppression" description="The Core API normalizes the recipient and persists only an HMAC-SHA-256 digest. Do not include real customer data in a preview environment." onClose={() => setModal(null)}>
        <form onSubmit={submitSuppression} className="form-stack"><div className="form-grid"><label className="field"><span>Channel</span><select name="channel"><option value="email">Email</option><option value="sms">SMS</option><option value="whatsapp">WhatsApp</option><option value="social">Social</option></select></label><label className="field"><span>Policy action</span><select name="suppressed"><option value="true">Suppress</option><option value="false">Restore</option></select></label></div><Field label="Recipient" name="recipient" placeholder="Authorized synthetic recipient" required /><Field label="Audit reason" name="reason" placeholder="Suppression request SUP-104" required /><ConnectionNote enabled={tenantConnected} text="Requires an authenticated super-operator session and configured suppression HMAC secret." /><Submit busy={busy} label="Update persistent policy" disabled={!tenantConnected} /></form>
      </Modal>}

      {toast && <div className={`toast toast-${toast.kind}`} role={toast.kind === 'error' ? 'alert' : 'status'}>{toast.kind === 'success' ? <CheckCircle2 size={19} /> : <CircleAlert size={19} />}<span>{toast.message}</span><button onClick={() => setToast(null)} aria-label="Dismiss notification"><X size={17} /></button></div>}
      {pwa.needRefresh && <div className="update-bar" role="status"><RefreshCw size={18} /><span>A new VolumeOS version is ready.</span><button onClick={pwa.update}>Reload</button><button onClick={pwa.dismissRefresh} aria-label="Dismiss update"><X size={16} /></button></div>}
      {pwa.offlineReady && <div className="update-bar" role="status"><CloudOff size={18} /><span>The console shell is ready for offline use. Live APIs remain network-only.</span><button onClick={pwa.dismissOfflineReady}>Got it</button></div>}
    </div>
  )
}

function Overview({ tenant, awaitingApprovals, campaigns, packs, onNavigate, onCampaign, onPack }: {
  tenant: Tenant | null; awaitingApprovals: number; campaigns: Campaign[]; packs: StudioPack[]
  onNavigate: (page: PageId) => void; onCampaign: (id: string) => void; onPack: (id: string) => void
}) {
  const campaign = campaigns.find(item => item.status === 'awaiting_approval') ?? campaigns[0]
  const pack = packs.find(item => item.status === 'awaiting_qa') ?? packs[0]
  return <div className="page overview-page">
    <section className="page-heading split-heading"><div><p className="section-kicker">Operations overview</p><h1>Run the day.<br /><span>Keep every release human.</span></h1><p className="heading-copy">One tenant-scoped view for client onboarding, coordinated campaigns, and marketplace-ready creative.</p></div><div className="heading-actions"><button className="button button-primary" onClick={() => onNavigate('campaigns')}>Review approval queue <ArrowRight size={17} /></button><button className="button button-secondary" onClick={() => onNavigate('onboarding')}>Open client journey</button></div></section>
    <section className="pulse-grid" aria-label="Operational summary">
      <div className="pulse-main"><div className="pulse-top"><span className="status-badge status-active"><span className="live-dot" /> M1 operating picture</span><span className="data-label">Preview workflow</span></div><div className="pulse-number">{String(awaitingApprovals).padStart(2, '0')}</div><h2>decisions need a person</h2><p>Nothing crosses a provider or client-delivery boundary on its own.</p><div className="segmented-progress" aria-label="Three M1 service lines"><span className="active" /><span className="active" /><span /></div><div className="pulse-legend"><span>Onboard</span><span>Approve</span><span>Deliver</span></div></div>
      <div className="metric-block"><Clock3 size={20} /><span>First report window</span><strong>48<small>hours</small></strong><p>Measured from signup to approved delivery.</p></div>
      <div className="metric-block"><ShieldCheck size={20} /><span>Release guardrails</span><strong>02<small>human gates</small></strong><p>Campaign approval and Studio QA.</p></div>
    </section>
    <section className="section-block"><div className="section-heading"><div><p className="section-kicker">Action queue</p><h2>What needs attention</h2></div><span className="data-label">Illustrative until connected</span></div><div className="action-list">
      {campaign && <button className="action-row" onClick={() => onCampaign(campaign.id)}><span className="action-icon"><Send size={20} /></span><span className="action-copy"><small>Campaign approval · {campaign.client}</small><strong>{campaign.name}</strong><span>{campaign.channels.join(' + ')} · {money(campaign.budgetMinor)} reservation</span></span><StatusBadge status={campaign.status} /><ChevronRight size={19} /></button>}
      {pack && <button className="action-row" onClick={() => onPack(pack.id)}><span className="action-icon"><ImageIcon size={20} /></span><span className="action-copy"><small>Studio QA · {pack.client}</small><strong>{pack.product}</strong><span>{pack.assets || 'Pending'} assets · {titleCase(pack.tier)} pack</span></span><StatusBadge status={pack.status} /><ChevronRight size={19} /></button>}
      <button className="action-row" onClick={() => onNavigate('onboarding')}><span className="action-icon"><Rocket size={20} /></span><span className="action-copy"><small>Client journey · {tenant?.name ?? 'Tenant setup required'}</small><strong>{tenant ? 'Verify operational readiness' : 'Create the tenant record'}</strong><span>Identity → tenancy → channels → activation → welcome</span></span><StatusBadge status={tenant?.lifecycle ?? 'required'} /><ChevronRight size={19} /></button>
    </div></section>
    <section className="service-grid"><ServiceLine number="01" title="Onboarding" copy="Compensating identity and channel setup, with customer-owned social OAuth left intact." status={tenant?.lifecycle ?? 'setup'} onClick={() => onNavigate('onboarding')} /><ServiceLine number="02" title="Content Cadence" copy="Draft, human approval, policy checks, coordinated dispatch, and reconciled outcomes." status="approval-gated" onClick={() => onNavigate('campaigns')} /><ServiceLine number="06" title="Shopfront Studio" copy="ComfyUI generation followed by human QA and channel-specific delivery formats." status="qa-gated" onClick={() => onNavigate('studio')} /></section>
  </div>
}

function Onboarding({ tenant, connected, canCreateTenant, busy, onModal }: { tenant: Tenant | null; connected: boolean; canCreateTenant: boolean; busy: boolean; onModal: (modal: ModalId) => void }) {
  const steps = [
    { title: 'Identity', copy: 'Keycloak client with hardcoded tenant claim', icon: LockKeyhole },
    { title: 'Tenancy', copy: 'Tenant record and isolated workspace', icon: Store },
    { title: 'Channels', copy: 'ListMonk list and existing Postiz OAuth validation', icon: Send },
    { title: 'Activation', copy: 'Optimistic lifecycle transition', icon: Activity },
    { title: 'Welcome', copy: 'Provider-backed welcome message', icon: BadgeCheck },
  ]
  return <div className="page"><PageHeading kicker="Client delivery" title="Onboarding with a way back." copy="The durable saga provisions only what VolumeOS owns and compensates in reverse when a step fails. Customer social connections are never invented or deleted." actions={<button className="button button-primary" onClick={() => onModal(tenant ? 'workspace' : 'tenant')} disabled={!canCreateTenant}>{tenant ? <Plus size={17} /> : <UserRoundCheck size={17} />}{tenant ? 'Create workspace' : 'Create tenant'}</button>} />
    <section className="tenant-strip"><div><span className="data-label">Current tenant</span><h2>{tenant?.name ?? 'No tenant record yet'}</h2><p>{tenant ? `${tenant.slug} · version ${tenant.version}` : 'Authenticate with a tenant-scoped OIDC account, then create the initial record.'}</p></div><StatusBadge status={tenant?.lifecycle ?? 'required'} /><div className="tenant-policy"><span>Outbound policy</span><strong>{tenant?.outbound_suspended ? 'Suspended' : 'Not suspended'}</strong></div></section>
    <section className="journey-grid">{steps.map((step, index) => <div className="journey-step" key={step.title}><div className="journey-marker"><step.icon size={19} /><span>{String(index + 1).padStart(2, '0')}</span></div><h3>{step.title}</h3><p>{step.copy}</p>{index < steps.length - 1 && <ArrowRight className="journey-arrow" size={18} />}</div>)}</section>
    <section className="two-column-section"><div className="section-block compact"><p className="section-kicker">Operational acceptance</p><h2>Before the first client goes live</h2><Checklist items={['Identity and tenant provisioning verified', 'Per-tenant ListMonk list bound', 'Existing enabled Postiz integration validated', 'Failure-injection compensation drill passed', 'First approved report delivered within 48 hours']} /></div><div className="section-block compact"><p className="section-kicker">Available Core action</p><h2>Delivery workspace</h2><p className="body-copy">Create a workspace through the existing tenant-scoped Core contract. Full saga execution remains in Temporal and requires staging acceptance.</p><button className="button button-secondary" onClick={() => onModal('workspace')} disabled={!connected || busy}><Plus size={16} /> Create workspace</button><ConnectionNote enabled={connected} text="Requires an authenticated tenant with the delivery role." /></div></section>
  </div>
}

function Campaigns({ campaigns, selected, onSelect, connected, busy, loading, feedError, onRefresh, onNew, onApprove }: { campaigns: Campaign[]; selected: string; onSelect: (id: string) => void; connected: boolean; busy: boolean; loading: boolean; feedError: string | null; onRefresh: () => Promise<void>; onNew: () => void; onApprove: (event: FormEvent<HTMLFormElement>) => void }) {
  const campaign = campaigns.find(item => item.id === selected) ?? campaigns[0]
  return <div className="page"><PageHeading kicker="Content Cadence" title="Approve the release. Not the risk." copy="Human approval precedes reservation and dispatch. Persistent suppression and tenant suspension checks still run before every new provider call." actions={<><button className="button button-secondary" onClick={() => void onRefresh()} disabled={!connected || loading}><RefreshCw className={loading ? 'spin' : ''} size={16} /> Refresh</button><button className="button button-primary" onClick={onNew}><Plus size={17} /> New campaign</button></>} />
    <div className="data-label page-data-label">{feedError ? 'Live workflow feed unavailable · actions disabled' : connected ? 'Live Temporal workflow snapshots · refresh every 30 seconds' : 'Illustrative preview queue · no live actions'}</div>
    {feedError && <div className="feed-error" role="alert"><CircleAlert size={17} /><span>Workflow refresh failed. Human actions are disabled until a current feed loads. {feedError}</span></div>}
    <div className="workspace-grid"><section className="queue-panel"><div className="panel-heading"><h2>Campaign cycles</h2><span>{campaigns.length} in view</span></div><div className="queue-list">{campaigns.map(item => <button key={item.id} className={`queue-item ${item.id === campaign?.id ? 'selected' : ''}`} onClick={() => onSelect(item.id)}><span className="queue-item-top"><strong>{item.name}</strong><StatusBadge status={item.status} /></span><span>{item.client}</span><small>{item.cycle} · {item.updated}</small></button>)}</div></section>
      {campaign && <section className="review-panel"><div className="review-header"><div><span className="data-label">Release review</span><h2>{campaign.name}</h2><p>{campaign.client} · {campaign.cycle}</p></div><StatusBadge status={campaign.status} /></div><div className="release-facts"><Fact label="Reservation" value={money(campaign.budgetMinor, campaign.currency)} /><Fact label="Channels" value={campaign.channels.length ? campaign.channels.join(', ') : 'Reconciled after dispatch'} /><Fact label="Approval timeout" value="24 hours" /><Fact label="Unapproved sends" value="0 permitted" /></div><div className="approval-flow"><span className="complete"><Check size={15} /> Draft</span><ArrowRight size={16} /><span className="current"><UserRoundCheck size={15} /> Human approval</span><ArrowRight size={16} /><span>Policy & dispatch</span></div><form key={campaign.id} onSubmit={onApprove} className="approval-form"><Field label="Approval ID" name="approval_id" defaultValue={`approval-${campaign.id}-01`} required /><ConnectionNote enabled={connected} text="Approval is signaled to the current Temporal workflow; this form never calls a provider directly." /><button className="button button-primary" disabled={!connected || busy || campaign.status !== 'awaiting_approval'}>{busy ? <Loader2 className="spin" size={17} /> : <FileCheck2 size={17} />} Approve for policy-gated dispatch</button></form></section>}
      {!campaign && <EmptyState title="No live campaign cycles loaded" copy={loading ? 'Loading tenant-scoped workflow snapshots.' : 'Start a campaign or refresh the tenant-scoped Temporal queue. Preview records are never carried into an authenticated session.'} />}
    </div>
  </div>
}

function Studio({ packs, selected, onSelect, connected, busy, loading, feedError, onRefresh, onNew, onReview, qaAssets, qaLoading, qaError, qaImageError, qaManifestReady, qaReviewReady, onQaLoad, onQaError, onQaRefresh }: { packs: StudioPack[]; selected: string; onSelect: (id: string) => void; connected: boolean; busy: boolean; loading: boolean; feedError: string | null; onRefresh: () => Promise<void>; onNew: () => void; onReview: (decision: 'approve' | 'reject', event: FormEvent<HTMLFormElement>) => void; qaAssets: StudioAsset[]; qaLoading: boolean; qaError: string | null; qaImageError: string | null; qaManifestReady: boolean; qaReviewReady: boolean; onQaLoad: (id: string) => void; onQaError: () => void; onQaRefresh: () => void }) {
  const pack = packs.find(item => item.id === selected) ?? packs[0]
  return <div className="page"><PageHeading kicker="Shopfront Studio" title="Creative that earns its release." copy="Generated assets stay in tenant-prefixed QA storage until review. Approved outputs become 1080×1080 WhatsApp and 1000×1000 Jumia JPEGs." actions={<><button className="button button-secondary" onClick={() => void onRefresh()} disabled={!connected || loading}><RefreshCw className={loading ? 'spin' : ''} size={16} /> Refresh</button><button className="button button-primary" onClick={onNew}><Plus size={17} /> New Studio pack</button></>} />
    <div className="data-label page-data-label">{feedError ? 'Live workflow feed unavailable · actions disabled' : connected ? 'Live Temporal workflow snapshots · manifest-bound QA' : 'Illustrative preview queue · no live actions'}</div>
    {feedError && <div className="feed-error" role="alert"><CircleAlert size={17} /><span>Workflow refresh failed. Human actions are disabled until a current feed loads. {feedError}</span></div>}
    <div className="workspace-grid"><section className="queue-panel"><div className="panel-heading"><h2>Asset packs</h2><span>{packs.length} in view</span></div><div className="queue-list">{packs.map(item => <button key={item.id} className={`queue-item ${item.id === pack?.id ? 'selected' : ''}`} onClick={() => onSelect(item.id)}><span className="queue-item-top"><strong>{item.product}</strong><StatusBadge status={item.status} /></span><span>{item.client}</span><small>{titleCase(item.tier)} · {item.assets || 'Pending'} assets · {item.updated}</small></button>)}</div></section>
      {pack && <section className="review-panel"><div className="review-header"><div><span className="data-label">Human QA</span><h2>{pack.product}</h2><p>{pack.client} · {pack.id}</p></div><StatusBadge status={pack.status} /></div>
        {qaLoading && <div className="asset-schema" role="status"><Loader2 className="spin" size={24} /><div><strong>Loading current QA manifest</strong><p>Validating tenant-prefixed objects and issuing short-lived image URLs.</p></div></div>}
        {qaError && <div className="feed-error" role="alert"><CircleAlert size={17} /><span>{qaError} Approval remains disabled.</span></div>}
        {qaAssets.length > 0 && <div className="qa-gallery" aria-label={`QA assets for ${pack.product}`}>{qaAssets.map((asset, index) => <a className="qa-asset" key={asset.id} href={asset.url} target="_blank" rel="noreferrer" aria-label={`Open QA asset ${index + 1} in a new tab`}><img src={asset.url} alt={`${pack.product}, QA asset ${index + 1}`} onLoad={() => onQaLoad(asset.id)} onError={onQaError} /><span>QA {String(index + 1).padStart(2, '0')}</span></a>)}</div>}
        {!qaLoading && !qaError && qaAssets.length === 0 && <div className="asset-schema"><div className="schema-mark"><ImageIcon size={34} /></div><div><strong>{pack.assets ? 'QA assets are not loaded' : 'Versioned ComfyUI output'}</strong><p>{pack.assets ? 'Refresh to validate the current manifest and load its tenant-scoped images.' : 'Asset count and review images appear after generation. Preview records never enable live review.'}</p></div></div>}
        {qaImageError && <div className="feed-error" role="alert"><CircleAlert size={17} /><span>{qaImageError}</span></div>}
        {(qaError || qaImageError) && connected && pack.status === 'awaiting_qa' && <button className="button button-secondary qa-refresh" onClick={onQaRefresh} disabled={qaLoading}><RefreshCw className={qaLoading ? 'spin' : ''} size={16} /> Refresh signed asset URLs</button>}
        <div className="release-facts"><Fact label="Pack tier" value={titleCase(pack.tier)} /><Fact label="WhatsApp" value="1080 × 1080 JPEG" /><Fact label="Jumia" value="1000 × 1000 JPEG" /><Fact label="Rejected delivery" value="Blocked" /></div><form onSubmit={event => onReview('approve', event)} className="approval-form"><ConnectionNote enabled={connected && qaReviewReady} text="Approval requires the current QA manifest and successful loading of every image. The same manifest hash is checked again before delivery." /><button className="button button-primary" disabled={!connected || busy || pack.status !== 'awaiting_qa' || !qaReviewReady}><PackageCheck size={17} /> Approve pack for conversion</button></form><form key={pack.id} onSubmit={event => onReview('reject', event)} className="reject-form"><Field label="Rejection reason" name="reason" placeholder="Describe the quality or brand issue" required={pack.status === 'awaiting_qa'} /><button className="button button-danger-quiet" disabled={!connected || busy || pack.status !== 'awaiting_qa' || !qaManifestReady}><Ban size={16} /> Reject pack</button></form></section>}
      {!pack && <EmptyState title="No live Studio packs loaded" copy={loading ? 'Loading tenant-scoped workflow snapshots.' : 'Start a Studio pack or refresh the tenant-scoped Temporal queue. Actual object-backed QA images appear after generation.'} />}
    </div>
  </div>
}

function Offers({ offers, bundles, live, connected, onSubscribe }: { offers: Offer[]; bundles: Bundle[]; live: boolean; connected: boolean; onSubscribe: (offer: string, tier: string) => void }) {
  const [tiers, setTiers] = useState<Record<string, string>>({})
  return <div className="page"><PageHeading kicker="Commercial catalog" title="A clear offer. A reviewed price." copy="M1 carries the supplied Q1 founding rates as reference data. An operator must activate a dated price book before quotes or billing; no M2 ledger posting is created here." actions={<span className={`status-badge ${live ? 'status-active' : 'status-warning'}`}>{live ? <Wifi size={14} /> : <Clock3 size={14} />}{live ? 'Live Core catalog' : 'Reference data'}</span>} />
    <section className="offer-list">{offers.map(offer => { const tierId = tiers[offer.id] ?? offer.tiers[0]?.id; const tier = offer.tiers.find(item => item.id === tierId) ?? offer.tiers[0]; if (!tier) return null; return <article className="offer-row" key={offer.id}><div className="offer-identity"><span className="offer-number">{offer.id}</span><div><h2>{offer.name}</h2><p>{offer.module}</p></div></div><div className="tier-tabs" role="group" aria-label={`${offer.name} tiers`}>{offer.tiers.map(item => <button key={item.id} className={item.id === tier.id ? 'active' : ''} onClick={() => setTiers(current => ({ ...current, [offer.id]: item.id }))}>{item.id}</button>)}</div><div className="offer-price"><span>{tier.pack_minor ? 'Per pack' : 'Monthly reference'}</span><strong>{money(tier.pack_minor ?? tier.monthly_minor, tier.currency)}</strong>{!tier.pack_minor && <small>Setup {money(tier.setup_minor, tier.currency)}</small>}{tier.turnaround_hours && <small>{tier.turnaround_hours}-hour turnaround</small>}</div><div className="entitlements">{Object.entries(tier.entitlements).slice(0, 4).map(([key, value]) => <span key={key}><Check size={14} /> {value === 0 ? 'Unlimited' : value} {titleCase(key)}</span>)}</div><button className="button button-secondary" onClick={() => onSubscribe(offer.id, tier.id)} disabled={!connected}>Choose {tier.id}</button></article>})}</section>
    {bundles.map(bundle => <section className="bundle-row" key={bundle.id}><div><span className="section-kicker">Launch bundle</span><h2>Cadence + Studio, one operating rhythm.</h2><p>{bundle.required_offers.join(' + ')} · {bundle.studio_packs_per_month} Studio packs per month</p></div><div className="bundle-price"><span>Monthly reference</span><strong>{money(bundle.monthly_minor, bundle.currency)}</strong><small>Setup {money(bundle.setup_minor, bundle.currency)}</small></div></section>)}
    <ConnectionNote enabled={connected} text="Subscription creation requires an authenticated tenant with the sales role. Prices remain operator-review-required." />
  </div>
}

function SettingsPage({ config, user, tenant, connected, pwa, busy, onInstall, onSignIn, onSignOut, onModal }: { config: RuntimeConfig; user: User | null; tenant: Tenant | null; connected: boolean; pwa: ReturnType<typeof usePwa>; busy: boolean; onInstall: () => void; onSignIn: () => void; onSignOut: () => void; onModal: (modal: ModalId) => void }) {
  return <div className="page"><PageHeading kicker="Deployment & safety" title="Know what is connected." copy="Runtime configuration is supplied by the deployment, never persisted as mock tenant data. API requests are network-only and are not cached by the service worker." />
    <section className="settings-grid"><div className="settings-group"><div className="settings-heading"><LockKeyhole size={20} /><div><h2>Identity and APIs</h2><p>OIDC Authorization Code with PKCE</p></div></div><ConnectionRow label="Core API" value={config.coreApiUrl || 'Not configured'} connected={Boolean(config.coreApiUrl)} /><ConnectionRow label="Orchestrator API" value={config.orchestratorApiUrl || 'Not configured'} connected={Boolean(config.orchestratorApiUrl)} /><ConnectionRow label="OIDC authority" value={config.oidc.authority || 'Not configured'} connected={oidcConfigured(config)} /><ConnectionRow label="Tenant session" value={user ? user.profile.name ?? 'Authenticated' : 'Not signed in'} connected={Boolean(user)} /><div className="settings-action">{user ? <button className="button button-secondary" onClick={onSignOut}><LogOut size={16} /> Sign out of this tab</button> : <button className="button button-primary" onClick={onSignIn} disabled={!oidcConfigured(config)}><LogIn size={16} /> Sign in with OIDC</button>}</div></div>
      <div className="settings-group"><div className="settings-heading"><Download size={20} /><div><h2>Installable PWA</h2><p>Desktop and mobile shell</p></div></div><ConnectionRow label="Network" value={pwa.online ? 'Online' : 'Offline'} connected={pwa.online} /><ConnectionRow label="Static offline shell" value={pwa.offlineReady ? 'Ready on this device' : 'Available after service-worker install'} connected={pwa.offlineReady} /><p className="body-copy">The app shell and icons can be cached. Tenant records, bearer tokens, provider responses, and API payloads are never added to runtime caches.</p><div className="settings-action"><button className="button button-secondary" onClick={onInstall}><Download size={16} /> Install or view instructions</button></div></div></section>
    <section className="safety-section"><div><p className="section-kicker">Persistent guardrails</p><h2>Outbound safety controls</h2><p className="body-copy">These actions call the Core API directly with the signed-in user’s tenant claim and role. They require an audit reason and are disabled in preview.</p></div><div className="safety-actions"><button className={`button ${tenant?.outbound_suspended ? 'button-secondary' : 'button-danger'}`} onClick={() => onModal('suspension')} disabled={!connected || busy || !tenant}><Ban size={17} />{tenant?.outbound_suspended ? 'Restore outbound' : 'Suspend outbound'}</button><button className="button button-secondary" onClick={() => onModal('suppression')} disabled={!connected || busy}><ShieldCheck size={17} /> Update suppression</button></div></section>
    <section className="boundary-note"><CircleAlert size={22} /><div><h3>M1 acceptance is still evidence-based.</h3><p>A connected interface does not prove four-channel delivery, GPU output, RPO/RTO, Wazuh detection, SLO compliance, or cost reconciliation. Those checks still require the staging runbook and real artifacts.</p></div></section>
  </div>
}

function EmptyState({ title, copy }: { title: string; copy: string }) {
  return <section className="review-panel empty-review"><CircleAlert size={24} /><h2>{title}</h2><p>{copy}</p></section>
}

function PageHeading({ kicker, title, copy, actions }: { kicker: string; title: string; copy: string; actions?: ReactNode }) {
  return <section className="page-heading split-heading"><div><p className="section-kicker">{kicker}</p><h1 className="page-title">{title}</h1><p className="heading-copy">{copy}</p></div>{actions && <div className="heading-actions">{actions}</div>}</section>
}

function ServiceLine({ number, title, copy, status, onClick }: { number: string; title: string; copy: string; status: string; onClick: () => void }) {
  return <button className="service-line" onClick={onClick}><span className="offer-number">{number}</span><h3>{title}</h3><p>{copy}</p><div><StatusBadge status={status} /><ArrowRight size={18} /></div></button>
}

function NavButton({ item, active, onClick }: { item: { id: PageId; label: string; icon: LucideIcon }; active: boolean; onClick: () => void }) {
  return <button className={`nav-button ${active ? 'active' : ''}`} onClick={onClick} aria-current={active ? 'page' : undefined}><item.icon size={19} /><span>{item.label}</span></button>
}

function StatusBadge({ status }: { status: string }) {
  const tone = ['active', 'approved', 'delivered', 'completed'].includes(status) ? 'status-active' : ['rejected', 'suspended', 'failed'].includes(status) ? 'status-danger' : ['awaiting_approval', 'awaiting_qa', 'required'].includes(status) ? 'status-warning' : 'status-neutral'
  return <span className={`status-badge ${tone}`}>{status === 'approved' || status === 'delivered' ? <CheckCircle2 size={13} /> : status === 'awaiting_approval' || status === 'awaiting_qa' ? <Clock3 size={13} /> : null}{titleCase(status)}</span>
}

function Fact({ label, value }: { label: string; value: string }) { return <div className="fact"><span>{label}</span><strong>{value}</strong></div> }
function Checklist({ items }: { items: string[] }) { return <ul className="checklist">{items.map(item => <li key={item}><Check size={16} />{item}</li>)}</ul> }
function ConnectionNote({ enabled, text }: { enabled: boolean; text: string }) { return <div className={`connection-note ${enabled ? 'enabled' : ''}`}>{enabled ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}<span>{enabled ? `Connected. ${text}` : text}</span></div> }
function ConnectionRow({ label, value, connected }: { label: string; value: string; connected: boolean }) { return <div className="connection-row"><span>{label}</span><strong>{value}</strong><i className={connected ? 'ok' : ''} aria-label={connected ? 'Connected' : 'Not connected'} /></div> }

function Field({ label, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string; name: string }) {
  return <label className="field"><span>{label}</span><input {...props} /></label>
}

function Submit({ busy, label, disabled, danger }: { busy: boolean; label: string; disabled?: boolean; danger?: boolean }) {
  return <button className={`button ${danger ? 'button-danger' : 'button-primary'}`} disabled={busy || disabled}>{busy ? <Loader2 className="spin" size={17} /> : <ArrowRight size={17} />}{label}</button>
}

function Modal({ title, description, onClose, children, wide }: { title: string; description: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const dialogRef = useRef<HTMLElement>(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose }, [onClose])
  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const focusable = () => Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [href], [tabindex]:not([tabindex="-1"])') ?? [])
    focusable()[0]?.focus()
    const listener = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current()
      if (event.key !== 'Tab') return
      const elements = focusable()
      const first = elements[0]
      const last = elements.at(-1)
      if (!first || !last) return
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', listener)
    return () => {
      document.removeEventListener('keydown', listener)
      document.body.style.overflow = previousOverflow
      previouslyFocused?.focus()
    }
  }, [])
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}><section ref={dialogRef} className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby="modal-title"><button className="modal-close icon-button" onClick={onClose} aria-label="Close dialog"><X size={20} /></button><p className="section-kicker">VolumeOS action</p><h2 id="modal-title">{title}</h2><p className="modal-description">{description}</p>{children}</section></div>
}

function Logo() { return <span className="logo" aria-hidden="true"><svg viewBox="0 0 40 40"><path d="M7 8h8l6 17 6-17h8L24 34h-7z" fill="currentColor" /><path d="M18 5h6v14h-6z" className="logo-cut" /></svg></span> }
function LoadingScreen() { return <div className="loading-screen"><Logo /><Loader2 className="spin" size={24} /><span>Loading VolumeOS</span></div> }
function errorMessage(error: unknown) { return error instanceof Error ? error.message : 'The action could not be completed.' }

export default App
