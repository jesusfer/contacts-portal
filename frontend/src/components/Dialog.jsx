import { useEffect, useRef } from 'preact/hooks'
import { Icon } from './Icon.jsx'
export function Dialog({ title, close, children }) {
  const ref = useRef()
  useEffect(() => {
    const previous = document.activeElement
    const bodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    ref.current?.querySelector('input, select, button')?.focus()
    const handler = e => {
      if (e.key === 'Escape') close()
      if (e.key === 'Tab') {
        const items = [...ref.current.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)')]
        const first = items[0], last = items.at(-1)
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
        if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
      }
    }
    document.addEventListener('keydown', handler)
    return () => { document.body.style.overflow = bodyOverflow; document.removeEventListener('keydown', handler); previous?.focus() }
  }, [])
  return <div class="dialog-backdrop" onClick={e => e.target === e.currentTarget && close()}><section ref={ref} class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><div class="panel-heading"><h2 id="dialog-title">{title}</h2><button class="close" aria-label="Close dialog" onClick={close}><Icon name="close"/></button></div><div class="panel-body">{children}</div></section></div>
}
