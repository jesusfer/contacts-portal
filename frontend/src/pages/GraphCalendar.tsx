// Displays up to five Microsoft Graph calendar events with month navigation.
import { useMemo, useState } from 'preact/hooks'
import { dateTime, useResource } from '../utils/data.ts'
import { Empty } from '../components/Empty.tsx'
import { GraphLink } from '../components/GraphLink.tsx'
import { Loading } from '../components/Loading.tsx'
import { ErrorNotice } from '../components/ErrorNotice.tsx'
import { PageHeading } from '../components/PageHeading.tsx'
import { Panel } from '../components/Panel.tsx'

export function GraphCalendar({ version, compact = false }) {
  const [month, setMonth] = useState(
    () => new Date(new Date().getFullYear(), new Date().getMonth(), 1),
  )
  const start = useMemo(() => {
    const now = new Date()
    return compact &&
      month.getFullYear() === now.getFullYear() &&
      month.getMonth() === now.getMonth()
      ? now.toISOString()
      : month.toISOString()
  }, [month, compact, version])
  const end = new Date(month.getFullYear(), month.getMonth() + 1, 1).toISOString()
  const resource = useResource(
    `graph/calendar/?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
    version,
  )
  const events = resource.data?.events || []
  const navigate = (offset) =>
    setMonth((value) => new Date(value.getFullYear(), value.getMonth() + offset, 1))
  return (
    <>
      {!compact && (
        <PageHeading
          title="Calendar"
          subtitle="Up to 5 events for the selected month. Read-only."
        />
      )}
      <Panel
        title={compact ? 'Coming up next · Microsoft calendar' : 'Microsoft calendar'}
        count={events.length}
        action={
          compact && (
            <a class="button quiet" href="#/calendar">
              View calendar
            </a>
          )
        }
      >
        <div class="graph-month">
          <button class="button secondary" aria-label="Previous month" onClick={() => navigate(-1)}>
            ←
          </button>
          <strong aria-live="polite">
            {month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}
          </strong>
          <button class="button secondary" aria-label="Next month" onClick={() => navigate(1)}>
            →
          </button>
        </div>
        {events
          .slice()
          .sort((a, b) => new Date(a.starts_at).getTime() - new Date(b.starts_at).getTime())
          .filter((event) => !compact || new Date(event.ends_at) >= new Date())
          .map((event) => (
            <div class="list-row" key={event.id}>
              <div class="row-main">
                <strong>{event.subject || '(No title)'}</strong>
                <small>
                  {dateTime(event.starts_at)} – {dateTime(event.ends_at)}
                  {event.location ? ` · ${event.location}` : ''}
                </small>
                <GraphLink url={event.web_url}>Open event</GraphLink>
              </div>
            </div>
          ))}
        {!resource.loading && !resource.error && !events.length && (
          <Empty title="No Microsoft events" icon="calendar">
            There are no events for this month.
          </Empty>
        )}
        {resource.loading && <Loading />}
        <ErrorNotice error={resource.error} retry={resource.retry} />
      </Panel>
    </>
  )
}
