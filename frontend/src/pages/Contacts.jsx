import { useState } from 'preact/hooks'
import { useResource } from '../lib/data.js'
import { Contact } from '../components/Contact.jsx'
import { Empty } from '../components/Empty.jsx'
import { ErrorNotice } from '../components/ErrorNotice.jsx'
import { Icon } from '../components/Icon.jsx'
import { Loading } from '../components/Loading.jsx'
import { PageHeading } from '../components/PageHeading.jsx'
import { Panel } from '../components/Panel.jsx'

export function Contacts({ version, openCreate }) {
  const r = useResource('contacts/', version), [query,setQuery] = useState('')
  const list = (r.data?.contacts || []).filter(c => `${c.name} ${c.email} ${c.company?.name}`.toLowerCase().includes(query.toLowerCase()))
  return <><PageHeading eyebrow="Contacts" title="People, not just records." subtitle="A shared directory of people and the companies they belong to."><div class="actions"><button class="button secondary" onClick={() => openCreate('company')}><Icon name="plus" size={16}/>Add company</button><button class="button" onClick={() => openCreate('contact')}><Icon name="plus" size={16}/>Add contact</button></div></PageHeading><div class="toolbar"><input aria-label="Search contacts" placeholder="Find a name, email, or company…" value={query} onInput={e => setQuery(e.target.value)}/></div><ErrorNotice error={r.error} retry={r.retry}/>{r.loading ? <Loading/> : !r.error && <Panel title="Your contacts" count={list.length}><div class="table-wrap"><table><thead><tr><th>Contact</th><th>Email</th><th>Company</th><th>Next step</th></tr></thead><tbody>{list.map(c => <tr key={c.id}><td><Contact contact={c}/></td><td><a class="link" href={`mailto:${c.email}`}>{c.email}</a></td><td>{c.company?.name || '—'}</td><td><button class="button secondary" onClick={() => openCreate('call',c.id)}>Log a call</button></td></tr>)}</tbody></table></div>{!list.length && <Empty title={query ? 'No contacts found' : 'Your next connection starts here'}>{query ? 'Try a different name or email.' : 'Add your first contact to begin logging conversations.'}</Empty>}</Panel>}</>
}
