import { Navigate, Route, Routes } from 'react-router'
import { Layout } from './components/Layout'
import { TokenProvider } from './components/TokenDialog'
import { NotifyProvider } from './lib/notify'
import { CardDetailPage } from './pages/CardDetailPage'
import { CardsPage } from './pages/CardsPage'
import { CoveragePage } from './pages/CoveragePage'
import { NotFound } from './pages/NotFound'
import { OverridesPage } from './pages/OverridesPage'
import { ReportsPage } from './pages/ReportsPage'
import { SourcesPage } from './pages/SourcesPage'
import { TrendsPage } from './pages/TrendsPage'

/** Routes only; the router itself is supplied by main.tsx (browser) or the tests (memory). */
export function App() {
  return (
    <TokenProvider>
      <NotifyProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Navigate to="/cards" replace />} />
            <Route path="cards" element={<CardsPage />} />
            <Route path="cards/:id" element={<CardDetailPage />} />
            <Route path="trends" element={<TrendsPage />} />
            <Route path="overrides" element={<OverridesPage />} />
            <Route path="sources" element={<SourcesPage />} />
            <Route path="coverage" element={<CoveragePage />} />
            <Route path="reports" element={<ReportsPage />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </NotifyProvider>
    </TokenProvider>
  )
}
