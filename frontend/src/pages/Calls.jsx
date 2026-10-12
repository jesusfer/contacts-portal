import { useState } from 'preact/hooks'
import { dateTime, statuses, useResource } from '../lib/data.js'
import { Contact } from '../components/Contact.jsx'
import { Badge } from '../components/Badge.jsx'
import { Empty } from '../components/Empty.jsx'
import { ErrorNotice } from '../components/ErrorNotice.jsx'
import { Icon } from '../components/Icon.jsx'
import { Loading } from '../components/Loading.jsx'
import { PageHeading } from '../components/PageHeading.jsx'
import { Panel } from '../components/Panel.jsx'

export function Calls({ version, openCreate }) {
  const r = useResource('calls/', version)
  const [status, setStatus] = useState('all'), [query, setQuery] = useState('')
  const calls = (r.data?.calls || []).filter(c => (status === 'all' || c.status === status) && `${c.contact.name} ${c.contact.email} ${c.summary || ''}`.toLowerCase().includes(query.toLowerCase()))
  return <><PageHeading title="Every conversation matters." eyebrow="Call log" subtitle="Track outcomes, capture details, and keep moving forward."><button class="button" onClick={() => openCreate('call')}><Icon name="plus" size={16}/>Log a call</button></PageHeading><div class="toolbar"><input aria-label="Search calls" placeholder="Search contacts or summaries…" value={query} onInput={e => setQuery(e.target.value)}/><select aria-label="Filter calls by status" value={status} onChange={e => setStatus(e.target.value)}><option value="all">All statuses</option>{statuses.map(s => <option value={s} key={s}>{s[0].toUpperCase()+s.slice(1)}</option>)}</select></div><ErrorNotice error={r.error} retry={r.retry}/>{r.loading ? <Loading/> : !r.error && <Panel title="Conversations" count={calls.length}><div class="table-wrap"><table><thead><tr><th>Contact</th><th>Appointment</th><th>Status</th><th>Rating</th><th>Summary</th><th><span class="visually-hidden">Details</span></th></tr></thead><tbody>{calls.map(c => <tr key={c.id}><td><Contact contact={c.contact}/></td><td>{dateTime(c.appointment?.starts_at)}</td><td><Badge status={c.status}/></td><td>{c.rating == null ? '—' : `${c.rating} / 5`}</td><td><div class="truncate">{c.summary || 'No summary yet'}</div></td><td><a class="link" href={`#/calls/${c.id}`}>Details →</a></td></tr>)}</tbody></table></div>{!calls.length && <Empty title={query || status !== 'all' ? 'No matching conversations' : 'Start a conversation'} icon="calls">{query || status !== 'all' ? 'Try another search or status.' : 'Log your first call to keep its details in one place.'}</Empty>}</Panel>}</>
}
