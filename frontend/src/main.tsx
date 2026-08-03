import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { hydratePreferences, startPreferenceSync } from './lib/preferences'

// Pull server-stored preferences into the cache BEFORE the app (and its stores)
// load, so every consumer reads consistent values on first paint.  App is
// imported dynamically for the same reason — its module graph (incl. zustand
// stores that read getPref at init) must evaluate after hydration.
hydratePreferences().finally(async () => {
  startPreferenceSync()
  const { default: App } = await import('./App')
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
