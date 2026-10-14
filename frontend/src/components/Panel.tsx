import type { ComponentChildren } from 'preact'

interface PanelProps {
  title: string
  count?: number
  action?: ComponentChildren
  children?: ComponentChildren
}

export function Panel({ title, count, action, children }: PanelProps) {
  return (
    <section class="panel">
      <div class="panel-heading">
        <div class="panel-title">
          <h2>{title}</h2>
          {count != null && <span class="counter">{count}</span>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}
