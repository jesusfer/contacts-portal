import { useEffect, useState } from 'preact/hooks'
import { api, date } from './utils/data.ts'
import type { Session } from './utils/data.ts'
import { Dialog } from './components/Dialog.tsx'
import { ErrorNotice } from './components/ErrorNotice.tsx'
import { GraphPresenceIndicator } from './components/GraphPresenceIndicator.tsx'
import { Icon } from './components/Icon.tsx'
import { Loading } from './components/Loading.tsx'
import { UserAvatar } from './components/UserAvatar.tsx'
import { CreateForm } from './pages/CreateForm.tsx'
import { Login } from './pages/Login.tsx'
import { WorkspaceRoutes } from './routes.tsx'

const labels = {
  dashboard: 'Overview',
  calls: 'Call log',
  calendar: 'Calendar',
  contacts: 'Contacts',
  inbox: 'Inbox',
  appointments: 'Appointment',
  profile: 'My profile',
  access: 'Workspace access',
}

interface WorkspaceAppProps {
  initialSession?: Session | null
  embedded?: boolean
  onSessionEnd?: () => void
}

export function WorkspaceApp({
  initialSession = null,
  embedded = false,
  onSessionEnd = () => {},
}: WorkspaceAppProps) {
  const [session, setSession] = useState<Session | null>(initialSession),
    [loading, setLoading] = useState(!initialSession),
    [error, setError] = useState(''),
    [route, setRoute] = useState(location.hash.slice(1) || '/dashboard'),
    [version, setVersion] = useState(0),
    [modal, setModal] = useState<{ type: string; initialId?: string | number } | null>(null),
    [mobile, setMobile] = useState(false),
    [signingOut, setSigningOut] = useState(false)
  const [profileRevision, setProfileRevision] = useState(0)
  async function loadSession() {
    setError('')
    const value = await api<Session>('session/')
    setSession(value)
    return value
  }
  async function refreshProfile() {
    setProfileRevision((n) => n + 1)
    await loadSession()
  }
  useEffect(() => {
    if (!initialSession)
      loadSession()
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false))
    const hash = () => {
      setRoute(location.hash.slice(1) || '/dashboard')
      setMobile(false)
      setModal(null)
      window.scrollTo(0, 0)
    }
    const expired = () => {
      setSession({ user: null, agent: null, providers: session?.providers || [] })
      setModal(null)
      if (embedded) onSessionEnd()
      else loadSession().catch((e) => setError(e.message))
    }
    window.addEventListener('hashchange', hash)
    window.addEventListener('session-expired', expired)
    return () => {
      window.removeEventListener('hashchange', hash)
      window.removeEventListener('session-expired', expired)
    }
  }, [])
  useEffect(() => {
    if (session?.user && (!location.hash || route === '/login' || route === '/'))
      location.hash = '/dashboard'
  }, [session, route])
  async function logout() {
    setSigningOut(true)
    setError('')
    try {
      await api('logout/', { method: 'POST' })
      if (embedded) onSessionEnd()
      else {
        await loadSession()
        location.hash = '/login'
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setSigningOut(false)
    }
  }
  const refresh = () => setVersion((n) => n + 1)
  const openCreate = (type, initialId) => setModal({ type, initialId })
  function done(type, result) {
    setModal(null)
    refresh()
    const id = result.id || result[type]?.id
    if (id && ['call', 'appointment'].includes(type))
      location.hash = `/${type === 'call' ? 'calls' : 'appointments'}/${id}`
  }
  if (loading) return <Loading />
  if (!session)
    return (
      <div class="provision">
        <div class="brand" style="color:#294733;justify-content:center;margin-bottom:25px">
          <span class="brand-mark">g</span>gather.
        </div>
        <ErrorNotice
          error={error}
          retry={() => {
            setLoading(true)
            loadSession()
              .catch((e) => setError(e.message))
              .finally(() => setLoading(false))
          }}
        />
      </div>
    )
  if (!session.user)
    return embedded ? (
      <ErrorNotice error="Your Teams session has ended. Reload this tab to sign in again." />
    ) : (
      <Login session={session} onLogin={loadSession} />
    )
  const parts = route.split('/').filter(Boolean),
    section = !session.agent && parts[0] !== 'profile' ? 'access' : parts[0] || 'dashboard',
    id = /^\d+$/.test(parts[1] || '') ? parts[1] : null
  const props = { version, refresh, openCreate }
  const page = (
    <WorkspaceRoutes
      session={session}
      section={section}
      id={id}
      props={props}
      profileRevision={profileRevision}
      refreshProfile={refreshProfile}
      loadSession={loadSession}
      setError={setError}
    />
  )
  return (
    <>
      <a
        class="skip-link"
        href="#main-content"
        onClick={(e) => {
          e.preventDefault()
          document.getElementById('main-content').focus()
        }}
      >
        Skip to content
      </a>
      <div class="shell">
        {mobile && (
          <button
            class="nav-backdrop"
            aria-label="Close navigation"
            onClick={() => setMobile(false)}
          />
        )}
        <aside class={`sidebar ${mobile ? 'open' : ''}`}>
          <div style="display:flex;align-items:center;justify-content:space-between">
            <a class="brand" href={session.agent ? '#/dashboard' : '#/access'}>
              <span class="brand-mark">g</span>gather<span style="color:#b8d99d">.</span>
            </a>
            <button
              class="menu-toggle"
              style="color:#d5edb7;margin:0"
              aria-label="Close navigation"
              onClick={() => setMobile(false)}
            >
              <Icon name="close" />
            </button>
          </div>
          <div class="workspace">Your workspace</div>
          <nav class="nav" aria-label="Main navigation">
            {(session.agent
              ? ['dashboard', 'calls', 'calendar', 'contacts', 'inbox', 'profile']
              : ['access', 'profile']
            ).map((item) => (
              <a
                href={`#/${item}`}
                key={item}
                class={
                  section === item || (item === 'calendar' && section === 'appointments')
                    ? 'active'
                    : ''
                }
                aria-current={
                  section === item || (item === 'calendar' && section === 'appointments')
                    ? 'page'
                    : undefined
                }
                onClick={() => setMobile(false)}
              >
                <Icon name={item} />
                {labels[item]}
              </a>
            ))}
          </nav>
          <div class="sidebar-bottom">
            <a
              href="/accounts/3rdparty/"
              target={embedded ? '_blank' : undefined}
              rel={embedded ? 'noopener noreferrer' : undefined}
            >
              Manage identity connections ↗
            </a>
            <div class="user">
              <UserAvatar key={session.user.id} user={session.user} revision={profileRevision} />
              <div>
                <strong>
                  {session.user.name || session.user.username}{' '}
                  <GraphPresenceIndicator user={session.user} />
                </strong>
                <small>{session.agent?.companies?.[0]?.name || 'Workspace access pending'}</small>
              </div>
            </div>
            <button class="signout" disabled={signingOut} onClick={logout}>
              {signingOut ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        </aside>
        <main class="main">
          <header class="topbar">
            <div style="display:flex;align-items:center">
              <button
                class="menu-toggle"
                aria-label={mobile ? 'Close navigation' : 'Open navigation'}
                aria-expanded={mobile}
                onClick={() => setMobile(!mobile)}
              >
                <Icon name={mobile ? 'close' : 'menu'} />
              </button>
              <span class="mobile-brand">gather.</span>
              <span class="breadcrumb">
                Workspace <span style="margin-left:10px">/</span>
                <strong>{labels[section] || 'Page'}</strong>
              </span>
            </div>
            <div class="today">
              <Icon name="calendar" size={15} />
              <span class="date-label">
                {date(new Date(), { weekday: 'short', year: 'numeric' })}
              </span>
            </div>
          </header>
          <div class="content" id="main-content" tabIndex={-1}>
            <ErrorNotice error={error} />
            <ErrorNotice error={session.graph_error || session.user.graph_error} />
            {page}
          </div>
        </main>
      </div>
      {modal && session.agent && (
        <Dialog
          title={
            modal.type === 'call'
              ? 'Log a conversation'
              : modal.type === 'contact'
                ? 'Add a new contact'
                : modal.type === 'company'
                  ? 'Add a company'
                  : 'Schedule a conversation'
          }
          close={() => setModal(null)}
        >
          <CreateForm {...modal} done={done} close={() => setModal(null)} />
        </Dialog>
      )}
    </>
  )
}
