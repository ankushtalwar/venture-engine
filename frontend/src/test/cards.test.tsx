import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { detail, health, summaries } from './fixtures'
import { mockApi } from './mockApi'
import { renderAt } from './render'

describe('Pain cards list', () => {
  it('redirects / to /cards and lists ranked cards with score, confidence, label and trend', async () => {
    mockApi({
      'GET /api/health': health,
      'GET /api/clusters': { items: summaries, total: summaries.length },
    })
    const { location } = renderAt('/')
    expect(await screen.findByRole('heading', { level: 1, name: 'Pain cards' })).toBeInTheDocument()
    expect(location()).toBe('/cards')

    const rows = await screen.findAllByTestId('card-row')
    expect(rows).toHaveLength(3)
    expect(rows.map((r) => r.getAttribute('data-cluster-id'))).toEqual(['c-invoices', 'c-moveout', 'c-ap'])

    const first = within(rows[0])
    expect(first.getByTestId('card-score')).toHaveTextContent('0.57')
    expect(first.getByTestId('card-confidence')).toHaveTextContent('Medium')
    expect(first.getByTestId('card-label')).toHaveTextContent('Unlabelled')
    expect(first.getByTestId('card-trend')).toHaveTextContent('Rising')
    expect(first.getByTestId('card-components')).toHaveAccessibleName(/Breadth 0\.58/)
    expect(first.getByRole('link', { name: summaries[0].title })).toHaveAttribute('href', '/cards/c-invoices')
    expect(first.getByText(/3 signals from 3 authors in forum and search snippet/)).toBeInTheDocument()

    expect(within(rows[1]).getByTestId('card-label')).toHaveTextContent('Accepted')
    expect(within(rows[2]).getByTestId('card-label')).toHaveTextContent('Rejected')
    expect(within(rows[2]).queryByTestId('card-trend')).toBeNull()
    expect(screen.getByText('Mock model')).toBeInTheDocument()
  })

  it('sends filters to the API and keeps them in the URL', async () => {
    const api = mockApi({
      'GET /api/health': health,
      'GET /api/clusters': (req: { query: URLSearchParams }) => {
        const conf = req.query.get('confidence')
        const items = conf ? summaries.filter((s) => s.confidence === conf) : summaries
        return { items, total: items.length }
      },
    })
    const { user, location } = renderAt('/cards')
    await screen.findAllByTestId('card-row')

    await user.selectOptions(screen.getByLabelText('Confidence'), 'Low')
    await waitFor(() => expect(screen.getAllByTestId('card-row')).toHaveLength(2))
    expect(location()).toBe('/cards?confidence=Low')

    await user.selectOptions(screen.getByLabelText('Label'), 'unlabelled')
    await waitFor(() => expect(location()).toBe('/cards?confidence=Low&label=unlabelled'))
    const last = api.calls.filter((c) => c.path === '/api/clusters').at(-1)!
    expect(last.query.get('confidence')).toBe('Low')
    expect(last.query.get('label')).toBe('unlabelled')
  })

  it('shows an empty state for filters that match nothing', async () => {
    mockApi({ 'GET /api/health': health, 'GET /api/clusters': { items: [], total: 0 } })
    renderAt('/cards?confidence=High')
    expect(await screen.findByText('No cards match these filters.')).toBeInTheDocument()
    expect(screen.getByLabelText('Confidence')).toHaveValue('High')
  })

  it('merges two selected cards and opens the merged card', async () => {
    const merged = detail({ id: 'c-merged', title: 'Merged card', synthesis: null })
    const api = mockApi({
      'GET /api/health': health,
      'GET /api/clusters': { items: summaries, total: 3 },
      'POST /api/clusters/merge': merged,
      'GET /api/clusters/:id': (_req: unknown, p: Record<string, string>) =>
        p.id === 'c-merged' ? merged : { status: 404, body: { detail: 'not found' } },
    })
    const { user, location } = renderAt('/cards')
    await screen.findAllByTestId('card-row')

    const merge = screen.getByRole('button', { name: 'Merge selected' })
    expect(merge).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: `Select ${summaries[0].title}` }))
    expect(merge).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: `Select ${summaries[2].title}` }))
    expect(merge).toBeEnabled()
    await user.click(merge)

    await waitFor(() => expect(location()).toBe('/cards/c-merged'))
    expect(api.writes()[0].body).toEqual({ ids: ['c-invoices', 'c-ap'] })
    expect(await screen.findByRole('heading', { level: 1, name: 'Merged card' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Cards merged')
  })

  it('shows the API error when a merge fails', async () => {
    mockApi({
      'GET /api/health': health,
      'GET /api/clusters': { items: summaries, total: 3 },
      'POST /api/clusters/merge': { status: 409, body: { detail: 'Clusters changed; reload and try again.' } },
    })
    const { user } = renderAt('/cards')
    await screen.findAllByTestId('card-row')
    await user.click(screen.getByRole('checkbox', { name: `Select ${summaries[0].title}` }))
    await user.click(screen.getByRole('checkbox', { name: `Select ${summaries[1].title}` }))
    await user.click(screen.getByRole('button', { name: 'Merge selected' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Clusters changed; reload and try again.')
  })

  it('loads more cards when there are more than one page', async () => {
    mockApi({
      'GET /api/health': health,
      'GET /api/clusters': (req: { query: URLSearchParams }) =>
        req.query.get('offset') === '0'
          ? { items: summaries.slice(0, 2), total: 3 }
          : { items: summaries.slice(2), total: 3 },
    })
    const { user } = renderAt('/cards')
    await screen.findAllByTestId('card-row')
    await user.click(screen.getByRole('button', { name: 'Show 1 more' }))
    await waitFor(() => expect(screen.getAllByTestId('card-row')).toHaveLength(3))
    expect(screen.queryByRole('button', { name: /more/ })).toBeNull()
  })
})
