import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import Pulse from './Pulse'
import './styles.css'

// One bundle serves two windows: the command center and the small notch strip.
const isPulse = new URLSearchParams(window.location.search).has('pulse')

createRoot(document.getElementById('root')).render(
  <React.StrictMode>{isPulse ? <Pulse /> : <App />}</React.StrictMode>,
)
