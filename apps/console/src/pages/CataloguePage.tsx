import { useState } from 'react'
import { Link } from 'react-router'
import { useSession, errorText } from '../state/session-context'
import { allowed, needs } from '../lib/access'
import type { Offer, OfferTier } from '../lib/types'
import { Confirm, Empty, Note, PageHead, Panel } from '../components/ui'
import { money } from '../lib/format'

type Group = { id: string; title: string; description: string; match: (offer: Offer) => boolean }

const num = (offer: Offer) => Number(offer.id)
/** Subscriptions are accepted for offers 01–08 on a priced tier (svc-tenancy ValidateSubscription). */
const subscribable = (offer: Offer) => num(offer) >= 1 && num(offer) <= 8 && offer.tiers.length > 0

const GROUPS: Group[] = [
  { id: 'available', title: 'Available to subscribe', description: 'Priced at founding reference rates. Each price still needs operator review before a client is billed.', match: subscribable },
  { id: 'built', title: 'Built, not yet priced', description: 'Delivery is implemented; there is no price, so no subscription or charge can be created.', match: o => num(o) <= 8 && !subscribable(o) },
  { id: 'catalogue', title: 'Priced, not yet sellable', description: 'Prices are in the price book, but subscriptions are refused until delivery is implemented.', match: o => num(o) >= 19 && num(o) <= 26 },
  { id: 'engagements', title: 'Plan-only engagements', description: 'A reviewed plan, never a charge or a delivery. Start one from Engagements.', match: o => num(o) >= 27 },
  { id: 'commerce', title: 'Commerce suite', description: 'Defined in the catalogue only: no price, and no screens or workflows in this console yet.', match: o => num(o) >= 9 && num(o) <= 18 },
]

const DELIVERY: Record<string, { label: string; to: string }> = {
  '01': { label: 'Campaigns', to: '/services/campaigns' }, '02': { label: 'Campaigns', to: '/services/campaigns' },
  '03': { label: 'Offer jobs', to: '/services/offer-jobs' }, '04': { label: 'Front desk', to: '/services/front-desk' },
  '05': { label: 'Offer jobs', to: '/services/offer-jobs' }, '06': { label: 'Shopfront Studio', to: '/services/studio' },
  '07': { label: 'Offer jobs', to: '/services/offer-jobs' }, '08': { label: 'Offer jobs', to: '/services/offer-jobs' },
  '35': { label: 'Creators Home', to: '/services/creators' },
}

function entitlement(key: string, value: number) {
  const label = key.replaceAll('_', ' ')
  if (value === 0) return `Unlimited ${label}`
  return `${value} ${value === 1 && label.endsWith('s') ? label.slice(0, -1) : label}`
}

function TierPrice({ tier }: { tier: OfferTier }) {
  return tier.pack_minor
    ? <div className="price">{money(tier.pack_minor, tier.currency)}<small>per pack{tier.turnaround_hours ? ` · ${tier.turnaround_hours} h turnaround` : ''}</small></div>
    : <div className="price">{money(tier.monthly_minor, tier.currency)}<small>a month · setup {money(tier.setup_minor, tier.currency)}</small></div>
}

