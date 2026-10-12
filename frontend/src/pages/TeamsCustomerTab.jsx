import { useEffect, useState } from 'preact/hooks'
import * as TeamsSDK from '../teams.js'
import { dateTime, initials } from '../utils/data.js'
import { Badge } from '../components/Badge.jsx'
import { Empty } from '../components/Empty.jsx'
import { ErrorNotice } from '../components/ErrorNotice.jsx'
import { Loading } from '../components/Loading.jsx'
import { PageHeading } from '../components/PageHeading.jsx'
import { Panel } from '../components/Panel.jsx'
import { initialProvider } from '../teams/selection.jsx'

export function TeamsCustomerTab() {
  const [data, setData] = useState(null), [error, setError] = useState(''), [loading, setLoading] = useState(true), [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    setData(null); setError(''); setLoading(true)
    async function load() {
      try {
        const query = new URLSearchParams(location.search)
        const customerId = query.get('customer')
        if (!/^[1-9]\d*$/.test(customerId || '')) throw new Error('Choose a customer in the tab configuration.')
        const config = await TeamsSDK.getTeamsConfig()
        const providerId = initialProvider(config.providers || [])
        if (!providerId) throw new Error('Choose an organization in the tab configuration.')
        const token = await TeamsSDK.getTeamsAuthToken()
        if (!active) return
        const session = await TeamsSDK.exchangeTeamsToken(providerId, token)
        if (!active) return
        if (!session.user || !session.agent) throw new Error('Your CRM account must be linked and provisioned with an agent.')
        const result = await TeamsSDK.getTeamsCustomer(providerId, customerId)
        if (active) setData(result)
      } catch (error) {
        if (active) setError(error.message)
      } finally {
        if (active) setLoading(false)
      }
    }
    load()
    return () => { active = false }
  }, [attempt])
  return <main class="content"><PageHeading title={data?.customer.name || 'Gather customer tab'} subtitle="Customer contacts and your CRM calls."/><button class="button secondary" disabled={loading} onClick={() => setAttempt(n => n + 1)}>Refresh</button>{loading ? <Loading/> : <ErrorNotice error={error} retry={() => setAttempt(n => n + 1)}/>}
    {data && <div class="dashboard-grid"><Panel title="Contacts" count={data.contacts.length}>{data.contacts.map(contact => <div class="list-row" key={contact.id}><span class="avatar">{initials(contact.name)}</span><div class="row-main"><strong>{contact.name}</strong><p>{contact.email}</p></div></div>)}{!data.contacts.length && <Empty title="No contacts" icon="contacts">This customer has no contacts.</Empty>}</Panel><Panel title="Your calls" count={data.calls.length}>{data.calls.map(call => <div class="list-row" key={call.id}><div class="row-main"><strong>{call.contact.name}</strong><p>{call.summary || 'No summary'}</p><small>{dateTime(call.appointment?.starts_at)}</small></div><Badge status={call.status}/></div>)}{!data.calls.length && <Empty title="No calls" icon="calls">You have no calls for this customer.</Empty>}</Panel></div>}
    {error && <a class="button secondary" href="/#/login" target="_blank" rel="noopener noreferrer">Open browser sign-in</a>}
  </main>
}
