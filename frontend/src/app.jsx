import { useEffect, useRef, useState } from 'preact/hooks'
import * as TeamsSDK from './teams.js'
import * as TeamsGraph from './teams-graph.js'

const statuses = ['scheduled', 'completed', 'missed', 'rescheduled', 'cancelled']
const labels = { dashboard: 'Overview', calls: 'Call log', calendar: 'Calendar', contacts: 'Contacts', inbox: 'Inbox', appointments: 'Appointment', profile: 'My profile', access: 'Workspace access' }
const icons = {
  dashboard: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
  calls: <path d="M6 3H3v4c0 8 6 14 14 14h4v-4l-5-2-2 3c-4-1-7-4-8-8l3-2-3-5Z"/>,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18m-14 4h3m4 0h3"/></>,
  contacts: <><circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-16a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5"/></>,
  inbox: <><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/></>,
  profile: <><circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/></>,
  access: <><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></>,
  arrow: <path d="m9 5 7 7-7 7"/>, plus: <path d="M12 5v14M5 12h14"/>,
  star: <path d="m12 3 2.8 5.7 6.3.9-4.6 4.4 1.1 6.2-5.6-2.9-5.6 2.9 1.1-6.2L3 9.6l6.2-.9L12 3Z"/>,
  check: <path d="m5 12 4 4L19 6"/>, clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  menu: <path d="M4 6h16M4 12h16M4 18h16"/>, close: <path d="m6 6 12 12M6 18 18 6"/>,
}
function Icon({ name, size = 18 }) { return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{icons[name] || icons.calls}</svg> }
function initials(name = '') { return name.split(/\s+/).slice(0, 2).map(s => s[0]).join('').toUpperCase() || '?' }
function date(value, options = {}) { return value ? new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...options }) : 'Not scheduled' }
function time(value) { return value ? new Date(value).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : '—' }
function dateTime(value) { return value ? `${date(value, { year: 'numeric' })} · ${time(value)}` : 'Not scheduled' }
function localInput(value) {
  if (!value) return ''
  const d = new Date(value)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
function csrf() { return decodeURIComponent(document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.slice(10) || '') }
async function api(path, options = {}) {
  if (TeamsGraph.teamsGraphActive() && (!options.method || options.method === 'GET') &&
      (path.startsWith('graph/') || path === 'profile/')) return TeamsGraph.teamsGraphRequest(path, options)
  const multipart = options.body instanceof FormData
  const response = await fetch(`/api/${path}`, {
    credentials: 'same-origin', ...options,
    headers: { ...(!multipart ? { 'Content-Type': 'application/json' } : {}), ...(options.method ? { 'X-CSRFToken': csrf() } : {}), ...options.headers },
    body: multipart ? options.body : options.body ? JSON.stringify(options.body) : undefined,
  })
  let data
  try { data = await response.json() } catch { throw new Error(`The server returned an unexpected response (${response.status}). Please try again.`) }
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('session-expired'))
    const fieldLabels = { starts_at: 'Start time', ends_at: 'End time', rating: 'Rating', name: 'Name', email: 'Email', company_id: 'Company', contact_id: 'Contact', call_id: 'Call', status: 'Status', summary: 'Summary', notes: 'Notes', username: 'Username', password: 'Password', non_field_errors: 'Details', __all__: 'Details' }
    const details = data.fields && typeof data.fields === 'object' ? Object.entries(data.fields).map(([field, messages]) => {
      const text = (Array.isArray(messages) ? messages : [messages]).filter(message => typeof message === 'string').join(' ')
      return text ? `${fieldLabels[field] || field.replaceAll('_', ' ')}: ${text}` : ''
    }).filter(Boolean).join(' ') : ''
    throw new Error([data.error || `Request failed (${response.status}).`, details].filter(Boolean).join(' '))
  }
  return data
}
function useResource(path, version = 0) {
  const [state, setState] = useState({ loading: true, data: null, error: '' })
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    if (!path) { setState({ loading: false, data: null, error: '' }); return }
    setState({ loading: true, data: null, error: '' })
    api(path).then(data => active && setState({ loading: false, data, error: '' })).catch(error => active && setState({ loading: false, data: null, error: error.message }))
    return () => { active = false }
  }, [path, version, retry])
  return { ...state, retry: () => setRetry(n => n + 1) }
}
function Loading() { return <div class="loading" role="status"><span class="spinner"/>Getting things ready…</div> }
function ErrorNotice({ error, retry }) { return error && <div class="error" role="alert"><span>{error}</span>{retry && <button class="button secondary" onClick={retry}>Try again</button>}</div> }
function Empty({ title = 'Nothing here yet', children, icon = 'contacts' }) { return <div class="empty"><Icon name={icon} size={27}/><strong>{title}</strong><p>{children}</p></div> }
function Badge({ status }) { return <span class={`badge ${status}`}>{status}</span> }
function Contact({ contact }) { return <div class="contact-cell"><span class="avatar">{initials(contact?.name)}</span><span><strong>{contact?.name || 'Unknown contact'}</strong><small>{contact?.company?.name || contact?.email}</small></span></div> }
function UserAvatar({ user, revision, large = false }) {
  const [failedSource, setFailedSource] = useState('')
  const [graphPhoto, setGraphPhoto] = useState('')
  useEffect(() => {
    if (!TeamsGraph.teamsGraphActive() || user.picture_url !== '/api/graph/picture/') return
    let active = true, objectUrl
    const controller = new AbortController()
    TeamsGraph.teamsGraphPhoto(controller.signal).then(blob => {
      if (active && blob) { objectUrl = URL.createObjectURL(blob); setGraphPhoto(objectUrl) }
    }).catch(() => {})
    return () => { active = false; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [user.id, user.picture_url, revision])
  const isLocalProfile = user.picture_url === '/api/profile/picture/'
  const isGraphProfile = user.picture_url === '/api/graph/picture/'
  const source = isLocalProfile ? `${user.picture_url}?v=${revision}` : (isGraphProfile ? (TeamsGraph.teamsGraphActive() ? graphPhoto : user.picture_url) : '')
  return <span class={`avatar user-avatar${large ? ' profile-avatar' : ''}`}>{source && source !== failedSource ? <img src={source} alt={`${user.name || user.username}'s profile picture`} onError={() => setFailedSource(source)}/> : <span aria-label="Profile initials">{initials(user.name || user.username)}</span>}</span>
}

function GraphPresenceIndicator({ user }) {
  const [presence, setPresence] = useState({ availability: 'Unknown', activity: '' })
  useEffect(() => {
    setPresence({ availability: 'Unknown', activity: '' })
    if (!user?.graph_enabled) return
    let active = true, pollId, pending = false
    const controller = new AbortController()
    async function poll() {
      if (!active || pending || document.hidden) return
      clearTimeout(pollId)
      pending = true
      try {
        const data = await api('graph/presence/', { signal: controller.signal })
        if (active) setPresence({ availability: data.availability || 'Unknown', activity: data.activity || '' })
      } catch { if (active) setPresence({ availability: 'Unknown', activity: '' }) }
      finally {
        pending = false
        if (active) pollId = setTimeout(poll, 30000)
      }
    }
    const onVisibility = () => !document.hidden && poll()
    document.addEventListener('visibilitychange', onVisibility)
    poll()
    return () => { active = false; controller.abort(); clearTimeout(pollId); document.removeEventListener('visibilitychange', onVisibility) }
  }, [user?.id, user?.graph_enabled])
  const status = presence.availability.toLowerCase().replace(/[^a-z]/g, '')
  const label = `Presence: ${presence.availability}${presence.activity ? ` · ${presence.activity}` : ''}`
  return user?.graph_enabled ? <span class={`presence-indicator status-${status}`} role="img" title={label} aria-label={label}>●</span> : null
}

function safeExternalUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null
  } catch { return null }
}

