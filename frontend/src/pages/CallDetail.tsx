// Displays and edits the details of a selected call.
import { useEffect, useState } from 'preact/hooks'
import { api, dateTime, time, useResource } from '../utils/data.ts'
import { CallFields } from '../components/CallFields.tsx'
import { Contact } from '../components/Contact.tsx'
import { ErrorNotice } from '../components/ErrorNotice.tsx'
import { Loading } from '../components/Loading.tsx'
import { PageHeading } from '../components/PageHeading.tsx'
import { Panel } from '../components/Panel.tsx'

export function CallDetail({ id, version, refresh, openCreate }) {
  const r = useResource(`calls/${id}/`, version)
  const [status, setStatus] = useState('scheduled'),
    [summary, setSummary] = useState(''),
    [rating, setRating] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [saved, setSaved] = useState(false)
  useEffect(() => {
    if (r.data) {
      setStatus(r.data.status)
      setSummary(r.data.summary || '')
      setRating(r.data.rating ?? '')
    }
  }, [r.data])
  async function save(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    setSaved(false)
    try {
      await api(`calls/${id}/`, {
        method: 'PATCH',
        body: {
          status,
          summary,
          rating: status === 'completed' && rating !== '' ? Number(rating) : null,
        },
      })
      setSaved(true)
      refresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  if (r.loading) return <Loading />
  if (r.error) return <ErrorNotice error={r.error} retry={r.retry} />
  const call = r.data
  return (
    <>
      <a class="back" href="#/calls">
        ← Back to call log
      </a>
      <PageHeading
        title={`Conversation with ${call.contact.name}`}
        eyebrow={`Call #${call.id}`}
        subtitle={call.contact.company?.name || call.contact.email}
      />
      <div class="detail-grid">
        <Panel title="Conversation details">
          <div class="panel-body">
            <ErrorNotice error={error} />
            {saved && (
              <div class="success" role="status">
                Conversation updated.
              </div>
            )}
            <form class="form" onSubmit={save}>
              <CallFields {...{ status, setStatus, summary, setSummary, rating, setRating }} />
              <div class="actions">
                <button class="button" disabled={busy}>
                  {busy ? 'Saving…' : 'Save changes'}
                </button>
                <a class="button secondary" href="#/calls">
                  Back to calls
                </a>
              </div>
            </form>
          </div>
        </Panel>
        <div>
          <Panel title="Contact">
            <div class="panel-body">
              <Contact contact={call.contact} />
              <dl>
                <div class="info-row">
                  <dt>Email</dt>
                  <dd>
                    <a class="link" href={`mailto:${call.contact.email}`}>
                      {call.contact.email}
                    </a>
                  </dd>
                </div>
                <div class="info-row">
                  <dt>Company</dt>
                  <dd>{call.contact.company?.name || '—'}</dd>
                </div>
              </dl>
            </div>
          </Panel>
          <Panel title="Appointment">
            <div class="panel-body">
              {call.appointment ? (
                <>
                  <p>{dateTime(call.appointment.starts_at)}</p>
                  <p class="muted" style="margin:6px 0 17px">
                    Until {time(call.appointment.ends_at)}
                  </p>
                  <a class="button secondary" href={`#/appointments/${call.appointment.id}`}>
                    View or reschedule
                  </a>
                </>
              ) : (
                <>
                  <p class="muted" style="margin-bottom:17px">
                    No appointment scheduled for this conversation.
                  </p>
                  <button
                    class="button secondary"
                    onClick={() => openCreate('appointment', call.id)}
                  >
                    Schedule appointment
                  </button>
                </>
              )}
            </div>
          </Panel>
        </div>
      </div>
    </>
  )
}
