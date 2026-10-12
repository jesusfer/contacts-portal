import { safeExternalUrl } from '../utils/data.js'
export function GraphLink({ url, children }) {
  const href = safeExternalUrl(url)
  return href ? <a class="link" href={href} target="_blank" rel="noopener noreferrer">{children} ↗</a> : null
}
