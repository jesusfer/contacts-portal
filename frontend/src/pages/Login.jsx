import { useState } from 'preact/hooks'
import { api } from '../utils/data.js'
import { ErrorNotice } from '../components/ErrorNotice.jsx'
import { Icon } from '../components/Icon.jsx'

export function Login({ session, onLogin }) {
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
