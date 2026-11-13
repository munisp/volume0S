import { useEffect, useState, type ReactNode } from 'react'
import { Link, NavLink, useLocation } from 'react-router'
import { CircleAlert, CircleCheck, CloudOff, LogOut, Menu, RefreshCw, ShieldCheck, X, type LucideIcon } from 'lucide-react'
import { useSession } from '../state/session-context'
import { roleLabel, sees } from '../lib/access'
import { usePwa } from '../hooks/usePwa'
import { Mark } from './ui'

export type NavItem = { to: string; label: string; icon: LucideIcon; count?: number; soon?: string; end?: boolean }
export type NavGroup = { label?: string; items: NavItem[] }

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]?.toUpperCase()).join('') || '?'
}

export function Shell({ groups, crumbs, children, footnote }: { groups: NavGroup[]; crumbs: { label: string; to?: string }[]; children: ReactNode; footnote?: ReactNode }) {
  const { user, roles, tenant, tenantLoad, signOut, toast, dismissToast, platform, actingTenant } = useSession()
  const pwa = usePwa()
  const location = useLocation()
  // Each remembers the page it was opened on, so navigating closes it without an effect.
  const [drawerAt, setDrawerAt] = useState<string | null>(null)
  const [menuAt, setMenuAt] = useState<string | null>(null)
  const drawer = drawerAt === location.pathname
  const menu = menuAt === location.pathname
  const setDrawer = (open: boolean) => setDrawerAt(open ? location.pathname : null)
  const setMenu = (open: boolean | ((current: boolean) => boolean)) => setMenuAt((typeof open === 'function' ? open(menu) : open) ? location.pathname : null)
  const name = String(user?.profile.name ?? user?.profile.preferred_username ?? 'Signed in')

  useEffect(() => {
    if (!menu) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenuAt(null) }
    document.addEventListener('keydown', close)
    return () => document.removeEventListener('keydown', close)
  }, [menu])
  useEffect(() => {
    document.title = crumbs.length ? `${crumbs.at(-1)!.label} · VolumeOS` : 'VolumeOS'
  }, [crumbs])

  return <div className={`app-shell ${drawer ? 'drawer-open' : ''}`}>
    <a className="skip-link" href="#main">Skip to content</a>
    <aside className="rail" aria-label="VolumeOS">
      <Link to="/" className="rail-brand" aria-label="VolumeOS home"><Mark /><span><strong>VolumeOS</strong><small>Naira Volume operations</small></span></Link>
      <div className="rail-scope" title={platform ? 'Every screen shows this client only. Switch from the client list.' : 'Every screen shows this business only. Your sign-in is scoped to it.'}>
        <span>{platform ? 'Working on' : 'Business'}</span>
        <strong>{platform && !actingTenant ? 'No client chosen' : tenant?.name ?? (tenantLoad === 'loading' ? 'Loading…' : 'No business record yet')}</strong>
        {platform && <Link to="/clients" className="rail-switch">{actingTenant ? 'Switch client' : 'Choose a client'}</Link>}
      </div>
      <nav aria-label="Main">
        {groups.map((group, index) => <div className="nav-group" key={group.label ?? index}>
          {group.label && <p>{group.label}</p>}
          {group.items.map(item => <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
            <item.icon size={16} /><span>{item.label}</span>
            {item.count ? <span className="count" aria-label={`${item.count} waiting`}>{item.count}</span> : null}
            {item.soon && <span className="soon">{item.soon}</span>}
          </NavLink>)}
        </div>)}
      </nav>
      {footnote && <div className="rail-foot">{footnote}</div>}
    </aside>
    <button className="drawer-backdrop" aria-label="Close menu" onClick={() => setDrawer(false)} tabIndex={drawer ? 0 : -1} />

    <div className="workspace">
      <header className="topbar">
        <button className="icon-button menu-button" onClick={() => setDrawer(true)} aria-label="Open menu" aria-expanded={drawer}><Menu size={16} /></button>
        <nav className="crumbs" aria-label="Breadcrumb">
          {crumbs.map((crumb, index) => index < crumbs.length - 1
            ? <span key={crumb.label} className="row" style={{ gap: 6 }}>{crumb.to ? <Link to={crumb.to}>{crumb.label}</Link> : <span>{crumb.label}</span>}<span aria-hidden="true">/</span></span>
            : <strong key={crumb.label} aria-current="page">{crumb.label}</strong>)}
        </nav>
        <div className="topbar-end">
          {pwa.online
            ? <span className="signal" title="This device is online"><i /><span>Online</span></span>
            : <span className="signal off"><CloudOff size={13} /><span>Offline</span></span>}
          {user && <button className="user-button" onClick={() => setMenu(open => !open)} aria-expanded={menu} aria-haspopup="menu">
            <span className="avatar" aria-hidden="true">{initials(name)}</span><span className="who">{name}</span>
          </button>}
        </div>
        {menu && user && <div className="user-menu" role="menu">
          <header><strong>{name}</strong><span>{String(user.profile.email ?? '')}</span>
            <div className="chips">{[...roles].map(role => <span className="chip" key={role}>{roleLabel(role)}</span>)}{!roles.size && <span className="chip">No roles</span>}</div>
          </header>
          <Link to="/account" role="menuitem"><ShieldCheck size={15} /> Your access</Link>
          <button role="menuitem" onClick={() => void signOut()}><LogOut size={15} /> Sign out of this tab</button>
        </div>}
      </header>
      <main id="main" className="main" tabIndex={-1}>
        {tenant?.outbound_suspended && <div className="suspended-bar" role="status">
          <strong>Outbound messages are suspended for {tenant.name}.</strong> Nothing is sent to customers by email, SMS, WhatsApp or social until someone restores it.
          {sees(roles, 'administration') && <> <Link to="/admin">Manage in Administration</Link></>}
        </div>}
        {children}
      </main>
    </div>

    {toast && <div className={`toast toast-${toast.kind}`} role={toast.kind === 'error' ? 'alert' : 'status'} key={toast.id}>
      {toast.kind === 'success' ? <CircleCheck size={16} /> : <CircleAlert size={16} />}<span>{toast.message}</span>
      <button onClick={dismissToast} aria-label="Dismiss notification"><X size={15} /></button>
    </div>}
    {pwa.needRefresh && <div className="update-bar" role="status"><RefreshCw size={15} /><span>A new version of VolumeOS is ready.</span><button onClick={pwa.update}>Reload</button><button onClick={pwa.dismissRefresh} aria-label="Dismiss"><X size={13} /></button></div>}
  </div>
}
