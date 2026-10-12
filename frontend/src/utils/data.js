import { useEffect, useState } from 'preact/hooks'
import * as TeamsGraph from '../teams-graph.js'

export const statuses = ['scheduled', 'completed', 'missed', 'rescheduled', 'cancelled']

export function initials(name = '') { return name.split(/\s+/).slice(0, 2).map(s => s[0]).join('').toUpperCase() || '?' }
export function date(value, options = {}) { return value ? new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...options }) : 'Not scheduled' }
export function time(value) { return value ? new Date(value).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : '—' }
export function dateTime(value) { return value ? `${date(value, { year: 'numeric' })} · ${time(value)}` : 'Not scheduled' }
export function localInput(value) {
  if (!value) return ''
  const d = new Date(value)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
function csrf() {
  return decodeURIComponent(document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.slice(10) || '')
}
export async function api(path, options = {}) {
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
export function useResource(path, version = 0) {
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

export function safeExternalUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null
  } catch { return null }
}

export function appointmentPayload(starts, ends, notes, original = null) {
  const unchangedStart = original && starts === localInput(original.starts_at)
  const unchangedEnd = original && ends === localInput(original.ends_at)
  const start = new Date(unchangedStart ? original.starts_at : starts)
  const end = new Date(unchangedEnd ? original.ends_at : ends)
  if (!starts || !ends || !Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || (!(unchangedStart && unchangedEnd) && end <= start)) throw new Error('Choose an end time after the start time.')
  return { starts_at: unchangedStart ? original.starts_at : start.toISOString(), ends_at: unchangedEnd ? original.ends_at : end.toISOString(), notes }
}
