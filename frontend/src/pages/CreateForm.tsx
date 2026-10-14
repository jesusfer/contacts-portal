// Provides forms for creating CRM calls and appointments.
import { useState } from 'preact/hooks'
import { api, appointmentPayload, useResource } from '../utils/data.ts'
import { AppointmentFields } from '../components/AppointmentFields.tsx'
import { CallFields } from '../components/CallFields.tsx'
import { Empty } from '../components/Empty.tsx'
import { ErrorNotice } from '../components/ErrorNotice.tsx'
import { Loading } from '../components/Loading.tsx'

interface CreateFormProps {
  type: string
  initialId?: string | number
  done: (type: string, result: Record<string, any>) => void
  close: () => void
}

export function CreateForm({ type, initialId, done, close }: CreateFormProps) {
  const contacts = useResource(type === 'call' ? 'contacts/' : null),
    calls = useResource(type === 'appointment' ? 'calls/' : null),
    companies = useResource(type === 'contact' ? 'companies/' : null)
  const [selected, setSelected] = useState(initialId || ''),
    [status, setStatus] = useState('scheduled'),
    [summary, setSummary] = useState(''),
    [rating, setRating] = useState(''),
    [starts, setStarts] = useState(''),
    [ends, setEnds] = useState(''),
    [notes, setNotes] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const pending = type === 'call' ? contacts : type === 'contact' ? companies : calls
  const unscheduled = (calls.data?.calls || []).filter((c) => !c.appointment)
  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    const form = new FormData(e.currentTarget)
    try {
      let result
      if (type === 'company')
        result = await api('companies/', {
          method: 'POST',
          body: { name: String(form.get('name') || '').trim() },
        })
      else if (type === 'contact')
        result = await api('contacts/', {
          method: 'POST',
          body: {
            name: String(form.get('name') || '').trim(),
            email: String(form.get('email') || '').trim(),
            company_id: Number(form.get('company_id')),
          },
        })
      else if (type === 'call')
        result = await api('calls/', {
          method: 'POST',
          body: {
            contact_id: Number(selected),
            status,
            summary,
            rating: status === 'completed' && rating !== '' ? Number(rating) : null,
          },
        })
      else
        result = await api('appointments/', {
          method: 'POST',
          body: { call_id: Number(selected), ...appointmentPayload(starts, ends, notes) },
        })
      done(type, result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  if (type !== 'company' && pending.loading) return <Loading />
  if (type !== 'company' && pending.error)
    return <ErrorNotice error={pending.error} retry={pending.retry} />
  const available = type === 'call' ? contacts.data?.contacts || [] : unscheduled
  if (['call', 'appointment'].includes(type) && !available.length)
    return (
      <Empty
        title={type === 'call' ? 'Add a contact first' : 'No unscheduled calls'}
        icon={type === 'call' ? 'contacts' : 'calendar'}
      >
        <span>
          {type === 'call'
            ? 'Create a contact, then log your conversation.'
            : 'Each call can have one appointment. Log a new call, or edit an existing appointment.'}
        </span>
        <div class="actions" style="justify-content:center;margin-top:15px">
          <a class="button" onClick={close} href={type === 'call' ? '#/contacts' : '#/calls'}>
            {type === 'call' ? 'Go to contacts' : 'Go to calls'}
          </a>
        </div>
      </Empty>
    )
  if (type === 'contact' && !companies.data.companies.length)
    return (
      <Empty title="Add a company first">
        Contacts belong to their own organizations. Close this form and use “Add company” on the
        contacts page.
      </Empty>
    )
  return (
    <form class="form" onSubmit={submit}>
      <ErrorNotice error={error} />
      {type === 'company' ? (
        <>
          <label>
            Company name
            <input name="name" autoComplete="organization" required maxLength={200} />
          </label>
          <p class="form-help">
            Add an organization and link it to your agent account. You can then assign contacts to
            it.
          </p>
        </>
      ) : type === 'contact' ? (
        <>
          <label>
            Full name
            <input name="name" autoComplete="name" required maxLength={200} />
          </label>
          <label>
            Email address
            <input name="email" type="email" autoComplete="email" required />
          </label>
          <label>
            Contact's company
            <select name="company_id" required defaultValue="">
              <option value="">Choose a company…</option>
              {companies.data.companies.map((company) => (
                <option key={company.id} value={company.id}>
                  {company.name}
                </option>
              ))}
            </select>
          </label>
          <p class="form-help">
            Choose one of your linked companies. Contacts are visible to agents linked to that
            company; your conversations remain private to you.
          </p>
        </>
      ) : (
        <>
          <label>
            {type === 'call' ? 'Contact' : 'Unscheduled call'}
            <select value={selected} required onChange={(e) => setSelected(e.currentTarget.value)}>
              <option value="">Choose {type === 'call' ? 'a contact' : 'a call'}…</option>
              {available.map((item) => (
                <option key={item.id} value={item.id}>
                  {type === 'call'
                    ? `${item.name} · ${item.company?.name || item.email}`
                    : `${item.contact.name} · Call #${item.id} · ${item.status}`}
                </option>
              ))}
            </select>
          </label>
          {type === 'call' ? (
            <CallFields {...{ status, setStatus, summary, setSummary, rating, setRating }} />
          ) : (
            <AppointmentFields {...{ starts, setStarts, ends, setEnds, notes, setNotes }} />
          )}
        </>
      )}
      <div class="actions">
        <button class="button" disabled={busy}>
          {busy
            ? 'Creating…'
            : type === 'company'
              ? 'Add company'
              : type === 'contact'
                ? 'Add contact'
                : type === 'call'
                  ? 'Create call'
                  : 'Create appointment'}
        </button>
        <button type="button" class="button secondary" disabled={busy} onClick={close}>
          Cancel
        </button>
      </div>
    </form>
  )
}
