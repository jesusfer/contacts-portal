import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from '../lib/data.js'

export function useGraphPages(path, field, version) {
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
