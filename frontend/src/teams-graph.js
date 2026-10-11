const scopes = ['User.Read', 'Calendars.Read', 'Mail.Read', 'Presence.Read']
let client

export async function startTeamsGraph(provider, expectedId, signal) {
  clearTeamsGraph()
  const [{ App }, me] = await Promise.all([
    import('@microsoft/teams.client'),
    import('@microsoft/teams.graph-endpoints/me'),
  ])
  signal?.throwIfAborted()
  const app = new App(provider.client_id, {
    msalOptions: {
      prewarmScopes: scopes,
      configuration: {
        auth: {
          clientId: provider.client_id,
          authority: `https://login.microsoftonline.com/${provider.tenant_id}`,
          supportsNestedAppAuth: true,
          redirectUri: window.location.origin,
        },
        cache: { cacheLocation: 'memoryStorage' },
      },
    },
  })
  await app.start()
  signal?.throwIfAborted()
  if (!await app.hasConsentForScopes(scopes)) {
    const result = await app.msalInstance.acquireTokenPopup({ scopes })
    app.msalInstance.setActiveAccount(result.account)
  }
  const profile = await app.graph.call(me.get, { $select: ['id'] })
  if (!expectedId || profile.id !== expectedId) throw new Error('Microsoft Graph identity does not match the portal session.')
  signal?.throwIfAborted()
  client = { app, me }
}

export function clearTeamsGraph() { client = undefined }
export function teamsGraphActive() { return !!client }

export function graphPageQuery(link, endpoint, params) {
  const url = new URL(link)
  const query = Object.fromEntries(url.searchParams)
  if (url.origin !== 'https://graph.microsoft.com' || url.username || url.password ||
      url.pathname !== `/v1.0${endpoint}` || url.hash ||
      url.searchParams.size !== Object.keys(query).length ||
      !('$skip' in query || '$skiptoken' in query) ||
      Object.keys(query).some(key => !(key in params) && !['$skip', '$skiptoken'].includes(key)) ||
      Object.values(query).some(value => value.length > 8192) ||
      Object.entries(params).some(([key, value]) => key in query && query[key] !== String(value))) {
    throw new Error('Invalid Microsoft Graph pagination link.')
  }
  return { ...params, ...query }
}

function eventTime(value) {
  if (!value?.dateTime || !['UTC', 'Etc/UTC'].includes(value.timeZone)) throw new Error('Invalid Microsoft Graph event time.')
  const date = new Date(/(?:[zZ]|[+-]\d\d:\d\d)$/.test(value.dateTime) ? value.dateTime : `${value.dateTime}Z`)
  return date.toISOString()
}

export async function teamsGraphRequest(path, { signal } = {}) {
  if (!client) throw new Error('Microsoft Graph is not initialized. Sign in with Teams again.')
  const { app, me } = client
  const url = new URL(path, 'https://portal.invalid/')
  const options = { requestConfig: { signal, timeout: 10000, headers: { Prefer: 'outlook.timezone="UTC", outlook.body-content-type="text"' } } }
  if (url.pathname === '/graph/presence/') return app.graph.call(me.presence.get, {}, options)
  if (['/graph/profile/', '/profile/'].includes(url.pathname)) {
    const data = await app.graph.call(me.get, { $select: ['id', 'displayName', 'givenName', 'surname', 'mail', 'userPrincipalName'] }, options)
    return {
      id: data.id, display_name: data.displayName || '', first_name: data.givenName || '',
      last_name: data.surname || '', email: data.mail || data.userPrincipalName || '',
      picture_url: '/api/graph/picture/', read_only: true, graph_enabled: true,
    }
  }
  let endpoint, params, operation
  if (url.pathname === '/graph/calendar/') {
    endpoint = '/me/calendarView'
    params = {
      startDateTime: url.searchParams.get('start'), endDateTime: url.searchParams.get('end'),
      $select: 'id,subject,bodyPreview,start,end,location,webLink,onlineMeetingUrl',
      $orderby: 'start/dateTime asc', $top: '50',
    }
    operation = me.calendarView.get
  } else if (url.pathname === '/graph/inbox/') {
    endpoint = '/me/mailFolders/inbox/messages'
    params = { $select: 'id,subject,body,bodyPreview,from,receivedDateTime,isRead,webLink', $orderby: 'receivedDateTime desc', $top: '25' }
    operation = me.mailFolders.messages.list
  } else {
    throw new Error('Unsupported Microsoft Graph request.')
  }
  const page = url.searchParams.get('page')
  const query = page ? graphPageQuery(page, endpoint, params) : params
  // The generated endpoint does not expose $skiptoken; keep pagination on the validated fixed path.
  const data = page
    ? (await app.graph.http.get(endpoint, { ...options.requestConfig, params: query })).data
    : await app.graph.call(operation, { ...params, 'mailFolder-id': 'inbox' }, options)
  if (!Array.isArray(data.value)) throw new Error('Invalid Microsoft Graph response.')
  const next = data['@odata.nextLink'] || null
  if (next) graphPageQuery(next, endpoint, params)
  if (endpoint === '/me/calendarView') return {
    events: data.value.map(event => ({
      id: event.id, subject: event.subject || '', body_preview: event.bodyPreview || '',
      starts_at: eventTime(event.start), ends_at: eventTime(event.end),
      location: event.location?.displayName || '', web_url: event.webLink,
      online_meeting_url: event.onlineMeetingUrl,
    })),
    next_page: next,
  }
  return {
    emails: data.value.map(message => {
      const body = message.body || {}
      const text = body.contentType?.toLowerCase() === 'html'
        ? new DOMParser().parseFromString(body.content || '', 'text/html').body.textContent
        : body.content || message.bodyPreview || ''
      return {
        id: message.id, subject: message.subject || '',
        contact: { name: message.from?.emailAddress?.name || '', email: message.from?.emailAddress?.address || '' },
        body: text, received_at: message.receivedDateTime, is_read: message.isRead || false,
        is_replied: false, web_url: message.webLink,
      }
    }),
    next_page: next,
  }
}

export async function teamsGraphPhoto(signal) {
  if (!client) return null
  try {
    const response = await client.app.graph.http.get('/me/photo/$value', { responseType: 'blob', signal, timeout: 10000 })
    return response.data
  } catch (error) {
    if (error.response?.status === 404) return null
    throw error
  }
}
