import { useMemo, type ReactNode } from 'react'
import { matchPath, Navigate, Route, Routes, useLocation } from 'react-router'
import {
  BookOpen, Building2, ClipboardCheck, Gauge, Inbox, Landmark, Layers3, LineChart, Loader2, MessagesSquare,
  Megaphone, Image as ImageIcon, PackageCheck, Settings2, UserRound, Wallet, Workflow, Users,
} from 'lucide-react'
import { SessionProvider } from './state/session'
import { useSession } from './state/session-context'
import { OperationsProvider } from './state/operations'
import { useOperations } from './state/operations-context'
import { sees, roleLabel, type Area } from './lib/access'
import { Shell, type NavGroup } from './components/Shell'
import { Empty, Mark, Panel } from './components/ui'
import { OverviewPage } from './pages/OverviewPage'
import { ApprovalsPage } from './pages/ApprovalsPage'
import { OperationsPage } from './pages/OperationsPage'
import { CataloguePage } from './pages/CataloguePage'
import { ClientPage } from './pages/ClientPage'
import { ClientsPage } from './pages/ClientsPage'
import { CampaignsPage } from './pages/CampaignsPage'
import { StudioPage } from './pages/StudioPage'
import { EngagementsPage, FinancePage, FrontDeskPage, OfferJobsPage } from './pages/ServicePages'
import { AccountPage, AdminPage, ForbiddenPage, IntelligencePage, NotFoundPage } from './pages/AdminPages'
import { ClientHomePage, PaymentReturnPage, ProfilePage, WalletPage } from './pages/ClientPages'
import { LandingPage } from './pages/LandingPage'

type RouteDef = { path: string; title: string; section?: string; sectionTo?: string; area?: Area; element: ReactNode }

const OPERATOR_ROUTES: RouteDef[] = [
  { path: '/', title: 'Overview', area: 'overview', element: <OverviewPage /> },
  { path: '/approvals', title: 'Approvals', section: 'Work', area: 'approvals', element: <ApprovalsPage /> },
  { path: '/engagements', title: 'Engagements', section: 'Work', area: 'engagements', element: <EngagementsPage /> },
  { path: '/engagements/:id', title: 'Engagement', section: 'Engagements', sectionTo: '/engagements', area: 'engagements', element: <EngagementsPage /> },
  { path: '/operations', title: 'Operations', section: 'Work', area: 'operations', element: <OperationsPage /> },
  { path: '/services/campaigns', title: 'Campaigns', section: 'Services', area: 'services', element: <CampaignsPage /> },
  { path: '/services/campaigns/:id', title: 'Campaign', section: 'Campaigns', sectionTo: '/services/campaigns', area: 'services', element: <CampaignsPage /> },
  { path: '/services/studio', title: 'Shopfront Studio', section: 'Services', area: 'services', element: <StudioPage /> },
  { path: '/services/studio/:id', title: 'Studio pack', section: 'Shopfront Studio', sectionTo: '/services/studio', area: 'services', element: <StudioPage /> },
  { path: '/services/offer-jobs', title: 'Offer jobs', section: 'Services', area: 'services', element: <OfferJobsPage /> },
  { path: '/services/offer-jobs/:id', title: 'Offer job', section: 'Offer jobs', sectionTo: '/services/offer-jobs', area: 'services', element: <OfferJobsPage /> },
  { path: '/services/front-desk', title: 'Front desk', section: 'Services', area: 'services', element: <FrontDeskPage /> },
  { path: '/client', title: 'Client', section: 'Business', area: 'client', element: <ClientPage /> },
  { path: '/catalogue', title: 'Service catalogue', section: 'Business', area: 'catalogue', element: <CataloguePage /> },
  { path: '/finance', title: 'Finance', section: 'Business', area: 'finance', element: <FinancePage /> },
  { path: '/intelligence', title: 'Intelligence', section: 'Insight', area: 'intelligence', element: <IntelligencePage /> },
  { path: '/admin', title: 'Administration', section: 'Admin', area: 'administration', element: <AdminPage /> },
  { path: '/account', title: 'Your access', section: 'Account', element: <AccountPage /> },
  { path: '/billing/callback', title: 'Payment submitted', section: 'Finance', sectionTo: '/finance', element: <PaymentReturnPage backTo="/finance" backLabel="Back to finance" /> },
]

