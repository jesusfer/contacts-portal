import { Empty } from './components/Empty.tsx'
import { AppointmentDetail } from './pages/AppointmentDetail.tsx'
import { Calendar } from './pages/Calendar.tsx'
import { CallDetail } from './pages/CallDetail.tsx'
import { Calls } from './pages/Calls.tsx'
import { Contacts } from './pages/Contacts.tsx'
import { Dashboard } from './pages/Dashboard.tsx'
import { GraphCalendar } from './pages/GraphCalendar.tsx'
import { GraphInbox } from './pages/GraphInbox.tsx'
import { Inbox } from './pages/Inbox.tsx'
import { Login } from './pages/Login.tsx'
import { Profile } from './pages/Profile.tsx'
export function AppRoutes({ workspace, pages }) {
  const Page = pages[location.pathname]
  return Page ? <Page /> : workspace
}

export function WorkspaceRoutes({
  session,
  section,
  id,
  props,
  profileRevision,
  refreshProfile,
  loadSession,
  setError,
}) {
  if (!session.user) return <Login session={session} onLogin={loadSession} />
  if (section === 'profile')
    return (
      <Profile
        key={session.user.id}
        user={session.user}
        revision={profileRevision}
        onUpdate={refreshProfile}
      />
    )
  if (!session.agent)
    return (
      <div class="provision">
        <div class="eyebrow">You're signed in</div>
        <h1>Your workspace is on its way.</h1>
        <p class="muted">
          Your account is not linked to an agent workspace yet. Ask your administrator to provision
          your access, then check again.
        </p>
        <div class="actions" style="margin-top:25px;justify-content:center">
          <button
            class="button"
            onClick={() => loadSession().catch((error) => setError(error.message))}
          >
            Check access
          </button>
          <a class="button secondary" href="#/profile">
            My profile
          </a>
        </div>
      </div>
    )
  if (section === 'dashboard') return <Dashboard user={session.user} {...props} />
  if (section === 'calls')
    return id ? <CallDetail key={id} id={id} {...props} /> : <Calls {...props} />
  if (section === 'appointments' && id) return <AppointmentDetail key={id} id={id} {...props} />
  if (section === 'calendar')
    return session.user.graph_enabled ? <GraphCalendar {...props} /> : <Calendar {...props} />
  if (section === 'contacts') return <Contacts {...props} />
  if (section === 'inbox')
    return session.user.graph_enabled ? <GraphInbox {...props} /> : <Inbox id={id} {...props} />
  return (
    <Empty title="This page wandered off">
      <a class="link" href="#/dashboard">
        Return to your overview →
      </a>
    </Empty>
  )
}
