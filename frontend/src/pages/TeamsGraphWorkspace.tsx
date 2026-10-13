import { useEffect, useState } from 'preact/hooks'
import { GraphPresenceIndicator } from '../components/GraphPresenceIndicator.tsx'
import { ErrorNotice } from '../components/ErrorNotice.tsx'
import { Icon } from '../components/Icon.tsx'
import { PageHeading } from '../components/PageHeading.tsx'
import { UserAvatar } from '../components/UserAvatar.tsx'
import { GraphCalendar } from './GraphCalendar.tsx'
import { GraphInbox } from './GraphInbox.tsx'

export function TeamsGraphWorkspace({ user, error, opening, openDashboard }) {
  const [route, setRoute] = useState(location.hash.slice(1)), [version, setVersion] = useState(0)
  useEffect(() => {
    const changed = () => setRoute(location.hash.slice(1))
    window.addEventListener('hashchange', changed)
    return () => window.removeEventListener('hashchange', changed)
  }, [])
  return <main class="content">
    <PageHeading title="Gather for Teams" subtitle="Your Microsoft 365 data. CRM data is available in the full dashboard."/>
    <div class="actions"><UserAvatar user={user} revision={version}/><strong>{user.name} <GraphPresenceIndicator user={user}/></strong><span>{user.email}</span></div>
    <nav class="actions" aria-label="Microsoft 365 navigation"><a class="button secondary" href="#/overview">Overview</a><a class="button secondary" href="#/calendar">Calendar</a><a class="button secondary" href="#/inbox">Inbox</a><button class="button secondary" onClick={() => setVersion(n => n + 1)}>Refresh</button><button class="button" disabled={opening} onClick={openDashboard}>{opening ? 'Opening dashboard…' : 'Open full dashboard'}</button></nav>
    <ErrorNotice error={error}/>
    {route === '/calendar' ? <GraphCalendar version={version}/> : route === '/inbox' ? <GraphInbox version={version}/> : <div class="dashboard-grid"><GraphCalendar version={version}/><GraphInbox version={version}/></div>}
  </main>
}