/** Platform operators only (the agency's own staff). */
const PLATFORM_ROUTES: RouteDef[] = [
  { path: '/clients', title: 'Clients', section: 'Agency', element: <ClientsPage /> },
  { path: '/account', title: 'Your access', section: 'Account', element: <AccountPage /> },
]

/** Inside a client, the agency pages a platform operator still reaches (the rest are already operator routes). */
const AGENCY_ROUTES = PLATFORM_ROUTES.filter(route => route.path === '/clients')

const CLIENT_ROUTES: RouteDef[] = [
  { path: '/', title: 'Home', element: <ClientHomePage /> },
  { path: '/services', title: 'Services', element: <CataloguePage readOnly /> },
  { path: '/engagements', title: 'Engagements', element: <EngagementsPage clientView /> },
  { path: '/engagements/:id', title: 'Engagement', section: 'Engagements', sectionTo: '/engagements', element: <EngagementsPage clientView /> },
  { path: '/wallet', title: 'Wallet & billing', element: <WalletPage /> },
  { path: '/profile', title: 'Business profile', element: <ProfilePage /> },
  { path: '/billing/callback', title: 'Payment submitted', section: 'Wallet & billing', sectionTo: '/wallet', element: <PaymentReturnPage backTo="/wallet" backLabel="Back to your wallet" /> },
  { path: '/account', title: 'Your access', section: 'Account', element: <AccountPage /> },
]

function useCrumbs(routes: RouteDef[]) {
  const { pathname } = useLocation()
  return useMemo(() => {
    const match = routes.find(route => matchPath({ path: route.path, end: true }, pathname))
    if (!match) return [{ label: 'Not found' }]
    return [...(match.section ? [{ label: match.section, to: match.sectionTo }] : []), { label: match.title }]
  }, [pathname, routes])
}

function Guard({ route }: { route: RouteDef }) {
  const { roles } = useSession()
  if (route.area && !sees(roles, route.area)) return <ForbiddenPage area={route.title} />
  return <>{route.element}</>
}

/** A platform operator who hasn't picked a client yet: only the client list (and their own access) make sense. */
function PlatformStartApp() {
  const { roles } = useSession()
  const crumbs = useCrumbs(PLATFORM_ROUTES)
  return <Shell groups={[{ label: 'Agency', items: [{ to: '/clients', label: 'Clients', icon: Users }] }]} crumbs={crumbs}
    footnote={<>Signed in as <strong>{[...roles].map(roleLabel).join(', ')}</strong> · platform</>}>
    <Routes>
      {PLATFORM_ROUTES.map(route => <Route key={route.path} path={route.path} element={route.element} />)}
      <Route path="*" element={<Navigate to="/clients" replace />} />
    </Routes>
  </Shell>
}

