// Loads global styles and mounts the Preact application at the root element.
import { render } from 'preact'
import './index.css'
import { App } from './App.tsx'

const container = document.getElementById('app')
if (!container) throw new Error('Frontend root element not found.')
render(<App />, container)
