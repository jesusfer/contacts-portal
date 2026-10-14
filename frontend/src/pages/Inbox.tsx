// Lists and manages the CRM inbox with filtering and message actions.
import { useEffect, useState } from 'preact/hooks'
import { api, date, dateTime, useResource } from '../utils/data.ts'
import { Badge } from '../components/Badge.tsx'
import { Empty } from '../components/Empty.tsx'
import { ErrorNotice } from '../components/ErrorNotice.tsx'
import { Loading } from '../components/Loading.tsx'
import { PageHeading } from '../components/PageHeading.tsx'

export function Inbox({ version, refresh, id }) {
  const r = useResource('inbox/', version),
    [filter, setFilter] = useState('all'),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState('')
  const all = (r.data?.emails || [])
    .slice()
    .sort((a, b) => new Date(b.received_at).getTime() - new Date(a.received_at).getTime())
  const list = all.filter(
    (e) =>
      filter === 'all' ||
      (filter === 'unread' && !e.is_read) ||
      (filter === 'unreplied' && !e.is_replied),
  )
  const selected = id ? all.find((e) => String(e.id) === String(id)) : list[0]
  useEffect(() => {
    setError('')
    setNotice('')
  }, [id])
  async function update(body) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await api(`inbox/${selected.id}/`, { method: 'PATCH', body })
      setNotice(
        body.is_replied ? 'Marked as replied. No email was sent.' : 'Message tracking updated.',
      )
      refresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <PageHeading
        eyebrow="Inbox"
        title="Keep the conversation going."
        subtitle="Incoming messages and a clear record of your follow-through."
      />
      <div class="toolbar">
        <select
          style="margin-left:0"
          aria-label="Filter inbox"
          value={filter}
          onChange={(e) => {
            setFilter(e.currentTarget.value)
            location.hash = '/inbox'
          }}
        >
          <option value="all">All messages</option>
          <option value="unread">Unread</option>
          <option value="unreplied">Not replied</option>
        </select>
        <span class="muted">Tracking only · no emails are sent from Gather</span>
      </div>
      <ErrorNotice error={r.error} retry={r.retry} />
      {r.loading ? (
        <Loading />
      ) : (
        !r.error && (
          <section class="panel">
            <div class="inbox-layout">
              <div class="inbox-list">
                {list.map((email) => (
                  <a
                    class={`email-item ${selected?.id === email.id ? 'selected' : ''} ${!email.is_read ? 'unread' : ''}`}
                    href={`#/inbox/${email.id}`}
                    key={email.id}
                    style="text-decoration:none"
                  >
                    <div class="email-top">
                      <strong>
                        {email.contact.name}
                        {!email.is_read && <span class="notification-dot" />}
                      </strong>
                      <small>{date(email.received_at)}</small>
                    </div>
                    <p>{(email.subject || '(No subject)').slice(0, 50)}</p>
                    <div style="margin-top:8px">
                      <Badge
                        status={email.is_replied ? 'replied' : email.is_read ? 'read' : 'unread'}
                      />
                    </div>
                  </a>
                ))}
                {!list.length && (
                  <Empty title="No messages here" icon="inbox">
                    Try another filter or check back later.
                  </Empty>
                )}
              </div>
              <div class="email-body">
                {selected ? (
                  <>
                    <ErrorNotice error={error} />
                    {notice && (
                      <div class="success" role="status">
                        {notice}
                      </div>
                    )}
                    <h2>{selected.subject || '(No subject)'}</h2>
                    <p class="email-metadata">
                      From {selected.contact.name} &lt;{selected.contact.email}&gt;
                    </p>
                    <p class="email-metadata">{dateTime(selected.received_at)}</p>
                    <div class="actions" style="margin-top:16px">
                      <Badge status={selected.is_read ? 'read' : 'unread'} />
                      <Badge status={selected.is_replied ? 'replied' : 'not replied'} />
                    </div>
                    <div class="email-content">
                      {selected.body.slice(0, 50) || '(Empty message)'}
                    </div>
                    <div class="actions">
                      <button
                        class="button secondary"
                        disabled={busy}
                        onClick={() => update({ is_read: !selected.is_read })}
                      >
                        {selected.is_read ? 'Mark unread' : 'Mark read'}
                      </button>
                      <button
                        class="button"
                        disabled={busy}
                        onClick={() => update({ is_replied: !selected.is_replied })}
                      >
                        {selected.is_replied ? 'Mark not replied' : 'Mark replied'}
                      </button>
                    </div>
                    <p class="form-help" style="margin-top:15px">
                      These buttons update tracking only. Reply using your email application.
                    </p>
                  </>
                ) : (
                  <Empty title={id ? 'Message not found' : 'Choose a conversation'} icon="inbox">
                    {id
                      ? 'This message is no longer available.'
                      : 'Select a message to read it and update its status.'}
                  </Empty>
                )}
              </div>
            </div>
          </section>
        )
      )}
    </>
  )
}
