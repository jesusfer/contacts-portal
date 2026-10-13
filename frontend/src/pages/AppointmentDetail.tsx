import { useEffect, useState } from 'preact/hooks'
import { api, appointmentPayload, dateTime, localInput, time, useResource } from '../utils/data.ts'
import { AppointmentFields } from '../components/AppointmentFields.tsx'
import { Badge } from '../components/Badge.tsx'
import { Contact } from '../components/Contact.tsx'
import { ErrorNotice } from '../components/ErrorNotice.tsx'
import { Loading } from '../components/Loading.tsx'
import { PageHeading } from '../components/PageHeading.tsx'
import { Panel } from '../components/Panel.tsx'

export function AppointmentDetail({ id, version, refresh }) {
  const r = useResource(`appointments/${id}/`, version)
  const [starts,setStarts] = useState(''), [ends,setEnds] = useState(''), [notes,setNotes] = useState(''), [error,setError] = useState(''), [busy,setBusy] = useState(false), [saved,setSaved] = useState(false)
  useEffect(() => { if (r.data) { setStarts(localInput(r.data.starts_at)); setEnds(localInput(r.data.ends_at)); setNotes(r.data.notes || '') } }, [r.data])
  async function save(e) {
    e.preventDefault(); setBusy(true); setError(''); setSaved(false)
    try { await api(`appointments/${id}/`, { method:'PATCH',body:appointmentPayload(starts,ends,notes,r.data) }); setSaved(true); refresh() } catch(err) { setError(err.message) } finally { setBusy(false) }
  }
  if (r.loading) return <Loading/>
  if (r.error) return <ErrorNotice error={r.error} retry={r.retry}/>
  const a = r.data
  return <><a class="back" href="#/calendar">← Back to calendar</a><PageHeading eyebrow={`Appointment #${id}`} title={`Time with ${a.call.contact.name}`} subtitle="Make room for your next meaningful conversation."/><div class="detail-grid"><Panel title="Appointment details"><div class="panel-body"><ErrorNotice error={error}/>{saved && <div class="success" role="status">Appointment updated.</div>}<form class="form" onSubmit={save}><AppointmentFields {...{starts,setStarts,ends,setEnds,notes,setNotes}}/><p class="form-help">Moving a pending appointment automatically marks its call as rescheduled. Correcting a completed appointment preserves its call outcome and rating.</p><button class="button" disabled={busy}>{busy ? 'Saving…' : 'Save appointment'}</button></form></div></Panel><Panel title="Linked conversation"><div class="panel-body"><Contact contact={a.call.contact}/><div class="actions" style="margin-top:20px"><Badge status={a.call.status}/><a class="link" href={`#/calls/${a.call.id}`}>View call →</a></div></div></Panel></div></>
}
