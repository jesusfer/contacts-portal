// Renders a validated external Microsoft Graph link.
import { safeExternalUrl } from '../utils/data.ts'
import type { ComponentChildren } from 'preact'

export function GraphLink({ url, children }: { url?: string | null; children: ComponentChildren }) {
  const href = safeExternalUrl(url)
  return href ? (
    <a class="link" href={href} target="_blank" rel="noopener noreferrer">
      {children} ↗
    </a>
  ) : null
}
