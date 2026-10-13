import { useState } from 'preact/hooks'
import { useGraphPages } from '../hooks/useGraphPages.ts'
import { dateTime } from '../utils/data.ts'
import { Empty } from '../components/Empty.tsx'
import { GraphLink } from '../components/GraphLink.tsx'
import { GraphPagination } from '../components/GraphPagination.tsx'
import { PageHeading } from '../components/PageHeading.tsx'
import { Panel } from '../components/Panel.tsx'

export function GraphCalendar({ version, compact = false }) {
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1))
  const start = month.toISOString(), end = new Date(month.getFullYear(), month.getMonth() + 1, 1).toISOString()
  const resource = useGraphPages(`graph/calendar/?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`, 'events', version)
  const navigate = offset => setMonth(value => new Date(value.getFullYear(), value.getMonth() + offset, 1))
  return <>{!compact && <PageHeading title="Calendar" subtitle="Your Microsoft calendar. Events are read-only."/>}<Panel title={compact ? 'Coming up next · Microsoft calendar' : 'Microsoft calendar'} count={resource.items.length} action={compact && <a class="button quiet" href="#/calendar">View calendar</a>}>
    <div class="graph-month"><button class="button secondary" aria-label="Previous month" onClick={() => navigate(-1)}>←</button><strong aria-live="polite">{month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}</strong><button class="button secondary" aria-label="Next month" onClick={() => navigate(1)}>→</button></div>
    {resource.items.slice().sort((a, b) => new Date(a.starts_at).getTime() - new Date(b.starts_at).getTime()).filter(event => !compact || new Date(event.ends_at) >= new Date()).map(event => <div class="list-row" key={event.id}><div class="row-main"><strong>{event.subject || '(No title)'}</strong><small>{dateTime(event.starts_at)} – {dateTime(event.ends_at)}{event.location ? ` · ${event.location}` : ''}</small><GraphLink url={event.web_url}>Open event</GraphLink></div></div>)}
    {!resource.loading && !resource.error && !resource.items.length && <Empty title="No Microsoft events" icon="calendar">There are no events for this month.</Empty>}
    <GraphPagination resource={resource}/>
  </Panel></>
}