function GraphLink({ url, children }) {
  const href = safeExternalUrl(url)
  return href ? <a class="link" href={href} target="_blank" rel="noopener noreferrer">{children} ↗</a> : null
}

function useGraphPages(path, field, version) {
  const [state, setState] = useState({ path, items: [], next: null, loading: true, error: '' })
  const request = useRef(null)
  const generation = useRef(0)
  const seen = useRef(new Set())
  async function load(page, append = false) {
    if (request.current) return
    const current = generation.current
    const controller = new AbortController()
    request.current = controller
    setState(s => ({ ...s, loading: true, error: '' }))
    try {
      const separator = path.includes('?') ? '&' : '?'
      const data = await api(page ? `${path}${separator}page=${encodeURIComponent(page)}` : path, { signal: controller.signal })
      if (current !== generation.current) return
      if (page) seen.current.add(page)
      const next = typeof data.next_page === 'string' && data.next_page && !seen.current.has(data.next_page) ? data.next_page : null
      setState(s => {
        const items = append ? [...s.items, ...(data[field] || [])] : data[field] || []
        return { path, items: [...new Map(items.map(item => [item.id, item])).values()], next, loading: false, error: '' }
      })
    } catch (error) {
      if (current === generation.current) setState(s => ({ ...s, loading: false, error: error.message }))
    } finally {
      if (request.current === controller) request.current = null
    }
  }
  useEffect(() => {
    generation.current++
    request.current?.abort()
    request.current = null
    seen.current = new Set()
    setState({ path, items: [], next: null, loading: true, error: '' })
    load()
    return () => { generation.current++; request.current?.abort(); request.current = null }
  }, [path, version])
  const visible = state.path === path ? state : { items: [], next: null, loading: true, error: '' }
  return { ...visible, more: () => load(visible.next, true), retry: () => load(visible.next, visible.items.length > 0) }
}

function GraphPagination({ resource }) {
  return <>{resource.loading && <Loading/>}<ErrorNotice error={resource.error} retry={resource.retry}/>{resource.next && !resource.error && <div class="panel-body"><button class="button secondary" disabled={resource.loading} onClick={resource.more}>Load more</button></div>}</>
}

function GraphCalendar({ version, compact = false }) {
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1))
  const start = month.toISOString(), end = new Date(month.getFullYear(), month.getMonth() + 1, 1).toISOString()
  const resource = useGraphPages(`graph/calendar/?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`, 'events', version)
  const navigate = offset => setMonth(value => new Date(value.getFullYear(), value.getMonth() + offset, 1))
  return <>{!compact && <PageHeading title="Calendar" subtitle="Your Microsoft calendar. Events are read-only."/>}<Panel title={compact ? 'Coming up next · Microsoft calendar' : 'Microsoft calendar'} count={resource.items.length} action={compact && <a class="button quiet" href="#/calendar">View calendar</a>}>
    <div class="graph-month"><button class="button secondary" aria-label="Previous month" onClick={() => navigate(-1)}>←</button><strong aria-live="polite">{month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}</strong><button class="button secondary" aria-label="Next month" onClick={() => navigate(1)}>→</button></div>
    {resource.items.slice().sort((a, b) => new Date(a.starts_at) - new Date(b.starts_at)).filter(event => !compact || new Date(event.ends_at) >= new Date()).map(event => <div class="list-row" key={event.id}><div class="row-main"><strong>{event.subject || '(No title)'}</strong><small>{dateTime(event.starts_at)} – {dateTime(event.ends_at)}{event.location ? ` · ${event.location}` : ''}</small><GraphLink url={event.web_url}>Open event</GraphLink></div></div>)}
    {!resource.loading && !resource.error && !resource.items.length && <Empty title="No Microsoft events" icon="calendar">There are no events for this month.</Empty>}
    <GraphPagination resource={resource}/>
  </Panel></>
}

function GraphInbox({ version, compact = false }) {
  const resource = useGraphPages('graph/inbox/', 'emails', version)
  return <>{!compact && <PageHeading title="Inbox" subtitle="Your Microsoft messages. Read-only; open Outlook to reply."/>}<Panel title="Microsoft inbox" count={resource.items.length} action={compact && <a class="button quiet" href="#/inbox">View inbox</a>}>
    {resource.items.map(email => <div class="list-row" key={email.id}><span class="avatar">{initials(email.contact?.name)}</span><div class="row-main"><strong>{email.contact?.name || email.contact?.email || 'Unknown sender'}{!email.is_read && <span class="notification-dot"/>}</strong><p>{email.subject || '(No subject)'}</p><small>{dateTime(email.received_at)}</small>{!compact && <p class="graph-message-body">{email.body}</p>}<GraphLink url={email.web_url}>Open message</GraphLink></div><Badge status={email.is_read ? 'read' : 'unread'}/></div>)}
    {!resource.loading && !resource.error && !resource.items.length && <Empty title="No Microsoft messages" icon="inbox">Your inbox is empty.</Empty>}
    <GraphPagination resource={resource}/>
  </Panel></>
}

function initialProvider(providers) {
  const query = new URLSearchParams(location.search)
  if (query.has('provider')) {
    const match = providers.find(provider => String(provider.id) === query.get('provider'))
    if (!match) throw new Error('The selected organization is not configured for Teams. Ask your administrator to update this tab.')
    return String(match.id)
  }
  return providers.length === 1 ? String(providers[0].id) : ''
}

function ProviderSelect({ providers, selected, onChange }) {
  return <label class="teams-provider">Organization<select value={selected} onChange={event => onChange(event.target.value)}><option value="">Select an organization</option>{providers.map(provider => <option key={provider.id} value={String(provider.id)}>{provider.name}</option>)}</select></label>
}

function TeamsTab() {
  const [providers, setProviders] = useState([]), [selected, setSelected] = useState('')
  const [session, setSession] = useState(null), [error, setError] = useState(''), [loading, setLoading] = useState(true), [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    TeamsGraph.clearTeamsGraph()
    setSession(null); setError(''); setLoading(true)
    async function signIn() {
      try {
        const config = await TeamsSDK.getTeamsConfig()
        if (!active) return
        const choices = config.providers || []
        setProviders(choices)
        const providerId = initialProvider(choices)
        setSelected(providerId)
        if (!choices.length) throw new Error('No Teams organizations are configured. Contact your administrator.')
        await TeamsSDK.initializeTeams()
        if (!active || !providerId) return
        const token = await TeamsSDK.getTeamsAuthToken()
        if (!active) return
        if (!token) throw new Error('Teams could not provide a sign-in token. Please try again.')
        const value = await TeamsSDK.exchangeTeamsToken(providerId, token)
        if (!active) return
        if (!value.user) throw new Error('Teams sign-in did not create a session. Please try again.')
        const profile = await api('graph/profile/')
        if (!active) return
        await TeamsGraph.startTeamsGraph(choices.find(provider => String(provider.id) === providerId), profile.id, controller.signal)
        if (!active) return
        history.replaceState(null, '', `${location.pathname}?provider=${encodeURIComponent(providerId)}#/dashboard`)
        setSession(value)
      } catch (error) {
        if (active) setError(`Unable to sign in with Teams. ${error.message}`)
      } finally {
        if (active) setLoading(false)
      }
    }
    signIn()
    return () => { active = false; controller.abort(); TeamsGraph.clearTeamsGraph() }
  }, [attempt])
  if (session) return <WorkspaceApp initialSession={session} embedded onSessionEnd={() => { TeamsGraph.clearTeamsGraph(); setSession(null); setError('Your Teams session has ended. Sign in again to continue.') }}/>
  return <div class="teams-page"><div class="teams-container"><h2>Gather for Teams</h2>{loading ? <><Loading/><p class="muted">Signing you in with Teams…</p></> : <><ErrorNotice error={error} retry={() => setAttempt(n => n + 1)}/>{!error && <><ProviderSelect providers={providers} selected={selected} onChange={setSelected}/><button class="button" disabled={!selected} onClick={() => { history.replaceState(null, '', `${location.pathname}?provider=${encodeURIComponent(selected)}${location.hash}`); setAttempt(n => n + 1) }}>Continue with Teams</button></>}<div class="actions"><a class="button secondary" href="/#/login" target="_blank" rel="noopener noreferrer">Open browser sign-in</a></div></>}</div></div>
}

