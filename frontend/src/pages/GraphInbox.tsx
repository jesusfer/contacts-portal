// Displays the five latest Microsoft Graph inbox messages.
import { dateTime, initials, useResource } from '../utils/data.ts'
import { Badge } from '../components/Badge.tsx'
import { Empty } from '../components/Empty.tsx'
import { GraphLink } from '../components/GraphLink.tsx'
import { Loading } from '../components/Loading.tsx'
import { ErrorNotice } from '../components/ErrorNotice.tsx'
import { PageHeading } from '../components/PageHeading.tsx'
import { Panel } from '../components/Panel.tsx'

export function GraphInbox({ version, compact = false }) {
  const resource = useResource('graph/inbox/', version)
  const emails = resource.data?.emails || []
  return (
    <>
      {!compact && (
        <PageHeading
          title="Inbox"
          subtitle="Your 5 latest messages. Read-only; open Outlook to reply."
        />
      )}
      <Panel
        title="Microsoft inbox · Latest 5"
        count={emails.length}
        action={
          compact && (
            <a class="button quiet" href="#/inbox">
              View inbox
            </a>
          )
        }
      >
        {emails.map((email) => (
          <div class="list-row" key={email.id}>
            <span class="avatar">{initials(email.contact?.name)}</span>
            <div class="row-main">
              <strong>
                {email.contact?.name || email.contact?.email || 'Unknown sender'}
                {!email.is_read && <span class="notification-dot" />}
              </strong>
              <p>{email.subject || '(No subject)'}</p>
              <small>{dateTime(email.received_at)}</small>
              {!compact && (
                <p class="graph-message-body">{email.body.slice(0, 50) || '(Empty message)'}</p>
              )}
              <GraphLink url={email.web_url}>Open message</GraphLink>
            </div>
            <Badge status={email.is_read ? 'read' : 'unread'} />
          </div>
        ))}
        {!resource.loading && !resource.error && !emails.length && (
          <Empty title="No Microsoft messages" icon="inbox">
            Your inbox is empty.
          </Empty>
        )}
        {resource.loading && <Loading />}
        <ErrorNotice error={resource.error} retry={resource.retry} />
      </Panel>
    </>
  )
}
