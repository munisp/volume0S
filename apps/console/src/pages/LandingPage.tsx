import { ArrowRight, Banknote, Check, LockKeyhole, LogIn, ShieldCheck, UserRoundCheck } from 'lucide-react'
import { useSession } from '../state/session-context'
import { money } from '../lib/format'
import type { Offer } from '../lib/types'
import { Mark } from '../components/ui'

/** Plain descriptions of the offers a business can subscribe to, from the repository's own scope and entitlements. */
const DESCRIBE: Record<string, { line: string; points: string[] }> = {
  '01': { line: 'Win back customers who stopped buying, with reactivation campaigns to your own customer list.', points: ['Email, SMS and WhatsApp touches', 'Suppressions and opt-outs honoured on every send'] },
  '02': { line: 'A steady monthly rhythm of posts and messages, drafted for you and published only once approved.', points: ['Email, SMS, WhatsApp and social', 'Every piece approved before it goes out'] },
  '06': { line: 'Marketplace-ready product photos from one source image, checked by a person before delivery.', points: ['1080 × 1080 for WhatsApp, 1000 × 1000 for Jumia', 'Rejected packs are never delivered'] },
}

const FLOW = [
  { id: 'prepare', title: 'Prepare', copy: 'Automation drafts the campaign, generates the images or writes the plan.' },
  { id: 'review', title: 'Human review', copy: 'A person approves the exact draft. Nothing moves on without that decision.', gate: true },
  { id: 'reserve', title: 'Reserve funds', copy: 'The cost is held from the client\'s prepaid balance, so it can\'t be spent twice.' },
  { id: 'execute', title: 'Execute', copy: 'The work is sent or produced, with suppressions and the kill switch checked first.' },
  { id: 'settle', title: 'Settle & deliver', copy: 'The final cost is charged. If anything failed, the reservation is released.' },
]

function fromPrice(offer: Offer): string | null {
  const packs = offer.tiers.map(tier => tier.pack_minor ?? 0).filter(Boolean)
  if (packs.length) return `from ${money(Math.min(...packs))} per pack`
  const monthly = offer.tiers.map(tier => tier.monthly_minor).filter(Boolean)
  return monthly.length ? `from ${money(Math.min(...monthly))} a month` : null
}

export function LandingPage() {
  const { signIn, signInAvailable, catalogue } = useSession()
  const sellable = catalogue.offers.filter(offer => Number(offer.id) <= 8 && offer.tiers.length > 0)
  const built = catalogue.offers.filter(offer => Number(offer.id) <= 8 && offer.tiers.length === 0)
  const later = catalogue.offers.filter(offer => Number(offer.id) > 8)
  const signInButton = (label: string, light = false) => <button className={`button landing-cta ${light ? 'light' : ''}`} onClick={() => void signIn('/')} disabled={!signInAvailable}>
    <LogIn size={15} /> {label}
  </button>

  return <div className="landing">
    <header className="landing-bar">
      <a href="/" className="landing-brand" aria-label="VolumeOS"><Mark /><span>VolumeOS</span></a>
      <nav aria-label="Page">
        <a href="#how">How it works</a>
        <a href="#services">Services</a>
        {signInButton('Sign in', true)}
      </nav>
    </header>

    <section className="landing-hero">
      <div className="landing-hero-copy">
        <p className="landing-eyebrow">The operating platform for the Naira Volume Playbook</p>
        <h1>Run client services at volume. Keep every release in human hands.</h1>
        <p className="landing-lede">VolumeOS lets a small team deliver packaged marketing and operations services to Nigerian businesses at volume. Automation prepares the work; people approve it before anything reaches a customer or spends a kobo.</p>
        <div className="landing-actions">
          {signInButton('Sign in to the console', true)}
          <a className="button landing-ghost" href="#services">See the services <ArrowRight size={15} /></a>
        </div>
        {!signInAvailable && <p className="landing-note">Sign-in isn't configured for this deployment.</p>}
      </div>
      <figure className="landing-flow" aria-label="How a piece of work moves through VolumeOS">
        <figcaption>Every piece of work, every time</figcaption>
        <ol>{FLOW.map((step, index) => <li key={step.id} className={step.gate ? 'is-gate' : ''}>
          <span className="landing-flow-index">{String(index + 1).padStart(2, '0')}</span>
          <div><strong>{step.title}</strong>{step.gate && <em>Person required</em>}</div>
        </li>)}</ol>
        <p>Approval lets work continue. It is never mistaken for delivery.</p>
      </figure>
    </section>

    <section className="landing-section" id="how" aria-labelledby="how-title">
      <div className="landing-section-head">
        <p className="landing-kicker">How it works</p>
        <h2 id="how-title">Automation does the preparation. People make the decisions.</h2>
      </div>
      <ol className="landing-steps">{FLOW.map((step, index) => <li key={step.id} className={step.gate ? 'is-gate' : ''}>
        <span>{String(index + 1).padStart(2, '0')}</span><h3>{step.title}</h3><p>{step.copy}</p>
      </li>)}</ol>
    </section>

    <section className="landing-section" id="services" aria-labelledby="services-title">
      <div className="landing-section-head">
        <p className="landing-kicker">Services</p>
        <h2 id="services-title">Packaged services, numbered and priced.</h2>
        <p>Reference rates from the current price book. Your account manager confirms the final price before anything is billed.</p>
      </div>
      {catalogue.load === 'loading' && <p className="landing-note">Loading services…</p>}
      {catalogue.load === 'error' && <p className="landing-note">The service list couldn't be loaded right now.</p>}
      <div className="landing-offers">{sellable.map(offer => {
        const about = DESCRIBE[offer.id]
        const price = fromPrice(offer)
        return <article key={offer.id} className="landing-offer">
          <div className="landing-offer-top"><span className="landing-offer-num">{offer.id}</span><span className="landing-offer-tiers">{offer.tiers.map(tier => tier.id).join(' · ')}</span></div>
          <h3>{offer.name}</h3>
          {about && <p>{about.line}</p>}
          {about && <ul>{about.points.map(point => <li key={point}><Check size={14} />{point}</li>)}</ul>}
          {price && <div className="landing-offer-price">{price}</div>}
        </article>
      })}</div>
      {(built.length > 0 || later.length > 0) && <div className="landing-more">
        {built.length > 0 && <p><strong>Built, pricing to come:</strong> {built.map(offer => `${offer.id} ${offer.name}`).join(' · ')}.</p>}
        {later.length > 0 && <p><strong>In the catalogue for later:</strong> {later.length} more services, from a point-of-sale suite to plan-only engagements such as website makeovers and course tutors.</p>}
      </div>}
    </section>

    <section className="landing-section landing-principles" aria-label="What VolumeOS guarantees">
      <div><UserRoundCheck size={20} /><h3>People approve, every time</h3><p>Drafts, images, plans and artifacts wait for a person. Approval is tied to the exact version that was reviewed.</p></div>
      <div><Banknote size={20} /><h3>Money you can trace</h3><p>Prepaid balances live in a double-entry ledger. A top-up counts only after the payment provider confirms it.</p></div>
      <div><LockKeyhole size={20} /><h3>Each business stays separate</h3><p>Every sign-in is scoped to one business. Its records, work and money are never visible to another.</p></div>
    </section>

    <section className="landing-close">
      <div><ShieldCheck size={22} /><div><h2>Already working with us?</h2><p>Sign in to see your services, approvals and balance.</p></div></div>
      {signInButton('Sign in', true)}
    </section>

    <footer className="landing-foot"><span>VolumeOS</span><span>Operating platform for the Naira Volume Playbook</span></footer>
  </div>
}
