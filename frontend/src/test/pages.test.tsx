import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { health, overrides, reports, sources, stats, trends } from './fixtures'
import { mockApi } from './mockApi'
import { renderAt } from './render'

describe('Secondary views', () => {
  it('groups trends into new, rising, falling and gone', async () => {
    mockApi({ 'GET /api/health': health, 'GET /api/trends': trends })
    renderAt('/trends')
    expect(await screen.findByRole('heading', { level: 1, name: 'Trends' })).toBeInTheDocument()
    for (const name of ['New', 'Rising', 'Falling', 'Gone']) {
      expect(screen.getByRole('heading', { level: 2, name: new RegExp(`^${name}`) })).toBeInTheDocument()
    }
    const rows = screen.getAllByTestId('trend-row')
    expect(rows.map((r) => r.dataset.status)).toEqual(['new', 'rising', 'falling', 'gone'])
    expect(within(rows[1]).getByRole('link', { name: 'Vendor invoices re-keyed' })).toHaveAttribute('href', '/cards/c-invoices')
    expect(within(rows[1]).getByText('+0.16')).toBeInTheDocument()
    expect(within(rows[2]).getByText('−0.08')).toBeInTheDocument()
    expect(within(rows[3]).queryByRole('link')).toBeNull()
  })

  it('explains trends when there is only one run', async () => {
    mockApi({ 'GET /api/health': health, 'GET /api/trends': { run_at: '2026-11-26T00:00:00Z', previous_run_at: null, items: [] } })
    renderAt('/trends')
    expect(await screen.findByText(/only one clustering run so far/)).toBeInTheDocument()
  })

  it('lists overrides and deletes one', async () => {
    const api = mockApi({
      'GET /api/health': health,
      'GET /api/overrides': overrides,
      'DELETE /api/overrides/:id': { status: 204 },
    })
    const { user } = renderAt('/overrides')
    const rows = await screen.findAllByTestId('override-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('Merge')).toBeInTheDocument()
    expect(within(rows[0]).getByText('Keeps 3 signals together in one card')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Moves 1 signal into a card of its own')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Delete override 1' }))
    await waitFor(() => expect(screen.getAllByTestId('override-row')).toHaveLength(1))
    expect(api.writes()[0]).toMatchObject({ method: 'DELETE', path: '/api/overrides/1' })
    expect(screen.getByRole('status')).toHaveTextContent('Override deleted')
  })

  it('shows allowed and blocked sources with reasons', async () => {
    mockApi({ 'GET /api/health': health, 'GET /api/sources': sources })
    renderAt('/sources')
    const rows = await screen.findAllByTestId('source-row')
    expect(rows.map((r) => r.dataset.allowed)).toEqual(['true', 'false'])
    expect(within(rows[0]).getByText('Allowed')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Blocked')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Needs Data API approval')).toBeInTheDocument()
    expect(screen.getByText('1 source allowed, 1 blocked.')).toBeInTheDocument()
  })

  it('shows coverage per source and the top query arms', async () => {
    mockApi({ 'GET /api/health': health, 'GET /api/stats': stats })
    renderAt('/coverage')
    expect(await screen.findByRole('table', { name: 'Per source' })).toBeInTheDocument()
    expect(screen.getAllByTestId('coverage-source-row')).toHaveLength(2)
    const arms = screen.getByRole('table', { name: 'Top query arms' })
    const arm = within(arms).getByTestId('arm-row')
    expect(within(arm).getByText('re-key invoices')).toBeInTheDocument()
    expect(within(arm).getByText('50%')).toBeInTheDocument()
  })

  it('lists reports with links that open the files', async () => {
    mockApi({ 'GET /api/health': health, 'GET /api/reports': reports })
    renderAt('/reports')
    const rows = await screen.findAllByTestId('report-row')
    expect(rows).toHaveLength(2)
    const link = within(rows[0]).getByRole('link', { name: 'demo-pain-report.html' })
    expect(link).toHaveAttribute('href', '/api/reports/demo-pain-report.html')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('contains a render failure to the view and keeps navigation', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    // /api/sources must be a list; an object makes the view throw while rendering.
    mockApi({ 'GET /api/health': health, 'GET /api/sources': { not: 'a list' } })
    renderAt('/sources')
    expect(await screen.findByRole('heading', { level: 1, name: 'This view could not be shown' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument()
  })

  it('shows a not-found page and an unreachable API', async () => {
    mockApi({ 'GET /api/health': { status: 500, body: { detail: 'boom' } } })
    renderAt('/nowhere')
    expect(await screen.findByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument()
    expect(await screen.findByText('API unreachable')).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Main' })
    for (const name of ['Pain cards', 'Trends', 'Overrides', 'Sources', 'Coverage', 'Reports']) {
      expect(within(nav).getByRole('link', { name })).toBeInTheDocument()
    }
  })
})
