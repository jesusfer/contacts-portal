// Displays read-only Microsoft Graph email messages with pagination.
import { useGraphPages } from '../hooks/useGraphPages.ts'
import { dateTime, initials } from '../utils/data.ts'
import { Badge } from '../components/Badge.tsx'
import { Empty } from '../components/Empty.tsx'
import { GraphLink } from '../components/GraphLink.tsx'
import { GraphPagination } from '../components/GraphPagination.tsx'
import { PageHeading } from '../components/PageHeading.tsx'
import { Panel } from '../components/Panel.tsx'

export function GraphInbox({ version, compact = false }) {
  const resource = useGraphPages('graph/inbox/', 'emails', version)
  return (
    <>
      {!compact && (
        <PageHeading
          title="Inbox"
          subtitle="Your Microsoft messages. Read-only; open Outlook to reply."
        />
      )}
      <Panel
        title="Microsoft inbox"
        count={resource.items.length}
        action={
          compact && (
            <a class="button quiet" href="#/inbox">
              View inbox
            </a>
          )
        }
      >
        {resource.items.map((email) => (
          <div class="list-row" key={email.id}>
            <span class="avatar">{initials(email.contact?.name)}</span>
            <div class="row-main">
              <strong>
                {email.contact?.name || email.contact?.email || 'Unknown sender'}
                {!email.is_read && <span class="notification-dot" />}
              </strong>
              <p>{email.subject || '(No subject)'}</p>
              <small>{dateTime(email.received_at)}</small>
              {!compact && <p class="graph-message-body">{email.body.slice(0, 50) || '(Empty message)'}</p>}
              <GraphLink url={email.web_url}>Open message</GraphLink>
            </div>
            <Badge status={email.is_read ? 'read' : 'unread'} />
          </div>
        ))}
        {!resource.loading && !resource.error && !resource.items.length && (
          <Empty title="No Microsoft messages" icon="inbox">
            Your inbox is empty.
          </Empty>
        )}
        <GraphPagination resource={resource} />
      </Panel>
    </>
  )
}
