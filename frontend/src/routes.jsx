import { WorkspaceApp } from './WorkspaceApp.jsx'
import { Empty } from './components/Empty.jsx'
import { AppointmentDetail } from './pages/AppointmentDetail.jsx'
import { Calendar } from './pages/Calendar.jsx'
import { CallDetail } from './pages/CallDetail.jsx'
import { Calls } from './pages/Calls.jsx'
import { Contacts } from './pages/Contacts.jsx'
import { Dashboard } from './pages/Dashboard.jsx'
import { GraphCalendar } from './pages/GraphCalendar.jsx'
import { GraphInbox } from './pages/GraphInbox.jsx'
import { Inbox } from './pages/Inbox.jsx'
import { Login } from './pages/Login.jsx'
import { Profile } from './pages/Profile.jsx'
import { TeamsConfig } from './pages/TeamsConfig.jsx'
import { TeamsCustomerTab } from './pages/TeamsCustomerTab.jsx'
import { TeamsTab } from './pages/TeamsTab.jsx'

export function AppRoutes() {
  if (location.pathname === '/teams/config/') return <TeamsConfig/>
  if (location.pathname === '/teams/customer/') return <TeamsCustomerTab/>
  if (location.pathname === '/teams/tab/') return <TeamsTab/>
  return <WorkspaceApp/>
}

export function WorkspaceRoutes({ session, section, id, props, profileRevision, refreshProfile, loadSession, setError }) {
  if (!session.user) return <Login session={session} onLogin={loadSession}/>
  if (section === 'profile') return <Profile key={session.user.id} user={session.user} revision={profileRevision} onUpdate={refreshProfile}/>
  if (!session.agent) return <div class="provision"><div class="eyebrow">You're signed in</div><h1>Your workspace is on its way.</h1><p class="muted">Your account is not linked to an agent workspace yet. Ask your administrator to provision your access, then check again.</p><div class="actions" style="margin-top:25px;justify-content:center"><button class="button" onClick={() => loadSession().catch(error => setError(error.message))}>Check access</button><a class="button secondary" href="#/profile">My profile</a></div></div>
  if (section === 'dashboard') return <Dashboard user={session.user} {...props}/>
  if (section === 'calls') return id ? <CallDetail key={id} id={id} {...props}/> : <Calls {...props}/>
  if (section === 'appointments' && id) return <AppointmentDetail key={id} id={id} {...props}/>
  if (section === 'calendar') return session.user.graph_enabled ? <GraphCalendar {...props}/> : <Calendar {...props}/>
  if (section === 'contacts') return <Contacts {...props}/>
  if (section === 'inbox') return session.user.graph_enabled ? <GraphInbox {...props}/> : <Inbox id={id} {...props}/>
  return <Empty title="This page wandered off"><a class="link" href="#/dashboard">Return to your overview →</a></Empty>
}
