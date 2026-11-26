import { NavLink, Outlet, useLocation } from 'react-router'
import { api } from '../api/client'
import { useApi } from '../lib/useApi'
import { ErrorBoundary } from './ErrorBoundary'

const NAV = [
  { to: '/cards', label: 'Pain cards' },
  { to: '/trends', label: 'Trends' },
  { to: '/overrides', label: 'Overrides' },
  { to: '/sources', label: 'Sources' },
  { to: '/coverage', label: 'Coverage' },
  { to: '/reports', label: 'Reports' },
]

function HealthChips() {
  const { data, error } = useApi(() => api.health(), 'health')
  if (error) {
    return (
      <p className="health health-down" data-testid="health">
        API unreachable
      </p>
    )
  }
  if (!data) return null
  return (
    <p className="health" data-testid="health">
      <span className="chip">{data.store === 'postgres' ? 'Postgres' : 'SQLite'}</span>
      {data.llm_mock ? (
        <span className="chip chip-mock" title="No API key: summaries and hypotheses come from the offline mock model">
          Mock model
        </span>
      ) : (
        <span className="chip">Claude</span>
      )}
    </p>
  )
}

export function Layout() {
  const { pathname } = useLocation()
  return (
    <div className="shell">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="masthead">
        <div className="masthead-inner">
          <NavLink to="/cards" className="wordmark">
            Pain review
          </NavLink>
          <nav aria-label="Main" className="nav">
            <ul>
              {NAV.map((item) => (
                <li key={item.to}>
                  <NavLink to={item.to}>{item.label}</NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <HealthChips />
        </div>
      </header>
      <main id="main" tabIndex={-1}>
        <ErrorBoundary resetKey={pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  )
}
