import { useEffect, useRef, useState } from 'preact/hooks'
import * as TeamsSDK from '../teams.js'
import * as TeamsGraph from '../teams-graph.js'
import { ErrorNotice } from '../components/ErrorNotice.jsx'
import { Loading } from '../components/Loading.jsx'
import { initialProvider, ProviderSelect } from '../teams/selection.jsx'
import { WorkspaceApp } from '../App.jsx'
import { TeamsGraphWorkspace } from './TeamsGraphWorkspace.jsx'

export function TeamsTab() {
  const [providers, setProviders] = useState([]), [selected, setSelected] = useState('')
  const [session, setSession] = useState(null), [error, setError] = useState(''), [loading, setLoading] = useState(true), [attempt, setAttempt] = useState(0)
  const [graphUser, setGraphUser] = useState(null), [opening, setOpening] = useState(false)
  const identity = useRef(null), lifetime = useRef(null)
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    lifetime.current = controller
    TeamsGraph.clearTeamsGraph()
    identity.current = null
    setSession(null); setGraphUser(null); setError(''); setLoading(true)
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
        const graphOnly = config.graph_only !== false
        const value = await TeamsSDK.exchangeTeamsToken(providerId, token, { graphOnly })
        if (!active) return
        const verified = graphOnly ? value : value.teams_identity
        if (!verified?.id || !verified.tenant_id) throw new Error('Teams sign-in did not verify an identity. Please try again.')
        identity.current = verified
        await TeamsGraph.startTeamsGraph({ ...choices.find(provider => String(provider.id) === providerId), tenant_id: verified.tenant_id }, verified.id, controller.signal)
        if (!active) return
        if (graphOnly) {
          const profile = await TeamsGraph.teamsGraphRequest('graph/profile/', { signal: controller.signal })
          if (active) setGraphUser({ ...profile, name: profile.display_name })
        } else {
          if (!value.user) throw new Error('Teams sign-in did not create a session. Please try again.')
          history.replaceState(null, '', `${location.pathname}?provider=${encodeURIComponent(providerId)}#/dashboard`)
          setSession(value)
        }
      } catch (error) {
        if (active) setError(`Unable to sign in with Teams. ${error.message}`)
      } finally {
        if (active) setLoading(false)
      }
    }
    signIn()
    return () => { active = false; controller.abort(); TeamsGraph.clearTeamsGraph() }
  }, [attempt])
  async function openDashboard() {
    setOpening(true); setError('')
    try {
      const token = await TeamsSDK.getTeamsAuthToken()
      const verified = await TeamsSDK.exchangeTeamsToken(selected, token, { graphOnly: true })
      if (verified.id !== identity.current?.id || verified.tenant_id !== identity.current?.tenant_id) throw new Error('Teams identity changed. Reload this tab to sign in again.')
      const value = await TeamsSDK.exchangeTeamsToken(selected, token)
      lifetime.current.signal.throwIfAborted()
      if (!value.user) throw new Error('Teams sign-in did not create a session. Please try again.')
      history.replaceState(null, '', `${location.pathname}?provider=${encodeURIComponent(selected)}#/dashboard`)
      setSession(value)
    } catch (error) {
      if (!lifetime.current.signal.aborted) setError(`Unable to open the full dashboard. ${error.message}`)
    } finally {
      if (!lifetime.current.signal.aborted) setOpening(false)
    }
  }
  if (session) return <WorkspaceApp initialSession={session} embedded onSessionEnd={() => { TeamsGraph.clearTeamsGraph(); setSession(null); setGraphUser(null); setError('Your Teams session has ended. Sign in again to continue.') }}/>
  if (graphUser) return <TeamsGraphWorkspace user={graphUser} error={error} opening={opening} openDashboard={openDashboard}/>
  return <div class="teams-page"><div class="teams-container"><h2>Gather for Teams</h2>{loading ? <><Loading/><p class="muted">Signing you in with Teams…</p></> : <><ErrorNotice error={error} retry={() => setAttempt(n => n + 1)}/>{!error && <><ProviderSelect providers={providers} selected={selected} onChange={setSelected}/><button class="button" disabled={!selected} onClick={() => { history.replaceState(null, '', `${location.pathname}?provider=${encodeURIComponent(selected)}${location.hash}`); setAttempt(n => n + 1) }}>Continue with Teams</button></>}<div class="actions"><a class="button secondary" href="/#/login" target="_blank" rel="noopener noreferrer">Open browser sign-in</a></div></>}</div></div>
}
