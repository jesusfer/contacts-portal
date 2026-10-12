let initialization

export function initializeTeams() {
  if (!initialization) {
    initialization = (async () => {
      const { app } = await import('@microsoft/teams-js')
      let timer
      try {
        await Promise.race([
          app.initialize(),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Open this page in Microsoft Teams to continue.')), 5000) }),
        ])
        return app
      } finally {
        clearTimeout(timer)
      }
    })().catch(error => { initialization = null; throw error })
  }
  return initialization
}

export async function getTeamsAuthToken() {
  await initializeTeams()
  const { authentication } = await import('@microsoft/teams-js')
  return authentication.getAuthToken()
}

async function request(path, options = {}, query = '') {
  const response = await fetch(`/api/teams/${path}/${query}`, { credentials: 'same-origin', ...options })
  let data
  try { data = await response.json() } catch { throw new Error('Teams sign-in returned an unexpected response. Please try again.') }
  if (!response.ok) throw new Error(data.error || `Teams request failed (${response.status}).`)
  return data
}

export function exchangeTeamsToken(providerId, token, { graphOnly = false } = {}) {
  return request(graphOnly ? 'identity' : 'sso', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ provider_id: providerId, token }),
  })
}

export function getTeamsConfig() { return request('config') }

export async function getTeamsTabConfig() {
  await initializeTeams()
  const { pages } = await import('@microsoft/teams-js')
  return pages.getConfig()
}

export function getTeamsCustomers(providerId) {
  return request('customers', {}, `?provider=${encodeURIComponent(providerId)}`)
}

export function getTeamsCustomer(providerId, customerId) {
  return request(`customers/${encodeURIComponent(customerId)}`, {}, `?provider=${encodeURIComponent(providerId)}`)
}

export async function registerTeamsConfigHandler(getSelection) {
  await initializeTeams()
  const { pages } = await import('@microsoft/teams-js')
  pages.config.setValidityState(false)
  pages.config.registerOnSaveHandler(async event => {
    try {
      const { provider, customer } = getSelection() || {}
      if (!provider || !customer) throw new Error('Sign in and select a customer before saving.')
      await getTeamsCustomer(provider.id, customer.id)
      const current = getSelection()
      if (current?.provider?.id !== provider.id || current?.customer?.id !== customer.id) {
        throw new Error('The selection changed. Save the tab again.')
      }
      const contentUrl = `${window.location.origin}/teams/customer/?provider=${encodeURIComponent(provider.id)}&customer=${encodeURIComponent(customer.id)}`
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
  return valid => pages.config.setValidityState(valid)
}

export function getCsrfToken() {
  return decodeURIComponent(document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.slice(10) || '')
}