function TeamsConfig() {
  const [providers, setProviders] = useState([]), [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [attempt, setAttempt] = useState(0)
  const providerRef = useRef(null), validity = useRef(null)
  providerRef.current = providers.find(provider => String(provider.id) === selected) || null
  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    async function configure() {
      try {
        const config = await TeamsSDK.getTeamsConfig()
        await TeamsSDK.initializeTeams()
        if (!active) return
        const setValidity = await TeamsSDK.registerTeamsConfigHandler(() => providerRef.current)
        if (!active) { setValidity(false); return }
        validity.current = setValidity
        const choices = config.providers || []
        setProviders(choices)
        setSelected(initialProvider(choices))
        if (!choices.length) throw new Error('No Teams organizations are configured. Contact your administrator.')
      } catch (error) {
        if (active) setError(error.message || 'Unable to configure this tab.')
      } finally {
        if (active) setLoading(false)
      }
    }
    configure()
    return () => { active = false; validity.current?.(false); validity.current = null; providerRef.current = null }
  }, [attempt])
  useEffect(() => { validity.current?.(!loading && !error && !!providerRef.current) }, [loading, error, selected, providers])
  return <div class="teams-page"><div class="teams-container"><h2>Gather for Teams</h2><p class="muted">Choose your organization, then click Save to add Gather.</p>{loading ? <Loading/> : <><ErrorNotice error={error} retry={() => setAttempt(n => n + 1)}/>{!error && <ProviderSelect providers={providers} selected={selected} onChange={setSelected}/>}</>}</div></div>
}

