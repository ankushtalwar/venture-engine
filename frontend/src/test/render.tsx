import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router'
import { App } from '../App'

let currentPath = ''

function LocationSpy() {
  const loc = useLocation()
  currentPath = `${loc.pathname}${loc.search}`
  return null
}

export function renderAt(path: string) {
  const user = userEvent.setup()
  const utils = render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      <LocationSpy />
    </MemoryRouter>,
  )
  return { user, ...utils, location: () => currentPath }
}
