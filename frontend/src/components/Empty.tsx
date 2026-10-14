import type { ComponentChildren } from 'preact'
import { Icon } from './Icon.tsx'

interface EmptyProps {
  title?: string
  children?: ComponentChildren
  icon?: string
}

export function Empty({ title = 'Nothing here yet', children, icon = 'contacts' }: EmptyProps) {
  return (
    <div class="empty">
      <Icon name={icon} size={27} />
      <strong>{title}</strong>
      <p>{children}</p>
    </div>
  )
}
