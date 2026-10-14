// Renders an accessible modal dialog and manages focus and dismissal.
import { useEffect, useRef } from 'preact/hooks'
import type { ComponentChildren } from 'preact'
import { Icon } from './Icon.tsx'

interface DialogProps {
  title: string
  close: () => void
  children?: ComponentChildren
}

export function Dialog({ title, close, children }: DialogProps) {
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const bodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    ref.current?.querySelector<HTMLElement>('input, select, button')?.focus()
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
      if (e.key === 'Tab') {
        const items = [
          ...ref.current.querySelectorAll<HTMLElement>(
            'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)',
          ),
        ]
        const first = items[0],
          last = items.at(-1)
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault()
          last?.focus()
        }
        if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault()
          first?.focus()
        }
      }
    }
    document.addEventListener('keydown', handler)
    return () => {
      document.body.style.overflow = bodyOverflow
      document.removeEventListener('keydown', handler)
      previous?.focus()
    }
  }, [])
  return (
    <div class="dialog-backdrop" onClick={(e) => e.target === e.currentTarget && close()}>
      <section
        ref={ref}
        class="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
      >
        <div class="panel-heading">
          <h2 id="dialog-title">{title}</h2>
          <button class="close" aria-label="Close dialog" onClick={close}>
            <Icon name="close" />
          </button>
        </div>
        <div class="panel-body">{children}</div>
      </section>
    </div>
  )
}