function OperatorApp() {
  const { roles, platform, actingTenant } = useSession()
  const { items } = useOperations()
  const crumbs = useCrumbs(platform ? OPERATOR_ROUTES.concat(AGENCY_ROUTES) : OPERATOR_ROUTES)
  const waiting = items.filter(item => item.state === 'needs-review').length
  if (platform && !actingTenant) return <PlatformStartApp />
  const groups: NavGroup[] = [
    ...(platform ? [{ label: 'Agency', items: [{ to: '/clients', label: 'Clients', icon: Users }] }] : []),
    { items: [{ to: '/', label: 'Overview', icon: Gauge, end: true }] },
    { label: 'Work', items: [
      ...(sees(roles, 'approvals') ? [{ to: '/approvals', label: 'Approvals', icon: Inbox, count: waiting }] : []),
      { to: '/engagements', label: 'Engagements', icon: ClipboardCheck },
      { to: '/operations', label: 'Operations', icon: Workflow },
    ] },
    { label: 'Services', items: [
      { to: '/services/campaigns', label: 'Campaigns', icon: Megaphone },
      { to: '/services/studio', label: 'Shopfront Studio', icon: ImageIcon },
      { to: '/services/offer-jobs', label: 'Offer jobs', icon: PackageCheck },
      { to: '/services/front-desk', label: 'Front desk', icon: MessagesSquare },
    ] },
    { label: 'Business', items: [
      { to: '/client', label: 'Client', icon: Building2 },
      { to: '/catalogue', label: 'Catalogue', icon: Layers3 },
      ...(sees(roles, 'finance') ? [{ to: '/finance', label: 'Finance', icon: Landmark }] : []),
    ] },
    ...(sees(roles, 'intelligence') ? [{ label: 'Insight', items: [{ to: '/intelligence', label: 'Intelligence', icon: LineChart, soon: 'Not built' }] }] : []),
    ...(sees(roles, 'administration') ? [{ label: 'Admin', items: [{ to: '/admin', label: 'Administration', icon: Settings2 }] }] : []),
  ].filter(group => group.items.length > 0)
  return <Shell groups={groups} crumbs={crumbs} footnote={<>Signed in as <strong>{[...roles].map(roleLabel).join(', ') || 'no role'}</strong>{platform ? ' · platform' : ''}</>}>
    <Routes>
      {OPERATOR_ROUTES.map(route => <Route key={route.path} path={route.path} element={<Guard route={route} />} />)}
      {platform && AGENCY_ROUTES.map(route => <Route key={route.path} path={route.path} element={route.element} />)}
      <Route path="/onboarding" element={<Navigate to="/client" replace />} />
      <Route path="/offers" element={<Navigate to="/catalogue" replace />} />
      <Route path="/billing" element={<Navigate to="/finance" replace />} />
      <Route path="/settings" element={<Navigate to="/admin" replace />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  </Shell>
}

function ClientApp() {
  const crumbs = useCrumbs(CLIENT_ROUTES)
  const groups: NavGroup[] = [{ items: [
    { to: '/', label: 'Home', icon: Gauge, end: true },
    { to: '/services', label: 'Services', icon: BookOpen },
    { to: '/engagements', label: 'Engagements', icon: ClipboardCheck },
    { to: '/wallet', label: 'Wallet & billing', icon: Wallet },
    { to: '/profile', label: 'Business profile', icon: UserRound },
  ] }]
  return <Shell groups={groups} crumbs={crumbs} footnote="Your workspace with your agency">
    <Routes>
      {CLIENT_ROUTES.map(route => <Route key={route.path} path={route.path} element={route.element} />)}
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  </Shell>
}

function NoRoles() {
  const { signOut, user } = useSession()
  return <div className="loading-screen" style={{ padding: 16 }}>
    <Panel title="Your account has no VolumeOS role yet">
      <Empty title={String(user?.profile.email ?? 'Signed in')}>Ask an administrator to give you a role (for example Delivery, Sales or Client owner). Until then there is nothing you can see here.</Empty>
      <button className="button button-secondary" onClick={() => void signOut()}>Sign out</button>
    </Panel>
  </div>
}

function Root() {
  const { ready, user, persona } = useSession()
  if (!ready) return <div className="loading-screen" role="status"><Mark /><Loader2 className="spin" size={18} /><span>Loading VolumeOS</span></div>
  if (!user) return <LandingPage />
  if (persona === 'none') return <NoRoles />
  return <OperationsProvider>{persona === 'client' ? <ClientApp /> : <OperatorApp />}</OperationsProvider>
}

export default function App() {
  return <SessionProvider><Root /></SessionProvider>
}

