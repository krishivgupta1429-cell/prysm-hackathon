// Built during the Meta Global AI Developer Hackathon (new work).
// The Dashboard's Instagram card: loading skeleton, not connected, connected, a failed status call (shows the not-connected card, never an error),
// Instagram not configured on the server (hidden), and Disconnect.
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.fn()
vi.mock('@/lib/auth', () => ({ authFetch: (...a: unknown[]) => authFetch(...a), getToken: () => 't' }))
vi.mock('@/lib/api', () => ({ apiUrl: (p: string) => p }))

import InstagramCard from './InstagramCard'

const status = (over: object = {}) => ({ configured: true, fixtureAvailable: false, connected: false, scopes: [], limits: { min: 1, max: 30, default: 10 }, account: null, ...over })
const reply = (body: unknown, ok = true) => Promise.resolve({ ok, json: () => Promise.resolve(body) })
const connected = status({ connected: true, account: { username: 'maya.fit', isFixture: false, tokenExpiresAt: null, lastImportAt: null, lastImportRequested: 10, lastImport: { imported: 8, found: 8, requested: 10, missing_metrics: [], rate_limited: false, token_invalid: false } } })

beforeEach(() => authFetch.mockReset())

describe('InstagramCard', () => {
  it('shows a skeleton while loading, then the connect card with a Connect button to /creator-dna', async () => {
    authFetch.mockReturnValue(reply(status()))
    render(<InstagramCard />)
    expect(screen.getByTestId('ig-card-loading')).toBeTruthy()
    await waitFor(() => expect(screen.getByTestId('ig-card-connect')).toBeTruthy())
    expect(screen.getByTestId('ig-title').textContent).toBe('Connect Instagram')
    expect(screen.getByText('Personalise every analysis with your real reels')).toBeTruthy()
    expect(screen.getByTestId('ig-connect').getAttribute('href')).toBe('/creator-dna')
  })

  it('shows the connected state: handle, reels imported, View Creator DNA and a quiet Disconnect', async () => {
    authFetch.mockReturnValue(reply(connected))
    render(<InstagramCard />)
    await waitFor(() => expect(screen.getByTestId('ig-card-connected')).toBeTruthy())
    expect(screen.getByTestId('ig-title').textContent).toBe('Connected as @maya.fit')
    expect(screen.getByTestId('ig-line').textContent).toBe('8 reels imported')
    expect(screen.getByTestId('ig-view').getAttribute('href')).toBe('/creator-dna')
    expect(screen.getByTestId('ig-disconnect')).toBeTruthy()
  })

  it('says so when nothing is imported yet', async () => {
    authFetch.mockReturnValue(reply(status({ connected: true, account: { username: 'a', isFixture: false, lastImport: null } })))
    render(<InstagramCard />)
    await waitFor(() => expect(screen.getByTestId('ig-line').textContent).toBe('No reels imported yet'))
  })

  it('shows the not-connected card, never an error, when the status call fails', async () => {
    authFetch.mockReturnValue(Promise.reject(new Error('network')))
    render(<InstagramCard />)
    await waitFor(() => expect(screen.getByTestId('ig-card-connect')).toBeTruthy())
    expect(screen.queryByText(/error|failed|could not/i)).toBeNull()
    authFetch.mockReset(); authFetch.mockReturnValue(reply({}, false))
    render(<InstagramCard />)
    await waitFor(() => expect(screen.getAllByTestId('ig-card-connect').length).toBe(2))
  })

  it('hides the card when Instagram is not configured on the server', async () => {
    authFetch.mockReturnValue(reply(status({ configured: false })))
    const { container } = render(<InstagramCard />)
    await waitFor(() => expect(screen.queryByTestId('ig-card-loading')).toBeNull())
    expect(container.innerHTML).toBe('')
  })

  it('keeps the card when only the test fixture is available', async () => {
    authFetch.mockReturnValue(reply(status({ configured: false, fixtureAvailable: true })))
    render(<InstagramCard />)
    await waitFor(() => expect(screen.getByTestId('ig-card-connect')).toBeTruthy())
  })

  it('Disconnect asks first, then calls the existing disconnect endpoint and returns to the connect card', async () => {
    let live = true
    authFetch.mockImplementation((url: string, opts?: { method?: string }) => {
      if (opts?.method === 'DELETE') { live = false; return reply({}) }
      return reply(live ? connected : status())
    })
    render(<InstagramCard />)
    await waitFor(() => expect(screen.getByTestId('ig-disconnect')).toBeTruthy())
    fireEvent.click(screen.getByTestId('ig-disconnect'))
    expect(authFetch.mock.calls.some(c => c[1]?.method === 'DELETE')).toBe(false)
    fireEvent.click(await screen.findByTestId('ig-disconnect-confirm'))
    await waitFor(() => expect(screen.getByTestId('ig-card-connect')).toBeTruthy())
    expect(authFetch).toHaveBeenCalledWith('/instagram/disconnect', { method: 'DELETE' })
  })
})
