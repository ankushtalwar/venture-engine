import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TOKEN_KEY } from '../api/client'
import { detail, health, summaries } from './fixtures'
import { mockApi, type MockRequest } from './mockApi'
import { renderAt } from './render'

describe('API token prompt', () => {
  it('asks for a token on 401, stores it for the tab and retries with a Bearer header', async () => {
    const d = detail()
    const api = mockApi({
      'GET /api/health': health,
      'GET /api/clusters/:id': d,
      'POST /api/clusters/:id/label': (req: MockRequest) =>
        req.headers.Authorization === 'Bearer s3cret'
          ? { ...summaries[0], label: 'accept' }
          : { status: 401, body: { detail: 'Missing or invalid token' } },
    })
    const { user } = renderAt('/cards/c-invoices')
    await screen.findByTestId('card-detail')
    await user.click(screen.getByRole('button', { name: 'Accept' }))

    const dialog = await screen.findByRole('dialog', { name: 'API token' })
    expect(within(dialog).getByRole('button', { name: 'Save token' })).toBeDisabled()
    await user.type(within(dialog).getByLabelText('API token'), 's3cret')
    await user.click(within(dialog).getByRole('button', { name: 'Save token' }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Card accepted'))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('s3cret')
    const writes = api.writes()
    expect(writes).toHaveLength(2)
    expect(writes[0].headers.Authorization).toBeUndefined()
    expect(writes[1].headers.Authorization).toBe('Bearer s3cret')
  })

  it('shows the API error when the person cancels the prompt', async () => {
    mockApi({
      'GET /api/health': health,
      'GET /api/clusters/:id': detail(),
      'POST /api/clusters/:id/label': { status: 401, body: { detail: 'Missing or invalid token' } },
    })
    const { user } = renderAt('/cards/c-invoices')
    await screen.findByTestId('card-detail')
    await user.click(screen.getByRole('button', { name: 'Reject' }))
    const dialog = await screen.findByRole('dialog', { name: 'API token' })
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This action needs an API token')
  })

  it('sends a saved token on every request and says when it is rejected', async () => {
    sessionStorage.setItem(TOKEN_KEY, 'old')
    mockApi({
      'GET /api/health': health,
      'GET /api/clusters/:id': detail(),
      'POST /api/clusters/:id/synthesize': { status: 401, body: { detail: 'Missing or invalid token' } },
    })
    const { user } = renderAt('/cards/c-invoices')
    await screen.findByTestId('card-detail')
    await user.click(screen.getByRole('button', { name: 'Re-synthesize' }))
    const dialog = await screen.findByRole('dialog', { name: 'API token' })
    expect(dialog).toHaveTextContent('rejected the saved token')
    await user.type(within(dialog).getByLabelText('API token'), 'still-wrong')
    await user.click(within(dialog).getByRole('button', { name: 'Save token' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The API rejected that token')
  })
})
