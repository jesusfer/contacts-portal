import { useEffect, useState } from 'preact/hooks'
import { api } from '../lib/data.js'
export function GraphPresenceIndicator({ user }) {
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
