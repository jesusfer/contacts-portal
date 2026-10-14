// Renders a page title, optional eyebrow, subtitle, and actions.
import type { ComponentChildren } from 'preact'

interface PageHeadingProps {
  eyebrow?: string
  title: string
  subtitle: string
  children?: ComponentChildren
}

export function PageHeading({
  eyebrow = 'Your workspace',
  title,
  subtitle,
  children,
}: PageHeadingProps) {
  return (
    <div class="page-heading">
      <div>
        <div class="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p class="muted">{subtitle}</p>
      </div>
      {children}
    </div>
  )
}