function Profile({ user, revision, onUpdate }) {
  const resource = useResource('profile/')
  const isGraphProfile = !!(user.graph_enabled || resource.data?.graph_enabled || resource.data?.read_only)
  const [firstName, setFirstName] = useState(''), [lastName, setLastName] = useState('')
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [success, setSuccess] = useState('')
  const [file, setFile] = useState(null), [pictureUrl, setPictureUrl] = useState(null)
  const fileInput = useRef()
  useEffect(() => {
    if (resource.data) { setFirstName(resource.data.first_name); setLastName(resource.data.last_name); setPictureUrl(resource.data.picture_url) }
  }, [resource.data])
  async function update(action, path, options, message) {
    if (isGraphProfile) return
    setBusy(action); setError(''); setSuccess('')
    try {
      const result = await api(path, options)
      if (action !== 'name') {
        setPictureUrl(action === 'remove' ? null : result.picture_url || '/api/profile/picture/')
        setFile(null)
        if (fileInput.current) fileInput.current.value = ''
      }
      await onUpdate()
      setSuccess(message)
    } catch (err) { setError(err.message) } finally { setBusy('') }
  }
  function chooseFile(e) {
    if (isGraphProfile) return
    const selected = e.target.files?.[0]
    setFile(null); setError(''); setSuccess('')
    if (!selected) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(selected.type)) { setError('Choose a JPEG, PNG, or WEBP image.'); e.target.value = ''; return }
    if (selected.size > 5 * 1024 * 1024) { setError('Your picture must be no larger than 5 MiB.'); e.target.value = ''; return }
    setFile(selected)
  }
  function upload(e) {
    e.preventDefault()
    if (!file || busy || isGraphProfile) return
    const body = new FormData()
    body.append('picture', file)
    update('upload', 'profile/picture/', { method: 'POST', body }, 'Profile picture updated.')
  }
  if (resource.loading) return <Loading/>
  if (resource.error) return <ErrorNotice error={resource.error} retry={resource.retry}/>
  return <>
    <PageHeading eyebrow="Your account" title="My profile" subtitle="Update your name and profile picture."/>
    <ErrorNotice error={error}/>
    {success && <div class="success" role="status">{success}</div>}
    <div class="detail-grid">
      <Panel title="Personal details"><div class="panel-body">
        <form class="form" aria-label="Personal details" aria-busy={!!busy} onSubmit={e => { e.preventDefault(); if (!busy && !isGraphProfile) update('name', 'profile/', { method: 'PATCH', body: { first_name: firstName, last_name: lastName } }, 'Profile details updated.') }}>
          <div class="form-grid">
            <label>
              First name
              <input name="first_name" autoComplete="given-name" maxLength={150} value={firstName} disabled={!!busy || isGraphProfile} onInput={e => setFirstName(e.target.value)}/>
            </label>
            <label>
              Last name
              <input name="last_name" autoComplete="family-name" maxLength={150} value={lastName} disabled={!!busy || isGraphProfile} onInput={e => setLastName(e.target.value)}/>
            </label>
          </div>
          <label>Username<input value={resource.data.username} readOnly autoComplete="username"/></label>
          <label>Email address<input value={resource.data.email || ''} readOnly autoComplete="email"/></label>
          <p class="form-help">Username and email are managed by your identity provider or administrator.{isGraphProfile && " Your profile details are managed by Teams/Graph and cannot be edited here."}</p>
          <p class="muted">Signed in with {user.auth_source || 'Local'}.</p>
          <div class="actions"><button class="button" disabled={!!busy || isGraphProfile}>{busy === 'name' ? 'Saving…' : 'Save details'}</button></div>
        </form>
      </div></Panel>
      <Panel title="Profile picture"><div class="panel-body">
        <div class="profile-picture"><UserAvatar user={{ ...user, picture_url: pictureUrl }} revision={revision} large/></div>
        <form class="form" aria-label="Profile picture" aria-busy={!!busy} onSubmit={upload}>
          <label>Choose a picture<input ref={fileInput} name="picture" type="file" accept="image/jpeg,image/png,image/webp" aria-describedby="picture-help" disabled={!!busy || isGraphProfile} onChange={chooseFile}/></label>
          <p class="form-help" id="picture-help">JPEG, PNG, or WEBP; up to 5 MiB and 4096 × 4096 pixels. Pictures are resized for your profile.</p>
          <div class="actions"><button class="button" disabled={!!busy || !file || isGraphProfile}>{busy === 'upload' ? 'Uploading…' : 'Upload picture'}</button><button class="button secondary" type="button" disabled={!!busy || !pictureUrl || isGraphProfile} onClick={() => update('remove', 'profile/picture/', { method: 'DELETE' }, 'Profile picture removed.')}>{busy === 'remove' ? 'Removing…' : 'Remove picture'}</button></div>
        </form>
      </div></Panel>
    </div>
  </>
}
function Panel({ title, count, action, children }) { return <section class="panel"><div class="panel-heading"><div class="panel-title"><h2>{title}</h2>{count != null && <span class="counter">{count}</span>}</div>{action}</div>{children}</section> }
function PageHeading({ eyebrow = 'Your workspace', title, subtitle, children }) { return <div class="page-heading"><div><div class="eyebrow">{eyebrow}</div><h1>{title}</h1><p class="muted">{subtitle}</p></div>{children}</div> }
function Dialog({ title, close, children }) {
  const ref = useRef()
  useEffect(() => {
    const previous = document.activeElement
    const bodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    ref.current?.querySelector('input, select, button')?.focus()
    const handler = e => {
      if (e.key === 'Escape') close()
      if (e.key === 'Tab') {
        const items = [...ref.current.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)')]
        const first = items[0], last = items.at(-1)
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
        if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
      }
    }
    document.addEventListener('keydown', handler)
    return () => { document.body.style.overflow = bodyOverflow; document.removeEventListener('keydown', handler); previous?.focus() }
  }, [])
  return <div class="dialog-backdrop" onClick={e => e.target === e.currentTarget && close()}><section ref={ref} class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><div class="panel-heading"><h2 id="dialog-title">{title}</h2><button class="close" aria-label="Close dialog" onClick={close}><Icon name="close"/></button></div><div class="panel-body">{children}</div></section></div>
}
function Login({ session, onLogin }) {
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  async function submit(e) {
    e.preventDefault(); setBusy(true); setError('')
    const fields = new FormData(e.currentTarget)
    try { await api('login/', { method: 'POST', body: { username: fields.get('username'), password: fields.get('password') } }); await onLogin(); location.hash = '/dashboard' } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  const providers = (session.providers || []).filter(provider => {
    try { return new URL(provider.login_url, location.origin).origin === location.origin } catch { return false }
  })
  return <div class="login"><aside class="login-art"><div class="brand"><span class="brand-mark">g</span>gather<span style="color:#b8d99d">.</span></div><div><div class="art-shapes"><span/><span/><span/></div><h1>Good relationships<br/>start with a<br/>conversation.</h1><p>A little more connection. A lot less busywork. Your contacts, calls, and calendar, together.</p></div><small>Made for meaningful connections.</small></aside><main class="login-main"><div class="login-card"><div class="eyebrow">Welcome to Gather</div><h1>A fresh start, every day.</h1><p class="muted">Sign in to your team's relationship workspace.</p><form class="form" onSubmit={submit}><ErrorNotice error={error}/><label>Username<input name="username" autoComplete="username" required/></label><label>Password<input name="password" type="password" autoComplete="current-password" required/></label><button class="button" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}<Icon name="arrow" size={16}/></button></form>{providers.length > 0 && <><div class="divider">or continue with</div><div class="login-providers">{providers.map(provider => <a class="button secondary" href={provider.login_url} key={provider.id}>Continue with {provider.name}</a>)}</div></>}<p class="muted" style="margin-top:25px;font-size:11px">A shared contact directory. Your own private conversations.</p></div></main></div>
}
function AppointmentRow({ appointment }) { return <a class="list-row" href={`#/appointments/${appointment.id}`}><div class="date-tile">{date(appointment.starts_at, { month: 'short', day: undefined })}<strong>{new Date(appointment.starts_at).getDate()}</strong></div><div class="row-main"><strong>{appointment.call.contact.name}</strong><small>{appointment.call.contact.company?.name || appointment.call.contact.email}</small></div><div class="row-end"><strong style="font-size:12px">{time(appointment.starts_at)}</strong><div class="muted">{time(appointment.ends_at)}</div></div><Icon name="arrow" size={14}/></a> }
function KpiTable({ title, rows }) { return <Panel title={title}><div class="table-wrap"><table><thead><tr><th>Name</th><th>Calls</th><th>Completed</th><th>Avg. rating</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}><td>{row.name}</td><td>{row.total_calls}</td><td>{row.completed_calls}</td><td>{row.average_rating == null ? '—' : Number(row.average_rating).toFixed(1)}</td></tr>)}</tbody></table>{!rows.length && <Empty title="Your insights will grow here">Start logging conversations to see results.</Empty>}</div></Panel> }
function Dashboard({ user, version, openCreate }) {
  const resource = useResource('kpis/', version), appointments = useResource('appointments/', version), calls = useResource('calls/', version), inbox = useResource(user.graph_enabled ? null : 'inbox/', version)
  const authSource = <p class="muted">Signed in with {user.auth_source || "Local"}</p>
  if ([resource, appointments, calls, inbox].some(r => r.loading)) return <>{authSource}<Loading/></>
  const failed = [resource, appointments, calls, inbox].filter(r => r.error)
  if (failed.length) return <><PageHeading title="Your day, at a glance" subtitle="A little clarity for your next conversation."/>{authSource}{failed.map((r, i) => <ErrorNotice key={i} error={r.error} retry={r.retry}/>)}</>
  const kpis = resource.data, now = Date.now()
  const upcoming = appointments.data.appointments.filter(a => ['scheduled', 'rescheduled'].includes(a.call.status) && new Date(a.ends_at).getTime() >= now).sort((a,b) => new Date(a.starts_at) - new Date(b.starts_at))
  const followups = calls.data.calls.filter(call => {
    const past = call.appointment && new Date(call.appointment.ends_at).getTime() < now
    return (past && ['scheduled', 'rescheduled'].includes(call.status)) || (call.status === 'completed' && (!call.summary?.trim() || call.rating == null))
  })
  const unread = inbox.data?.emails.filter(e => !e.is_read).length || 0
  const stats = [
    ['Total conversations', kpis.total_calls, 'All-time calls across your workspace', 'calls'],
    ['Completed calls', kpis.completed_calls, 'Conversations brought to a close', 'check'],
    ['Average rating', kpis.average_rating == null ? '—' : `${Number(kpis.average_rating).toFixed(1)} / 5`, 'Rated completed conversations', 'star'],
    ['Missed calls', `${Number(kpis.missed_percentage || 0).toFixed(1)}%`, `${Number(kpis.rescheduled_percentage || 0).toFixed(1)}% rescheduled`, 'clock'],
  ]
  return <>
    <PageHeading title="Your day, at a glance" subtitle="Stay connected. Keep the conversation moving."><button class="button" onClick={() => openCreate('call')}><Icon name="plus" size={16}/>Log a call</button></PageHeading>
    <div class="welcome-strip"><div><h2>Good {new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 18 ? 'afternoon' : 'evening'}, {user.name?.split(' ')[0] || user.username}.</h2>{authSource}<p class="muted">{user.graph_enabled ? 'Your Microsoft calendar and inbox are below. CRM calls and insights remain in your workspace.' : `You have ${upcoming.length} upcoming appointment${upcoming.length !== 1 ? 's' : ''} and ${unread} unread message${unread !== 1 ? 's' : ''}. Let's make today count.`}</p></div><span class="plant" aria-hidden="true">✳</span></div>
    <div class="stats">{stats.map(([label,value,caption,icon]) => <div class="stat" key={label}><div class="stat-top">{label}<span class="stat-icon"><Icon name={icon}/></span></div><div class="stat-value">{value}</div><p class="stat-caption">{caption}</p></div>)}</div>
    <div class="dashboard-grid"><div>
      {user.graph_enabled ? <GraphCalendar version={version} compact/> : <Panel title="Coming up next" count={upcoming.length} action={<a class="button quiet" href="#/calendar">View calendar <Icon name="arrow" size={13}/></a>}>{upcoming.slice(0,4).map(a => <AppointmentRow appointment={a} key={a.id}/>)}{!upcoming.length && <Empty title="A little breathing room" icon="calendar">No upcoming appointments. Schedule your next conversation.</Empty>}</Panel>}
      <Panel title="Conversations to wrap up" count={followups.length} action={<a class="button quiet" href="#/calls">All calls <Icon name="arrow" size={13}/></a>}>{followups.slice(0,4).map(call => <a class="list-row" key={call.id} href={`#/calls/${call.id}`}><span class="avatar">{initials(call.contact.name)}</span><div class="row-main"><strong>{call.contact.name}</strong><small>{call.status === 'completed' ? 'Add a summary or rating' : 'Update the call outcome'} · {date(call.appointment?.starts_at)}</small></div><Badge status={call.status}/><Icon name="arrow" size={14}/></a>)}{!followups.length && <Empty title="All caught up" icon="check">Your past conversations are up to date.</Empty>}</Panel>
    </div><div>
      {user.graph_enabled ? <GraphInbox version={version} compact/> : <Panel title="Your inbox" count={unread} action={<a class="button quiet" href="#/inbox">View inbox <Icon name="arrow" size={13}/></a>}>{inbox.data.emails.slice().sort((a,b) => new Date(b.received_at)-new Date(a.received_at)).slice(0,4).map(email => <a class="list-row" href={`#/inbox/${email.id}`} key={email.id}><span class="avatar">{initials(email.contact.name)}</span><div class="row-main"><strong>{email.contact.name}{!email.is_read && <span class="notification-dot"/>}</strong><p class="muted truncate">{email.subject || '(No subject)'}</p><small>{date(email.received_at)}</small></div><Badge status={email.is_replied ? 'replied' : email.is_read ? 'read' : 'unread'}/></a>)}{!inbox.data.emails.length && <Empty title="A quiet inbox" icon="inbox">Incoming messages will appear here.</Empty>}</Panel>}
      <Panel title="A note on your numbers"><div class="panel-body"><p class="muted">Your insights cover all time. Missed and rescheduled percentages use non-scheduled calls as their denominator. Average ratings include rated, completed calls only.</p></div></Panel>
    </div></div>
    <div class="dashboard-grid"><KpiTable title="Performance by contact" rows={kpis.by_contact || []}/><KpiTable title="Performance by company" rows={kpis.by_company || []}/></div>
  </>
}
function Calls({ version, openCreate }) {
  const r = useResource('calls/', version)
  const [status, setStatus] = useState('all'), [query, setQuery] = useState('')
  const calls = (r.data?.calls || []).filter(c => (status === 'all' || c.status === status) && `${c.contact.name} ${c.contact.email} ${c.summary || ''}`.toLowerCase().includes(query.toLowerCase()))
  return <><PageHeading title="Every conversation matters." eyebrow="Call log" subtitle="Track outcomes, capture details, and keep moving forward."><button class="button" onClick={() => openCreate('call')}><Icon name="plus" size={16}/>Log a call</button></PageHeading><div class="toolbar"><input aria-label="Search calls" placeholder="Search contacts or summaries…" value={query} onInput={e => setQuery(e.target.value)}/><select aria-label="Filter calls by status" value={status} onChange={e => setStatus(e.target.value)}><option value="all">All statuses</option>{statuses.map(s => <option value={s} key={s}>{s[0].toUpperCase()+s.slice(1)}</option>)}</select></div><ErrorNotice error={r.error} retry={r.retry}/>{r.loading ? <Loading/> : !r.error && <Panel title="Conversations" count={calls.length}><div class="table-wrap"><table><thead><tr><th>Contact</th><th>Appointment</th><th>Status</th><th>Rating</th><th>Summary</th><th><span class="visually-hidden">Details</span></th></tr></thead><tbody>{calls.map(c => <tr key={c.id}><td><Contact contact={c.contact}/></td><td>{dateTime(c.appointment?.starts_at)}</td><td><Badge status={c.status}/></td><td>{c.rating == null ? '—' : `${c.rating} / 5`}</td><td><div class="truncate">{c.summary || 'No summary yet'}</div></td><td><a class="link" href={`#/calls/${c.id}`}>Details →</a></td></tr>)}</tbody></table></div>{!calls.length && <Empty title={query || status !== 'all' ? 'No matching conversations' : 'Start a conversation'} icon="calls">{query || status !== 'all' ? 'Try another search or status.' : 'Log your first call to keep its details in one place.'}</Empty>}</Panel>}</>
}
function CallFields({ status, setStatus, summary, setSummary, rating, setRating }) { return <><label>Call status<select value={status} onChange={e => {setStatus(e.target.value);if (e.target.value !== 'completed') setRating('')}}>{statuses.map(s => <option value={s} key={s}>{s[0].toUpperCase()+s.slice(1)}</option>)}</select></label><label>Conversation summary<textarea value={summary} onInput={e => setSummary(e.target.value)} placeholder="What did you discuss? What comes next?"/></label><label>Conversation rating<select value={rating ?? ''} onChange={e => setRating(e.target.value)} disabled={status !== 'completed'}><option value="">Not rated</option>{[1,2,3,4,5].map(n => <option value={n} key={n}>{n} / 5{n === 5 ? ' · Excellent' : n === 1 ? ' · Needs improvement' : ''}</option>)}</select></label><p class="form-help">Keep a summary for any call status. Ratings apply only to completed calls and are cleared when you choose another status.</p></> }
function CallDetail({ id, version, refresh, openCreate }) {
  const r = useResource(`calls/${id}/`, version)
  const [status, setStatus] = useState('scheduled'), [summary, setSummary] = useState(''), [rating, setRating] = useState(''), [busy,setBusy] = useState(false), [error,setError] = useState(''), [saved,setSaved] = useState(false)
  useEffect(() => { if (r.data) { setStatus(r.data.status); setSummary(r.data.summary || ''); setRating(r.data.rating ?? '') } }, [r.data])
  async function save(e) {
    e.preventDefault(); setBusy(true); setError(''); setSaved(false)
    try { await api(`calls/${id}/`, { method:'PATCH', body:{ status, summary, rating: status === 'completed' && rating !== '' ? Number(rating) : null } }); setSaved(true); refresh() } catch(err) { setError(err.message) } finally { setBusy(false) }
  }
  if (r.loading) return <Loading/>
  if (r.error) return <ErrorNotice error={r.error} retry={r.retry}/>
  const call = r.data
  return <><a class="back" href="#/calls">← Back to call log</a><PageHeading title={`Conversation with ${call.contact.name}`} eyebrow={`Call #${call.id}`} subtitle={call.contact.company?.name || call.contact.email}/><div class="detail-grid"><Panel title="Conversation details"><div class="panel-body"><ErrorNotice error={error}/>{saved && <div class="success" role="status">Conversation updated.</div>}<form class="form" onSubmit={save}><CallFields {...{status,setStatus,summary,setSummary,rating,setRating}}/><div class="actions"><button class="button" disabled={busy}>{busy ? 'Saving…' : 'Save changes'}</button><a class="button secondary" href="#/calls">Back to calls</a></div></form></div></Panel><div><Panel title="Contact"><div class="panel-body"><Contact contact={call.contact}/><dl><div class="info-row"><dt>Email</dt><dd><a class="link" href={`mailto:${call.contact.email}`}>{call.contact.email}</a></dd></div><div class="info-row"><dt>Company</dt><dd>{call.contact.company?.name || '—'}</dd></div></dl></div></Panel><Panel title="Appointment"><div class="panel-body">{call.appointment ? <><p>{dateTime(call.appointment.starts_at)}</p><p class="muted" style="margin:6px 0 17px">Until {time(call.appointment.ends_at)}</p><a class="button secondary" href={`#/appointments/${call.appointment.id}`}>View or reschedule</a></> : <><p class="muted" style="margin-bottom:17px">No appointment scheduled for this conversation.</p><button class="button secondary" onClick={() => openCreate('appointment', call.id)}>Schedule appointment</button></>}</div></Panel></div></div></>
}
function AppointmentFields({ starts, setStarts, ends, setEnds, notes, setNotes }) { return <><div class="form-grid"><label>Starts at<input type="datetime-local" value={starts} onInput={e => setStarts(e.target.value)} required/></label><label>Ends at<input type="datetime-local" value={ends} onInput={e => setEnds(e.target.value)} required min={starts || undefined}/></label></div><p class="form-help">Times are shown in your local timezone ({Intl.DateTimeFormat().resolvedOptions().timeZone}). They are saved in UTC.</p><label>Appointment notes<textarea value={notes} onInput={e => setNotes(e.target.value)} placeholder="An agenda, a reminder, or a little context…"/></label></> }
function appointmentPayload(starts, ends, notes, original = null) {
  const unchangedStart = original && starts === localInput(original.starts_at)
  const unchangedEnd = original && ends === localInput(original.ends_at)
  const start = new Date(unchangedStart ? original.starts_at : starts)
  const end = new Date(unchangedEnd ? original.ends_at : ends)
  if (!starts || !ends || !Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || (!(unchangedStart && unchangedEnd) && end <= start)) throw new Error('Choose an end time after the start time.')
  return { starts_at: unchangedStart ? original.starts_at : start.toISOString(), ends_at: unchangedEnd ? original.ends_at : end.toISOString(), notes }
}
function AppointmentDetail({ id, version, refresh }) {
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
function Calendar({ version, openCreate }) {
  const r = useResource('appointments/', version), [month,setMonth] = useState(() => new Date(new Date().getFullYear(),new Date().getMonth(),1)), [agenda,setAgenda] = useState('next')
  const appointments = r.data?.appointments || []
  const first = new Date(month.getFullYear(),month.getMonth(),1), start = new Date(first)
  start.setDate(start.getDate() - ((first.getDay()+6)%7))
  const cells = Array.from({length:42},(_,i) => { const d = new Date(start); d.setDate(d.getDate()+i); return d })
  const dayKey = d => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
  const now = new Date()
  const next = appointments.filter(a => ['scheduled', 'rescheduled'].includes(a.call.status) && new Date(a.ends_at) >= now).sort((a,b) => new Date(a.starts_at) - new Date(b.starts_at))
  const past = appointments.filter(a => new Date(a.ends_at) < now).sort((a,b) => new Date(b.starts_at) - new Date(a.starts_at))
  const list = agenda === 'next' ? next : past
  return <><PageHeading eyebrow="Calendar" title="Make time for connection." subtitle="A clear view of what's ahead and what came before."><button class="button" onClick={() => openCreate('appointment')}><Icon name="plus" size={16}/>New appointment</button></PageHeading><ErrorNotice error={r.error} retry={r.retry}/>{r.loading ? <Loading/> : !r.error && <><section class="panel"><div class="calendar-header"><h2>{month.toLocaleDateString(undefined,{month:'long',year:'numeric'})}</h2><div class="actions"><button class="button secondary" aria-label="Previous month" onClick={() => setMonth(new Date(month.getFullYear(),month.getMonth()-1,1))}>←</button><button class="button secondary" onClick={() => setMonth(new Date(now.getFullYear(),now.getMonth(),1))}>Today</button><button class="button secondary" aria-label="Next month" onClick={() => setMonth(new Date(month.getFullYear(),month.getMonth()+1,1))}>→</button></div></div><div class="calendar-grid">{['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(day => <div class="weekday" key={day}>{day}</div>)}{cells.map(d => <div key={dayKey(d)} class={`calendar-day ${d.getMonth() !== month.getMonth() ? 'other' : ''} ${dayKey(d) === dayKey(now) ? 'today-cell' : ''}`}><span class="day-number" aria-label={date(d, {year:'numeric',month:'long'})}>{d.getDate()}</span>{appointments.filter(a => dayKey(new Date(a.starts_at)) === dayKey(d)).sort((a,b) => new Date(a.starts_at)-new Date(b.starts_at)).map(a => <a class="calendar-event" href={`#/appointments/${a.id}`} key={a.id} title={`${a.call.contact.name} · ${dateTime(a.starts_at)}`}>{time(a.starts_at)} {a.call.contact.name}</a>)}</div>)}</div></section><Panel title="Your agenda" count={list.length} action={<div class="tabs" role="group" aria-label="Agenda timeframe"><button class={agenda === 'next' ? 'selected' : ''} aria-pressed={agenda === 'next'} onClick={() => setAgenda('next')}>Upcoming</button><button class={agenda === 'past' ? 'selected' : ''} aria-pressed={agenda === 'past'} onClick={() => setAgenda('past')}>Past</button></div>}>{list.map(a => <div key={a.id}><div class="agenda-heading">{date(a.starts_at, { weekday:'long',year:'numeric' })}</div><AppointmentRow appointment={a}/></div>)}{!list.length && <Empty title={agenda === 'next' ? 'Your calendar has room' : 'No past appointments'} icon="calendar">{agenda === 'next' ? 'Schedule a conversation to get started.' : 'Previous appointments will be listed here.'}</Empty>}</Panel></>}</>
}
function Contacts({ version, openCreate }) {
  const r = useResource('contacts/', version), [query,setQuery] = useState('')
  const list = (r.data?.contacts || []).filter(c => `${c.name} ${c.email} ${c.company?.name}`.toLowerCase().includes(query.toLowerCase()))
  return <><PageHeading eyebrow="Contacts" title="People, not just records." subtitle="A shared directory of people and the companies they belong to."><div class="actions"><button class="button secondary" onClick={() => openCreate('company')}><Icon name="plus" size={16}/>Add company</button><button class="button" onClick={() => openCreate('contact')}><Icon name="plus" size={16}/>Add contact</button></div></PageHeading><div class="toolbar"><input aria-label="Search contacts" placeholder="Find a name, email, or company…" value={query} onInput={e => setQuery(e.target.value)}/></div><ErrorNotice error={r.error} retry={r.retry}/>{r.loading ? <Loading/> : !r.error && <Panel title="Your contacts" count={list.length}><div class="table-wrap"><table><thead><tr><th>Contact</th><th>Email</th><th>Company</th><th>Next step</th></tr></thead><tbody>{list.map(c => <tr key={c.id}><td><Contact contact={c}/></td><td><a class="link" href={`mailto:${c.email}`}>{c.email}</a></td><td>{c.company?.name || '—'}</td><td><button class="button secondary" onClick={() => openCreate('call',c.id)}>Log a call</button></td></tr>)}</tbody></table></div>{!list.length && <Empty title={query ? 'No contacts found' : 'Your next connection starts here'}>{query ? 'Try a different name or email.' : 'Add your first contact to begin logging conversations.'}</Empty>}</Panel>}</>
}
function Inbox({ version, refresh, id }) {
  const r = useResource('inbox/', version), [filter,setFilter] = useState('all'), [error,setError] = useState(''), [busy,setBusy] = useState(false), [notice,setNotice] = useState('')
  const all = (r.data?.emails || []).slice().sort((a,b) => new Date(b.received_at)-new Date(a.received_at))
  const list = all.filter(e => filter === 'all' || (filter === 'unread' && !e.is_read) || (filter === 'unreplied' && !e.is_replied))
  const selected = id ? all.find(e => String(e.id) === String(id)) : list[0]
  useEffect(() => {setError('');setNotice('')}, [id])
  async function update(body) {
    setBusy(true);setError('');setNotice('')
    try { await api(`inbox/${selected.id}/`,{method:'PATCH',body});setNotice(body.is_replied ? 'Marked as replied. No email was sent.' : 'Message tracking updated.'); refresh() } catch(err){setError(err.message)} finally{setBusy(false)}
  }
  return <><PageHeading eyebrow="Inbox" title="Keep the conversation going." subtitle="Incoming messages and a clear record of your follow-through."/><div class="toolbar"><select style="margin-left:0" aria-label="Filter inbox" value={filter} onChange={e => {setFilter(e.target.value);location.hash='/inbox'}}><option value="all">All messages</option><option value="unread">Unread</option><option value="unreplied">Not replied</option></select><span class="muted">Tracking only · no emails are sent from Gather</span></div><ErrorNotice error={r.error} retry={r.retry}/>{r.loading ? <Loading/> : !r.error && <section class="panel"><div class="inbox-layout"><div class="inbox-list">{list.map(email => <a class={`email-item ${selected?.id === email.id ? 'selected' : ''} ${!email.is_read ? 'unread' : ''}`} href={`#/inbox/${email.id}`} key={email.id} style="text-decoration:none"><div class="email-top"><strong>{email.contact.name}{!email.is_read && <span class="notification-dot"/>}</strong><small>{date(email.received_at)}</small></div><p>{email.subject || '(No subject)'}</p><div style="margin-top:8px"><Badge status={email.is_replied ? 'replied' : email.is_read ? 'read' : 'unread'}/></div></a>)}{!list.length && <Empty title="No messages here" icon="inbox">Try another filter or check back later.</Empty>}</div><div class="email-body">{selected ? <><ErrorNotice error={error}/>{notice && <div class="success" role="status">{notice}</div>}<h2>{selected.subject || '(No subject)'}</h2><p class="email-metadata">From {selected.contact.name} &lt;{selected.contact.email}&gt;</p><p class="email-metadata">{dateTime(selected.received_at)}</p><div class="actions" style="margin-top:16px"><Badge status={selected.is_read ? 'read' : 'unread'}/><Badge status={selected.is_replied ? 'replied' : 'not replied'}/></div><div class="email-content">{selected.body || '(Empty message)'}</div><div class="actions"><button class="button secondary" disabled={busy} onClick={() => update({is_read:!selected.is_read})}>{selected.is_read ? 'Mark unread' : 'Mark read'}</button><button class="button" disabled={busy} onClick={() => update({is_replied:!selected.is_replied})}>{selected.is_replied ? 'Mark not replied' : 'Mark replied'}</button></div><p class="form-help" style="margin-top:15px">These buttons update tracking only. Reply using your email application.</p></> : <Empty title={id ? 'Message not found' : 'Choose a conversation'} icon="inbox">{id ? 'This message is no longer available.' : 'Select a message to read it and update its status.'}</Empty>}</div></div></section>}</>
}
function CreateForm({ type, initialId, done, close }) {
  const contacts = useResource(type === 'call' ? 'contacts/' : null), calls = useResource(type === 'appointment' ? 'calls/' : null), companies = useResource(type === 'contact' ? 'companies/' : null)
  const [selected,setSelected] = useState(initialId || ''), [status,setStatus] = useState('scheduled'), [summary,setSummary] = useState(''), [rating,setRating] = useState(''), [starts,setStarts] = useState(''), [ends,setEnds] = useState(''), [notes,setNotes] = useState(''), [error,setError] = useState(''), [busy,setBusy] = useState(false)
  const pending = type === 'call' ? contacts : type === 'contact' ? companies : calls
  const unscheduled = (calls.data?.calls || []).filter(c => !c.appointment)
  async function submit(e) {
    e.preventDefault();setBusy(true);setError('')
    const form = new FormData(e.currentTarget)
    try {
      let result
      if (type === 'company') result = await api('companies/',{method:'POST',body:{name:form.get('name').trim()}})
      else if (type === 'contact') result = await api('contacts/',{method:'POST',body:{name:form.get('name').trim(),email:form.get('email').trim(),company_id:Number(form.get('company_id'))}})
      else if (type === 'call') result = await api('calls/',{method:'POST',body:{contact_id:Number(selected),status,summary,rating:status === 'completed' && rating !== '' ? Number(rating) : null}})
      else result = await api('appointments/',{method:'POST',body:{call_id:Number(selected),...appointmentPayload(starts,ends,notes)}})
      done(type,result)
    } catch(err) { setError(err.message) } finally {setBusy(false)}
  }
  if (type !== 'company' && pending.loading) return <Loading/>
  if (type !== 'company' && pending.error) return <ErrorNotice error={pending.error} retry={pending.retry}/>
  const available = type === 'call' ? contacts.data?.contacts || [] : unscheduled
  if (['call','appointment'].includes(type) && !available.length) return <Empty title={type === 'call' ? 'Add a contact first' : 'No unscheduled calls'} icon={type === 'call' ? 'contacts' : 'calendar'}><span>{type === 'call' ? 'Create a contact, then log your conversation.' : 'Each call can have one appointment. Log a new call, or edit an existing appointment.'}</span><div class="actions" style="justify-content:center;margin-top:15px"><a class="button" onClick={close} href={type === 'call' ? '#/contacts' : '#/calls'}>{type === 'call' ? 'Go to contacts' : 'Go to calls'}</a></div></Empty>
  if (type === 'contact' && !companies.data.companies.length) return <Empty title="Add a company first">Contacts belong to their own organizations. Close this form and use “Add company” on the contacts page.</Empty>
  return <form class="form" onSubmit={submit}>
    <ErrorNotice error={error}/>
    {type === 'company' ? <><label>Company name<input name="name" autoComplete="organization" required maxLength={200}/></label><p class="form-help">Add an organization to the shared company directory. You can then assign contacts to it.</p></> :
      type === 'contact' ? <><label>Full name<input name="name" autoComplete="name" required maxLength={200}/></label><label>Email address<input name="email" type="email" autoComplete="email" required/></label><label>Contact's company<select name="company_id" required defaultValue=""><option value="">Choose a company…</option>{companies.data.companies.map(company => <option key={company.id} value={company.id}>{company.name}</option>)}</select></label><p class="form-help">Choose the contact's organization, not necessarily your employer. Companies and contacts are shared; your conversations remain private to you.</p></> :
      <><label>{type === 'call' ? 'Contact' : 'Unscheduled call'}<select value={selected} required onChange={e => setSelected(e.target.value)}><option value="">Choose {type === 'call' ? 'a contact' : 'a call'}…</option>{available.map(item => <option key={item.id} value={item.id}>{type === 'call' ? `${item.name} · ${item.company?.name || item.email}` : `${item.contact.name} · Call #${item.id} · ${item.status}`}</option>)}</select></label>{type === 'call' ? <CallFields {...{status,setStatus,summary,setSummary,rating,setRating}}/> : <AppointmentFields {...{starts,setStarts,ends,setEnds,notes,setNotes}}/>}</>}
    <div class="actions"><button class="button" disabled={busy}>{busy ? 'Creating…' : type === 'company' ? 'Add company' : type === 'contact' ? 'Add contact' : type === 'call' ? 'Create call' : 'Create appointment'}</button><button type="button" class="button secondary" disabled={busy} onClick={close}>Cancel</button></div>
  </form>
}
export function App() {
  if (location.pathname === '/teams/config/') return <TeamsConfig/>
  if (location.pathname === '/teams/tab/') return <TeamsTab/>
  return <WorkspaceApp/>
}

function WorkspaceApp({ initialSession = null, embedded = false, onSessionEnd }) {
  const [session,setSession] = useState(initialSession), [loading,setLoading] = useState(!initialSession), [error,setError] = useState(''), [route,setRoute] = useState(location.hash.slice(1) || '/dashboard'), [version,setVersion] = useState(0), [modal,setModal] = useState(null), [mobile,setMobile] = useState(false), [signingOut,setSigningOut] = useState(false)
  const [profileRevision, setProfileRevision] = useState(0)
  async function loadSession() { setError(''); const value = await api('session/');setSession(value);return value }
  async function refreshProfile() { setProfileRevision(n => n + 1); await loadSession() }
  useEffect(() => {
    if (!initialSession) loadSession().catch(e => setError(e.message)).finally(() => setLoading(false))
    const hash = () => {setRoute(location.hash.slice(1) || '/dashboard');setMobile(false);setModal(null);window.scrollTo(0,0)}
    const expired = () => {
      setSession({user:null,agent:null,providers:session?.providers || []});setModal(null)
      if (embedded) onSessionEnd()
      else loadSession().catch(e => setError(e.message))
    }
    window.addEventListener('hashchange',hash);window.addEventListener('session-expired',expired)
    return () => {window.removeEventListener('hashchange',hash);window.removeEventListener('session-expired',expired)}
  }, [])
  useEffect(() => { if (session?.user && (!location.hash || route === '/login' || route === '/')) location.hash='/dashboard' },[session,route])
  async function logout() {setSigningOut(true);setError('');try{await api('logout/',{method:'POST'});if (embedded) onSessionEnd();else {await loadSession();location.hash='/login'}}catch(e){setError(e.message)}finally{setSigningOut(false)}}
  const refresh = () => setVersion(n => n+1)
  const openCreate = (type,initialId) => setModal({type,initialId})
  function done(type,result) {setModal(null);refresh();const id = result.id || result[type]?.id;if (id && ['call','appointment'].includes(type)) location.hash=`/${type === 'call' ? 'calls' : 'appointments'}/${id}`}
  if (loading) return <Loading/>
  if (!session) return <div class="provision"><div class="brand" style="color:#294733;justify-content:center;margin-bottom:25px"><span class="brand-mark">g</span>gather.</div><ErrorNotice error={error} retry={() => {setLoading(true);loadSession().catch(e=>setError(e.message)).finally(()=>setLoading(false))}}/></div>
  if (!session.user) return embedded ? <ErrorNotice error="Your Teams session has ended. Reload this tab to sign in again."/> : <Login session={session} onLogin={loadSession}/>
  const parts = route.split('/').filter(Boolean), section = !session.agent && parts[0] !== 'profile' ? 'access' : parts[0] || 'dashboard', id = /^\d+$/.test(parts[1] || '') ? parts[1] : null
  const props = {version,refresh,openCreate}
  let page
  if (section === 'profile') page = <Profile key={session.user.id} user={session.user} revision={profileRevision} onUpdate={refreshProfile}/>
  else if (!session.agent) page = <div class="provision"><div class="eyebrow">You're signed in</div><h1>Your workspace is on its way.</h1><p class="muted">Your account is not linked to an agent workspace yet. Ask your administrator to provision your access, then check again.</p><div class="actions" style="margin-top:25px;justify-content:center"><button class="button" onClick={() => loadSession().catch(e => setError(e.message))}>Check access</button><a class="button secondary" href="#/profile">My profile</a></div></div>
  else if (section === 'dashboard') page = <Dashboard user={session.user} {...props}/>
  else if (section === 'calls') page = id ? <CallDetail key={id} id={id} {...props}/> : <Calls {...props}/>
  else if (section === 'appointments' && id) page = <AppointmentDetail key={id} id={id} {...props}/>
  else if (section === 'calendar') page = session.user.graph_enabled ? <GraphCalendar {...props}/> : <Calendar {...props}/>
  else if (section === 'contacts') page = <Contacts {...props}/>
  else if (section === 'inbox') page = session.user.graph_enabled ? <GraphInbox {...props}/> : <Inbox id={id} {...props}/>
  else page = <Empty title="This page wandered off"><a class="link" href="#/dashboard">Return to your overview →</a></Empty>
  return <>
    <a class="skip-link" href="#main-content" onClick={e => {e.preventDefault();document.getElementById('main-content').focus()}}>Skip to content</a>
    <div class="shell">
      {mobile && <button class="nav-backdrop" aria-label="Close navigation" onClick={() => setMobile(false)}/>}
      <aside class={`sidebar ${mobile ? 'open' : ''}`}>
        <div style="display:flex;align-items:center;justify-content:space-between">
          <a class="brand" href={session.agent ? '#/dashboard' : '#/access'}><span class="brand-mark">g</span>gather<span style="color:#b8d99d">.</span></a>
          <button class="menu-toggle" style="color:#d5edb7;margin:0" aria-label="Close navigation" onClick={() => setMobile(false)}><Icon name="close"/></button>
        </div>
        <div class="workspace">Your workspace</div>
        <nav class="nav" aria-label="Main navigation">{(session.agent ? ['dashboard','calls','calendar','contacts','inbox','profile'] : ['access','profile']).map(item => <a href={`#/${item}`} key={item} class={section === item || (item === 'calendar' && section === 'appointments') ? 'active' : ''} aria-current={section === item || (item === 'calendar' && section === 'appointments') ? 'page' : undefined} onClick={() => setMobile(false)}><Icon name={item}/>{labels[item]}</a>)}</nav>
        <div class="sidebar-bottom">
          <a href="/accounts/3rdparty/" target={embedded ? '_blank' : undefined} rel={embedded ? 'noopener noreferrer' : undefined}>Manage identity connections ↗</a>
          <div class="user"><UserAvatar key={session.user.id} user={session.user} revision={profileRevision}/><div><strong>{session.user.name || session.user.username} <GraphPresenceIndicator user={session.user}/></strong><small>{session.agent?.companies?.[0]?.name || 'Workspace access pending'}</small></div></div>
          <button class="signout" disabled={signingOut} onClick={logout}>{signingOut ? 'Signing out…' : 'Sign out'}</button>
        </div>
      </aside>
      <main class="main">
        <header class="topbar">
          <div style="display:flex;align-items:center">
            <button class="menu-toggle" aria-label={mobile ? 'Close navigation' : 'Open navigation'} aria-expanded={mobile} onClick={() => setMobile(!mobile)}><Icon name={mobile ? 'close' : 'menu'}/></button>
            <span class="mobile-brand">gather.</span><span class="breadcrumb">Workspace <span style="margin-left:10px">/</span><strong>{labels[section] || 'Page'}</strong></span>
          </div>
          <div class="today"><Icon name="calendar" size={15}/><span class="date-label">{date(new Date(),{weekday:'short',year:'numeric'})}</span></div>
        </header>
        <div class="content" id="main-content" tabIndex={-1}><ErrorNotice error={error}/><ErrorNotice error={session.graph_error || session.user.graph_error}/>{page}</div>
      </main>
    </div>
    {modal && session.agent && <Dialog title={modal.type === 'call' ? 'Log a conversation' : modal.type === 'contact' ? 'Add a new contact' : modal.type === 'company' ? 'Add a company' : 'Schedule a conversation'} close={() => setModal(null)}><CreateForm {...modal} done={done} close={() => setModal(null)}/></Dialog>}
  </>
}
