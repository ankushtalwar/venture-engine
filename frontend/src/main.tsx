import '@fontsource-variable/instrument-sans/wdth.css'
import '@fontsource-variable/instrument-sans/wdth-italic.css'
import '@fontsource-variable/source-serif-4/opsz.css'
import './styles/app.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { App } from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
