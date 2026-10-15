const scopes = ['User.Read', 'Calendars.Read', 'Mail.Read', 'Presence.Read']
let client

/**
 * Start the Teams nested-app authentication flow and bind Graph access to the
 * expected Entra tenant and user identity.
 * @param {{client_id: string, tenant_id: string}} provider OIDC provider details.
 * @param {string} expectedId Expected Entra object ID for the signed-in user.
 * @param {AbortSignal} [signal] Signal used to cancel setup between async steps.
 * @returns {Promise<void>}
 * @throws {Error} If authentication or identity validation fails.
 */
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
  let result: { account?: { tenantId?: string } | null }
  try {
    result = await app.msalInstance.acquireTokenSilent({ scopes })
  } catch {
    result = await app.msalInstance.acquireTokenPopup({ scopes })
  }
  if (result.account?.tenantId !== provider.tenant_id) {
    throw new Error('Microsoft Graph tenant does not match the Teams identity.')
  }
  app.msalInstance.setActiveAccount(
    result.account as Parameters<typeof app.msalInstance.setActiveAccount>[0],
  )
  const profile = (await app.graph.call(me.get, { $select: ['id'] })) as { id?: string }
  if (!expectedId || profile.id !== expectedId)
    throw new Error('Microsoft Graph identity does not match the Teams identity.')
  signal?.throwIfAborted()
  client = { app, me }
}

/** Clear the in-memory Teams Graph client. */
export function clearTeamsGraph() {
  client = undefined
}

/** Check whether a Teams Graph client is currently initialized. */
export function teamsGraphActive() {
  return !!client
}

/**
 * Convert a Microsoft Graph UTC date-time object to an ISO timestamp.
 * @param {{dateTime?: string, timeZone?: string}} value Graph date-time value.
 * @returns {string} The date and time as an ISO timestamp.
 * @throws {Error} If the value is missing or uses an unsupported time zone.
 */
function eventTime(value) {
  if (!value?.dateTime || !['UTC', 'Etc/UTC'].includes(value.timeZone))
    throw new Error('Invalid Microsoft Graph event time.')
  const date = new Date(
    /(?:[zZ]|[+-]\d\d:\d\d)$/.test(value.dateTime) ? value.dateTime : `${value.dateTime}Z`,
  )
  return date.toISOString()
}

/**
 * Run a supported read-only Graph request through the active Teams client.
 * Supports presence, profile, and previews of up to five events or messages.
 * @param {string} path Internal Graph request path and query string.
 * @param {{signal?: AbortSignal}} [options] Optional request cancellation signal.
 * @returns {Promise<object>} A normalized profile, presence, event, or email result.
 * @throws {Error} If Graph is not initialized, the path is unsupported, or data is invalid.
 */
export async function teamsGraphRequest(path: string, { signal }: { signal?: AbortSignal } = {}) {
  if (!client) throw new Error('Microsoft Graph is not initialized. Sign in with Teams again.')
  const { app, me } = client
  const url = new URL(path, 'https://portal.invalid/')
  const options = {
    requestConfig: {
      signal,
      timeout: 10000,
      headers: { Prefer: 'outlook.timezone="UTC", outlook.body-content-type="text"' },
    },
  }
  if (url.pathname === '/graph/presence/') return app.graph.call(me.presence.get, {}, options)
  if (['/graph/profile/', '/profile/'].includes(url.pathname)) {
    const data = await app.graph.call(
      me.get,
      { $select: ['id', 'displayName', 'givenName', 'surname', 'mail', 'userPrincipalName'] },
      options,
    )
    return {
      id: data.id,
      display_name: data.displayName || '',
      first_name: data.givenName || '',
      last_name: data.surname || '',
      email: data.mail || data.userPrincipalName || '',
      picture_url: '/api/graph/picture/',
      read_only: true,
      graph_enabled: true,
    }
  }
  let endpoint, params, operation
  if (url.pathname === '/graph/calendar/') {
    endpoint = '/me/calendarView'
    params = {
      startDateTime: url.searchParams.get('start'),
      endDateTime: url.searchParams.get('end'),
      $select: 'id,subject,bodyPreview,start,end,location,webLink,onlineMeetingUrl',
      $orderby: 'start/dateTime asc',
      $top: '5',
    }
    operation = me.calendarView.get
  } else if (url.pathname === '/graph/inbox/') {
    endpoint = `/me/mailFolders('inbox')/messages`
    params = {
      $select: 'id,subject,body,bodyPreview,from,receivedDateTime,isRead,webLink',
      $orderby: 'receivedDateTime desc',
      $top: '5',
    }
    operation = me.mailFolders.messages.list
  } else {
    throw new Error('Unsupported Microsoft Graph request.')
  }
  const data = await app.graph.call(operation, { ...params, 'mailFolder-id': 'inbox' }, options)
  if (!Array.isArray(data.value)) throw new Error('Invalid Microsoft Graph response.')
  if (endpoint === '/me/calendarView')
    return {
      events: data.value.slice(0, 5).map((event) => ({
        id: event.id,
        subject: event.subject || '',
        body_preview: event.bodyPreview || '',
        starts_at: eventTime(event.start),
        ends_at: eventTime(event.end),
        location: event.location?.displayName || '',
        web_url: event.webLink,
        online_meeting_url: event.onlineMeetingUrl,
      })),
    }
  return {
    emails: data.value.slice(0, 5).map((message) => {
      const body = message.body || {}
      const text =
        body.contentType?.toLowerCase() === 'html'
          ? new DOMParser().parseFromString(body.content || '', 'text/html').body.textContent
          : body.content || message.bodyPreview || ''
      return {
        id: message.id,
        subject: message.subject || '',
        contact: {
          name: message.from?.emailAddress?.name || '',
          email: message.from?.emailAddress?.address || '',
        },
        body: text,
        received_at: message.receivedDateTime,
        is_read: message.isRead || false,
        is_replied: false,
        web_url: message.webLink,
      }
    }),
  }
}

/**
 * Fetch the signed-in user's Graph profile photo.
 * @param {AbortSignal} [signal] Optional request cancellation signal.
 * @returns {Promise<Blob|null>} The photo data, or `null` if unavailable/not found.
 * @throws {Error} If Graph returns an error other than not found.
 */
export async function teamsGraphPhoto(signal) {
  if (!client) return null
  try {
    const response = await client.app.graph.http.get('/me/photo/$value', {
      responseType: 'blob',
      signal,
      timeout: 10000,
    })
    return response.data
  } catch (error) {
    if (error.response?.status === 404) return null
    throw error
  }
}
