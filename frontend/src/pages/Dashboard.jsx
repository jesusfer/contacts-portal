import { api, date, initials, useResource } from '../lib/data.js'
import { AppointmentRow } from '../components/AppointmentRow.jsx'
import { Badge } from '../components/Badge.jsx'
import { Empty } from '../components/Empty.jsx'
import { ErrorNotice } from '../components/ErrorNotice.jsx'
import { Icon } from '../components/Icon.jsx'
import { KpiTable } from '../components/KpiTable.jsx'
import { Loading } from '../components/Loading.jsx'
import { PageHeading } from '../components/PageHeading.jsx'
import { Panel } from '../components/Panel.jsx'
import { GraphCalendar } from './GraphCalendar.jsx'
import { GraphInbox } from './GraphInbox.jsx'

export function Dashboard({ user, version, openCreate }) {
  const resource = useResource('kpis/', version), appointments = useResource('appointments/', version), calls = useResource('calls/', version), inbox = useResource(user.graph_enabled ? null : 'inbox/', version)
  const authSource = <p class="muted">Signed in with {user.auth_source || "Local"}</p>
  if ([resource, appointments, calls, inbox].some(r => r.loading)) return <>{authSource}<Loading/></>
  const failed = [resource, appointments, calls, inbox].filter(r => r.error)
  if (failed.length) return <><PageHeading title="Your day, at a glance" subtitle="A little clarity for your next conversation."/>{authSource}{failed.map((r, i) => <ErrorNotice key={i} error={r.error} retry={r.retry}/>)}</>
  const kpis = resource.data, now = Date.now()
  const upcoming = appointments.data.appointments.filter(a => ['scheduled', 'rescheduled'].includes(a.call.status) && new Date(a.ends_at).getTime() >= now).sort((a,b) => new Date(a.starts_at) - new Date(b.starts_at))
  const followups = calls.data.calls.filter(call => {
    const past = call.appointment && new Date(call.appointment.ends_at).getTime() < now
    return (past && ['scheduled', 'rescheduled'].includes(call.status)) || (call.status === 'completed' && (!call.summary?.trim() || call.rating == null))
  })
  const unread = inbox.data?.emails.filter(e => !e.is_read).length || 0
  const stats = [
    ['Total conversations', kpis.total_calls, 'All-time calls across your workspace', 'calls'],
    ['Completed calls', kpis.completed_calls, 'Conversations brought to a close', 'check'],
    ['Average rating', kpis.average_rating == null ? '—' : `${Number(kpis.average_rating).toFixed(1)} / 5`, 'Rated completed conversations', 'star'],
    ['Missed calls', `${Number(kpis.missed_percentage || 0).toFixed(1)}%`, `${Number(kpis.rescheduled_percentage || 0).toFixed(1)}% rescheduled`, 'clock'],
  ]
  return <>
    <PageHeading title="Your day, at a glance" subtitle="Stay connected. Keep the conversation moving."><button class="button" onClick={() => openCreate('call')}><Icon name="plus" size={16}/>Log a call</button></PageHeading>
    <div class="welcome-strip"><div><h2>Good {new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 18 ? 'afternoon' : 'evening'}, {user.name?.split(' ')[0] || user.username}.</h2>{authSource}<p class="muted">{user.graph_enabled ? 'Your Microsoft calendar and inbox are below. CRM calls and insights remain in your workspace.' : `You have ${upcoming.length} upcoming appointment${upcoming.length !== 1 ? 's' : ''} and ${unread} unread message${unread !== 1 ? 's' : ''}. Let's make today count.`}</p></div><span class="plant" aria-hidden="true">✳</span></div>
    <div class="stats">{stats.map(([label,value,caption,icon]) => <div class="stat" key={label}><div class="stat-top">{label}<span class="stat-icon"><Icon name={icon}/></span></div><div class="stat-value">{value}</div><p class="stat-caption">{caption}</p></div>)}</div>
    <div class="dashboard-grid"><div>
      {user.graph_enabled ? <GraphCalendar version={version} compact/> : <Panel title="Coming up next" count={upcoming.length} action={<a class="button quiet" href="#/calendar">View calendar <Icon name="arrow" size={13}/></a>}>{upcoming.slice(0,4).map(a => <AppointmentRow appointment={a} key={a.id}/>)}{!upcoming.length && <Empty title="A little breathing room" icon="calendar">No upcoming appointments. Schedule your next conversation.</Empty>}</Panel>}
      <Panel title="Conversations to wrap up" count={followups.length} action={<a class="button quiet" href="#/calls">All calls <Icon name="arrow" size={13}/></a>}>{followups.slice(0,4).map(call => <a class="list-row" key={call.id} href={`#/calls/${call.id}`}><span class="avatar">{initials(call.contact.name)}</span><div class="row-main"><strong>{call.contact.name}</strong><small>{call.status === 'completed' ? 'Add a summary or rating' : 'Update the call outcome'} · {date(call.appointment?.starts_at)}</small></div><Badge status={call.status}/><Icon name="arrow" size={14}/></a>)}{!followups.length && <Empty title="All caught up" icon="check">Your past conversations are up to date.</Empty>}</Panel>
    </div><div>
      {user.graph_enabled ? <GraphInbox version={version} compact/> : <Panel title="Your inbox" count={unread} action={<a class="button quiet" href="#/inbox">View inbox <Icon name="arrow" size={13}/></a>}>{inbox.data.emails.slice().sort((a,b) => new Date(b.received_at)-new Date(a.received_at)).slice(0,4).map(email => <a class="list-row" href={`#/inbox/${email.id}`} key={email.id}><span class="avatar">{initials(email.contact.name)}</span><div class="row-main"><strong>{email.contact.name}{!email.is_read && <span class="notification-dot"/>}</strong><p class="muted truncate">{email.subject || '(No subject)'}</p><small>{date(email.received_at)}</small></div><Badge status={email.is_replied ? 'replied' : email.is_read ? 'read' : 'unread'}/></a>)}{!inbox.data.emails.length && <Empty title="A quiet inbox" icon="inbox">Incoming messages will appear here.</Empty>}</Panel>}
      <Panel title="A note on your numbers"><div class="panel-body"><p class="muted">Your insights cover all time. Missed and rescheduled percentages use non-scheduled calls as their denominator. Average ratings include rated, completed calls only.</p></div></Panel>
    </div></div>
    <div class="dashboard-grid"><KpiTable title="Performance by contact" rows={kpis.by_contact || []}/><KpiTable title="Performance by company" rows={kpis.by_company || []}/></div>
  </>
}
