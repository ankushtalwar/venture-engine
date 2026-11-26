import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ClusterDetail } from '../api/types'
import { AUTHOR_HASH, detail, health, summaries } from './fixtures'
import { mockApi, type MockRequest } from './mockApi'
import { renderAt } from './render'

function setup(d: ClusterDetail = detail(), extra: Record<string, unknown> = {}) {
  return mockApi({
    'GET /api/health': health,
    'GET /api/clusters/:id': (_req: MockRequest, p: Record<string, string>) =>
      p.id === d.id ? d : { status: 404, body: { detail: 'Cluster not found' } },
    ...extra,
  })
}

describe('Card detail', () => {
  it('labels model-written text as such and shows skeptic points', async () => {
    setup()
    renderAt('/cards/c-invoices')
    const card = await screen.findByTestId('card-detail')
    expect(card).toHaveAttribute('data-cluster-id', 'c-invoices')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Small accounting practices')

    const model = screen.getByTestId('model-written')
    expect(within(model).getByRole('heading', { name: 'Model-written summary' })).toBeInTheDocument()
    expect(model).toHaveTextContent('Written by the model')
    expect(within(model).getByText(/Hypothesis: the people describing this/)).toBeInTheDocument()

    for (const name of ['Skeptic', 'Evidence', 'Money', 'Signals']) {
      expect(screen.getByRole('heading', { level: 2, name })).toBeInTheDocument()
    }
    expect(screen.getByText('Existing tools may already solve this well for most firms.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Signal s1aaaa' })).toHaveAttribute('href', '#signal-s1aaaaaaaaaa')
  })

  it('shows all four evidence statuses as distinct badges', async () => {
    setup()
    renderAt('/cards/c-invoices')
    await screen.findByTestId('card-detail')
    const statuses = new Set(screen.getAllByTestId('evidence-status').map((b) => b.getAttribute('data-status')))
    expect(statuses).toEqual(new Set(['observed', 'inferred', 'derived', 'unknown']))
    const classes = new Set(screen.getAllByTestId('evidence-status').map((b) => b.className))
    expect(classes.size).toBe(4)
    const inferred = screen.getAllByTestId('evidence-status').find((b) => b.dataset.status === 'inferred')!
    expect(inferred).toHaveTextContent('Inferred')
    expect(inferred).toHaveAttribute('title', expect.stringMatching(/hypothesis/i))
  })

  it('renders verified quotes with safe source links, money and vendor prices', async () => {
    setup()
    renderAt('/cards/c-invoices')
    await screen.findByTestId('card-detail')
    const quotes = screen.getAllByTestId('quote')
    expect(quotes.length).toBeGreaterThan(0)
    const link = within(quotes[0]).getByRole('link', { name: 'demo-forum' })
    expect(link).toHaveAttribute('href', 'https://example.com/forum/a1')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(within(quotes[0]).getByText('12 Sept 2026')).toBeInTheDocument()

    const money = screen.getByRole('heading', { level: 2, name: 'Money' }).closest('section')!
    expect(within(money).getByText('$1,850')).toBeInTheDocument()
    expect(within(money).getByText('$22/hour')).toBeInTheDocument()
    expect(within(money).getByRole('rowheader', { name: 'ExampleBooks' })).toBeInTheDocument()
  })

  it('renders source text as text, never HTML, and drops non-http links', async () => {
    setup()
    const { container } = renderAt('/cards/c-invoices')
    await screen.findByTestId('card-detail')
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getAllByText(/<img src=x onerror=/).length).toBeGreaterThan(0)
    expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined()
    const row = screen.getAllByTestId('signal-row').find((r) => r.dataset.signalId === 's3cccccccccc')!
    expect(within(row).queryByRole('link', { name: 'Blog post' })).toBeNull()
    expect(within(row).getByText('Blog post')).toBeInTheDocument()
    for (const a of container.querySelectorAll('a[target="_blank"]')) {
      expect(a.getAttribute('rel')).toBe('noopener noreferrer')
    }
  })

  it('never shows author hashes', async () => {
    setup()
    const { container } = renderAt('/cards/c-invoices')
    await screen.findByTestId('card-detail')
    for (const details of container.querySelectorAll('details')) details.open = true
    expect(container.innerHTML).not.toContain(AUTHOR_HASH)
  })

  it('accepts, rejects and clears the card label', async () => {
    const d = detail()
    const api = setup(d, {
      'POST /api/clusters/:id/label': (req: MockRequest) => ({ ...summaries[0], label: (req.body as { label: string | null }).label }),
    })
    const { user } = renderAt('/cards/c-invoices')
    await screen.findByTestId('card-detail')
    const label = () => within(screen.getByRole('complementary')).getByTestId('card-label')
    expect(label()).toHaveTextContent('Unlabelled')
    expect(screen.getByRole('button', { name: 'Clear label' })).toBeDisabled()

    await user.type(screen.getByLabelText('Note (optional)'), 'Clear spend evidence')
    await user.click(screen.getByRole('button', { name: 'Accept' }))
    await waitFor(() => expect(label()).toHaveTextContent('Accepted'))
    expect(screen.getByRole('status')).toHaveTextContent('Card accepted')
    expect(api.writes()[0]).toMatchObject({
      path: '/api/clusters/c-invoices/label',
      body: { label: 'accept', note: 'Clear spend evidence' },
    })
    expect(screen.getByRole('button', { name: 'Accept' })).toHaveAttribute('aria-pressed', 'true')

    await user.click(screen.getByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(label()).toHaveTextContent('Rejected'))
    expect(screen.getByRole('status')).toHaveTextContent('Card rejected')

    await user.click(screen.getByRole('button', { name: 'Clear label' }))
    await waitFor(() => expect(label()).toHaveTextContent('Unlabelled'))
    expect(api.writes()[2].body).toEqual({ label: null })
    expect(screen.getByRole('status')).toHaveTextContent('Label cleared')
  })

  it('labels a signal and rolls back when the API fails', async () => {
    let fail = false
    const api = setup(detail(), {
      'POST /api/signals/:id/label': (req: MockRequest, p: Record<string, string>) =>
        fail ? { status: 500, body: { detail: 'Database is locked' } } : { id: p.id, label: (req.body as { label: string }).label },
    })
    const { user } = renderAt('/cards/c-invoices')
    await screen.findByTestId('card-detail')
    const row = screen.getAllByTestId('signal-row')[0]
    const group = within(row).getByRole('radiogroup', { name: 'Signal label' })
    expect(within(group).getByRole('radio', { name: 'None' })).toBeChecked()

    await user.click(within(group).getByRole('radio', { name: 'Invalid' }))
    await waitFor(() => expect(within(group).getByRole('radio', { name: 'Invalid' })).toBeChecked())
    expect(api.writes()[0]).toMatchObject({ path: '/api/signals/s1aaaaaaaaaa/label', body: { label: 'invalid' } })
    expect(screen.getByRole('status')).toHaveTextContent('Signal labelled invalid')

    fail = true
    await user.click(within(group).getByRole('radio', { name: 'Valid' }))
    expect(await within(row).findByRole('alert')).toHaveTextContent('Database is locked')
    expect(within(group).getByRole('radio', { name: 'Invalid' })).toBeChecked()
  })

  it('splits selected signals into a new card', async () => {
    const d = detail()
    const first = { ...summaries[0], id: 'c-left', title: 'Left card' }
    const second = { ...summaries[0], id: 'c-right', title: 'Right card' }
    const api = mockApi({
      'GET /api/health': health,
      'GET /api/clusters/:id': (_req: MockRequest, p: Record<string, string>) =>
        p.id === 'c-left' ? detail({ id: 'c-left', title: 'Left card', synthesis: null }) : d,
      'POST /api/clusters/:id/split': { clusters: [first, second] },
    })
    const { user, location } = renderAt('/cards/c-invoices')
    await screen.findByTestId('card-detail')
    const split = screen.getByRole('button', { name: 'Split selected signals' })
    expect(split).toBeDisabled()

    const rows = screen.getAllByTestId('signal-row')
    for (const row of rows) await user.click(within(row).getByRole('checkbox', { name: 'Select signal for split' }))
    expect(split).toBeDisabled() // cannot move every signal out
    await user.click(within(rows[0]).getByRole('checkbox', { name: 'Select signal for split' }))
    expect(split).toBeEnabled()
    await user.click(split)

    await waitFor(() => expect(location()).toBe('/cards/c-left'))
    expect(api.writes()[0]).toMatchObject({
      path: '/api/clusters/c-invoices/split',
      body: { signal_ids: ['s2bbbbbbbbbb', 's3cccccccccc'] },
    })
    expect(await screen.findByRole('heading', { level: 1, name: 'Left card' })).toBeInTheDocument()
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('Card split into 2')
    expect(within(status).getByRole('link', { name: 'Open the other card' })).toHaveAttribute('href', '/cards/c-right')
    expect(screen.getByTestId('model-written')).toHaveTextContent('No summary yet')
    // Per-card state must not leak from the card that was split.
    expect(screen.getByRole('button', { name: 'Split selected signals' })).toBeDisabled()
    for (const box of screen.getAllByRole('checkbox', { name: 'Select signal for split' })) expect(box).not.toBeChecked()
  })

  it('re-synthesizes the card', async () => {
    const d = detail({ synthesis: null })
    const api = setup(d, {
      'POST /api/clusters/:id/synthesize': detail({
        synthesis: { title: 'Fresh title', summary: 'A new summary.', hypotheses: ['Hypothesis: new.'] },
        title: 'Fresh title',
      }),
    })
    const { user } = renderAt('/cards/c-invoices')
    await screen.findByTestId('card-detail')
    expect(screen.getByTestId('model-written')).toHaveTextContent('No summary yet')
    await user.click(screen.getByRole('button', { name: 'Re-synthesize' }))
    expect(await screen.findByText('A new summary.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Fresh title' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Synthesis updated')
    expect(api.writes()[0].path).toBe('/api/clusters/c-invoices/synthesize')
  })

  it('explains a missing card', async () => {
    setup()
    renderAt('/cards/nope')
    expect(await screen.findByRole('heading', { level: 1, name: 'Card not found' })).toBeInTheDocument()
    const main = screen.getByRole('main')
    expect(within(main).getByRole('link', { name: 'Pain cards' })).toHaveAttribute('href', '/cards')
  })
})
