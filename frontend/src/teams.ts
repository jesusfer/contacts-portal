let initialization

/**
 * Initialize the Teams SDK once and reuse the pending or completed initialization.
 * @returns {Promise<object>} The initialized Teams SDK app namespace.
 * @throws {Error} If the SDK cannot initialize or the page is not hosted in Teams.
 */
export function initializeTeams() {
  if (!initialization) {
    initialization = (async () => {
      const { app } = await import('@microsoft/teams-js')
      let timer
      try {
        await Promise.race([
          app.initialize(),
          new Promise((_, reject) => {
            timer = setTimeout(
              () => reject(new Error('Open this page in Microsoft Teams to continue.')),
              5000,
            )
          }),
        ])
        return app
      } finally {
        clearTimeout(timer)
      }
    })().catch((error) => {
      initialization = null
      throw error
    })
  }
  return initialization
}

/**
 * Get the Teams SSO token after initializing the Teams SDK.
 * @returns {Promise<string>} The Teams authentication token.
 */
export async function getTeamsAuthToken() {
  await initializeTeams()
  const { authentication } = await import('@microsoft/teams-js')
  return authentication.getAuthToken()
}

/**
 * Send a same-origin request to the Teams API and parse its JSON response.
 * @param {string} path API path below `/api/teams/`.
 * @param {RequestInit} [options] Fetch options for the request.
 * @param {string} [query=''] Query string, including its leading `?` when present.
 * @returns {Promise<unknown>} The decoded response data.
 * @throws {Error} If the response is not JSON or the request fails.
 */
async function request(path, options = {}, query = '') {
  const response = await fetch(`/api/teams/${path}/${query}`, {
    credentials: 'same-origin',
    ...options,
  })
  let data
  try {
    data = await response.json()
  } catch {
    throw new Error('Teams sign-in returned an unexpected response. Please try again.')
  }
  if (!response.ok) throw new Error(data.error || `Teams request failed (${response.status}).`)
  return data
}

/**
 * Exchange a Teams token for CRM Teams authentication or Graph-only identity.
 * @param {string} providerId The configured OIDC provider ID.
 * @param {string} token The Teams SSO token.
 * @param {{graphOnly?: boolean}} [options] Set `graphOnly` to request identity exchange.
 * @returns {Promise<unknown>} The exchange response.
 */
export function exchangeTeamsToken(providerId, token, { graphOnly = false } = {}) {
  return request(graphOnly ? 'identity' : 'sso', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ provider_id: providerId, token }),
  })
}

/** Fetch the available Teams/Graph provider configuration. */
export function getTeamsConfig() {
  return request('config')
}

/**
 * Read the current Teams tab configuration from the Teams SDK.
 * @returns {Promise<unknown>} The tab configuration.
 */
export async function getTeamsTabConfig() {
  await initializeTeams()
  const { pages } = await import('@microsoft/teams-js')
  return pages.getConfig()
}

/**
 * List CRM customers available for a configured provider.
 * @param {string} providerId The configured OIDC provider ID.
 * @returns {Promise<unknown>} The customer-list response.
 */
export function getTeamsCustomers(providerId) {
  return request('customers', {}, `?provider=${encodeURIComponent(providerId)}`)
}

/**
 * Fetch one CRM customer for a configured provider.
 * @param {string} providerId The configured OIDC provider ID.
 * @param {string|number} customerId The CRM customer ID.
 * @returns {Promise<unknown>} The customer response.
 */
export function getTeamsCustomer(providerId, customerId) {
  return request(
    `customers/${encodeURIComponent(customerId)}`,
    {},
    `?provider=${encodeURIComponent(providerId)}`,
  )
}

/**
 * Register Teams tab save handling and return a validity-state setter.
 * Saving validates the selected provider/customer on the server and configures
 * the tab URL only if the selection is unchanged.
 * @param {() => {provider?: object, customer?: object}} getSelection
 *   Callback returning the currently selected provider and customer.
 * @returns {Promise<(valid: boolean) => void>} Setter for Teams tab validity.
 */
export async function registerTeamsConfigHandler(getSelection) {
  await initializeTeams()
  const { pages } = await import('@microsoft/teams-js')
  pages.config.setValidityState(false)
  pages.config.registerOnSaveHandler(async (event) => {
    try {
      const { provider, customer } = getSelection() || {}
      if (!provider || !customer) throw new Error('Sign in and select a customer before saving.')
      await getTeamsCustomer(provider.id, customer.id)
      const current = getSelection()
      if (current?.provider?.id !== provider.id || current?.customer?.id !== customer.id) {
        throw new Error('The selection changed. Save the tab again.')
      }
      const contentUrl = `${window.location.origin}/teams/shared/customer/?provider=${encodeURIComponent(provider.id)}&customer=${encodeURIComponent(customer.id)}`
      await pages.config.setConfig({
        entityId: `gather-customer-${provider.id}-${customer.id}`,
        contentUrl,
        websiteUrl: `${window.location.origin}/#/contacts`,
        suggestedDisplayName: `Gather · ${customer.name}`,
      })
      event.notifySuccess()
    } catch (error) {
      event.notifyFailure(error.message || 'Unable to save this tab. Please try again.')
    }
  })
  return (valid) => pages.config.setValidityState(valid)
}

/** Read and URL-decode the browser's Django CSRF cookie. */
export function getCsrfToken() {
  return decodeURIComponent(
    document.cookie
      .split('; ')
      .find((c) => c.startsWith('csrftoken='))
      ?.slice(10) || '',
  )
}