export function CataloguePage({ readOnly = false }: { readOnly?: boolean }) {
  const { catalogue, tenant, roles, api, reloadTenant, notify, user } = useSession()
  const [tiers, setTiers] = useState<Record<string, string>>({})
  const [pending, setPending] = useState<{ offer: Offer; tier: OfferTier } | null>(null)
  const [busy, setBusy] = useState(false)
  const held = new Map((tenant?.subscriptions ?? []).map(sub => [sub.offer, sub.tier]))
  const canSubscribe = !readOnly && allowed(roles, 'subscribe') && Boolean(tenant)

  async function subscribe() {
    if (!pending) return
    setBusy(true)
    try {
      await api.createSubscription(pending.offer.id, pending.tier.id)
      await reloadTenant()
      notify('success', `${tenant?.name ?? 'The business'} is now subscribed to ${pending.offer.id} / ${pending.offer.name} (${pending.tier.id}). Nothing has been charged.`)
      setPending(null)
    } catch (error) {
      notify('error', errorText(error))
    } finally { setBusy(false) }
  }

  if (catalogue.load === 'error') return <div className="page"><PageHead title="Service catalogue" /><Note tone="failed">The catalogue couldn't be loaded: {catalogue.error}</Note></div>

  return <div className="page">
    <PageHead eyebrow={catalogue.priceBook ? `Price book · ${catalogue.priceBook}` : undefined} title={readOnly ? 'Services' : 'Service catalogue'}
      description={readOnly ? (user ? 'The services your agency offers, and the ones you already have.' : 'The packaged services offered through VolumeOS, with their reference prices.') : 'Every packaged offer, numbered, with what is actually true of it today: priced or not, sellable or not, and which part of VolumeOS delivers it.'} />
    {!readOnly && !allowed(roles, 'subscribe') && <Note>Subscribing a business to an offer {needs('subscribe').toLowerCase()}.</Note>}
    {catalogue.load === 'loading' && <Empty title="Loading the catalogue" />}
    {GROUPS.map(group => {
      const offers = catalogue.offers.filter(group.match)
      if (!offers.length) return null
      return <Panel key={group.id} title={`${group.title} · ${offers.length}`} meta={group.description} flush>
        <div className="portfolio">{offers.map(offer => {
          const tier = offer.tiers.find(t => t.id === tiers[offer.id]) ?? offer.tiers[0]
          const holds = held.get(offer.id)
          const delivery = DELIVERY[offer.id]
          return <article key={offer.id} className={`portfolio-row ${tier ? '' : 'muted-row'}`} aria-label={`${offer.id} ${offer.name}`}>
            <span className="portfolio-num">{offer.id}</span>
            <div><h3>{offer.name}</h3><p>{offer.module}{delivery && !readOnly && <> · delivered by <Link to={delivery.to}>{delivery.label}</Link></>}</p></div>
            <div>{offer.tiers.length > 1
              ? <div className="tier-tabs" role="group" aria-label={`${offer.name} tiers`}>{offer.tiers.map(t => <button key={t.id} className={t.id === tier?.id ? 'active' : ''} aria-pressed={t.id === tier?.id} onClick={() => setTiers(current => ({ ...current, [offer.id]: t.id }))}>{t.id}</button>)}</div>
              : tier ? <span className="chip">{tier.id}</span> : null}
              {tier && <div className="entitlements" style={{ marginTop: 6 }}>{Object.entries(tier.entitlements).slice(0, 3).map(([key, value]) => <span key={key}>{entitlement(key, value)}</span>)}</div>}
            </div>
            <div>{tier ? <TierPrice tier={tier} /> : <div className="price muted">Unpriced<small>No charge can be created</small></div>}</div>
            <div className="row" style={{ justifyContent: 'flex-end' }}>
              {holds ? <span className="status done">Subscribed · {holds}</span>
                : group.id === 'available' ? (readOnly ? <span className="chip">{user ? 'Ask your account manager' : 'Available'}</span>
                  : <button className="button button-secondary small" disabled={!canSubscribe || !tier} title={canSubscribe ? undefined : needs('subscribe')} onClick={() => tier && setPending({ offer, tier })}>Subscribe</button>)
                : group.id === 'engagements' && !readOnly ? <Link className="button button-secondary small" to="/engagements">Start plan</Link>
                : <span className="chip">{group.id === 'catalogue' ? 'Catalogue only' : 'Not for sale'}</span>}
            </div>
          </article>
        })}</div>
      </Panel>
    })}
    {catalogue.bundles.map(bundle => <Panel key={bundle.id} title={`Bundle · ${bundle.id}`} meta={`${bundle.required_offers.join(' + ')} · ${bundle.studio_packs_per_month} Studio packs a month`}>
      <div className="price">{money(bundle.monthly_minor, bundle.currency)} a month<small>setup {money(bundle.setup_minor, bundle.currency)} · reference rate</small></div>
    </Panel>)}
    {pending && <Confirm title={`Subscribe to ${pending.offer.id} / ${pending.offer.name}`} confirmLabel="Record subscription" busy={busy} onCancel={() => setPending(null)} onConfirm={() => void subscribe()}>
      <p><strong>{tenant?.name}</strong> will be subscribed to the <strong>{pending.tier.id}</strong> tier at the reference rate of <strong>{pending.tier.pack_minor ? `${money(pending.tier.pack_minor)} per pack` : `${money(pending.tier.monthly_minor)} a month plus ${money(pending.tier.setup_minor)} setup`}</strong>.</p>
      <p style={{ marginTop: 8 }}>This records the subscription only. Nothing is charged, and billing still needs its own cycle once prices are confirmed.</p>
    </Confirm>}
  </div>
}
