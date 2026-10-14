import { useEffect, useRef, useState } from 'preact/hooks'
import * as TeamsSDK from '../teams.ts'
import { ErrorNotice } from '../components/ErrorNotice.tsx'
import { Loading } from '../components/Loading.tsx'
import { initialProvider, ProviderSelect } from '../teams/selection.tsx'

export function TeamsConfig() {
  const [providers, setProviders] = useState([]),
    [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true),
    [error, setError] = useState(''),
    [attempt, setAttempt] = useState(0)
  const [signingIn, setSigningIn] = useState(false)
  const [customers, setCustomers] = useState([]),
    [customerId, setCustomerId] = useState('')
  const [authenticatedProvider, setAuthenticatedProvider] = useState('')
  const selectionRef = useRef(null),
    validity = useRef(null)
  selectionRef.current =
    !loading && !signingIn && !error && authenticatedProvider === selected
      ? {
          provider: providers.find((provider) => String(provider.id) === selected),
          customer: customers.find((customer) => String(customer.id) === customerId),
        }
      : null
  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    setSelected('')
    setCustomers([])
    setAuthenticatedProvider('')
    async function configure() {
      try {
        const config = await TeamsSDK.getTeamsConfig()
        await TeamsSDK.initializeTeams()
        if (!active) return
        const setValidity = await TeamsSDK.registerTeamsConfigHandler(() => selectionRef.current)
        if (!active) {
          setValidity(false)
          return
        }
        validity.current = setValidity
        const choices = config.providers || []
        setProviders(choices)
        const saved = await TeamsSDK.getTeamsTabConfig()
        if (!active) return
        const savedUrl = saved.contentUrl ? new URL(saved.contentUrl) : null
        const savedProvider = savedUrl?.searchParams.get('provider')
        setSelected(
          savedProvider && choices.some((provider) => String(provider.id) === savedProvider)
            ? savedProvider
            : initialProvider(choices),
        )
        setCustomerId(savedUrl?.searchParams.get('customer') || '')
        if (!choices.length)
          throw new Error('No Teams organizations are configured. Contact your administrator.')
      } catch (error) {
        if (active) setError(error.message || 'Unable to configure this tab.')
      } finally {
        if (active) setLoading(false)
      }
    }
    configure()
    return () => {
      active = false
      validity.current?.(false)
      validity.current = null
      selectionRef.current = null
    }
  }, [attempt])
  useEffect(() => {
    let active = true
    setCustomers([])
    setAuthenticatedProvider('')
    validity.current?.(false)
    if (!selected) {
      setSigningIn(false)
      return () => {
        active = false
      }
    }
    setSigningIn(true)
    setError('')
    async function signIn() {
      try {
        const token = await TeamsSDK.getTeamsAuthToken()
        if (!active) return
        const session = await TeamsSDK.exchangeTeamsToken(selected, token)
        if (!active) return
        if (!session.user || !session.agent)
          throw new Error(
            'Your CRM account must be linked and provisioned with an agent. Open browser sign-in or contact your administrator.',
          )
        const data = await TeamsSDK.getTeamsCustomers(selected)
        if (!active) return
        setCustomers(data.customers)
        setAuthenticatedProvider(selected)
      } catch (error) {
        if (active) setError(error.message)
      } finally {
        if (active) setSigningIn(false)
      }
    }
    signIn()
    return () => {
      active = false
    }
  }, [selected])
  useEffect(() => {
    validity.current?.(!!selectionRef.current?.provider && !!selectionRef.current?.customer)
  }, [loading, signingIn, error, selected, providers, customers, customerId, authenticatedProvider])
  return (
    <div class="teams-page">
      <div class="teams-container">
        <h2>Gather customer tab</h2>
        <p class="muted">
          Sign in with Teams, choose a CRM customer, then click Save. Calls are private to the
          signed-in agent.
        </p>
        <ProviderSelect
          providers={providers}
          selected={selected}
          disabled={loading || signingIn}
          onChange={(value) => {
            validity.current?.(false)
            selectionRef.current = null
            setCustomerId('')
            setSelected(value)
          }}
        />
        {loading || signingIn ? (
          <Loading />
        ) : (
          <>
            <ErrorNotice error={error} retry={() => setAttempt((n) => n + 1)} />
            {authenticatedProvider === selected && selected && !error && (
              <label class="teams-provider">
                Customer
                <select
                  value={customerId}
                  onChange={(event) => setCustomerId(event.currentTarget.value)}
                >
                  <option value="">Select a customer</option>
                  {customers.map((customer) => (
                    <option key={customer.id} value={String(customer.id)}>
                      {customer.name}
                    </option>
                  ))}
                </select>
                {!customers.length && <p class="muted">No customers are available in the CRM.</p>}
              </label>
            )}
            <a class="button secondary" href="/#/login" target="_blank" rel="noopener noreferrer">
              Open browser sign-in
            </a>
          </>
        )}
      </div>
    </div>
  )
}
