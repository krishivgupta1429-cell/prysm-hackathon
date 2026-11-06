// Built during the Meta Global AI Developer Hackathon (new work).
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/auth', () => ({ setToken: vi.fn(), setRefreshToken: vi.fn(), fetchUser: vi.fn() }))

import Login from '@/pages/Login'

describe('login page demo account', () => {
  it('shows the demo login under the form and fills the fields with one click', () => {
    render(<MemoryRouter><Login /></MemoryRouter>)
    const box = screen.getByTestId('demo-login')
    expect(box.textContent).toContain('demo@theprysm.io')
    expect(box.textContent).toContain('Demo@12345')
    fireEvent.click(screen.getByText('Fill in the demo login'))
    expect((screen.getByPlaceholderText('you@example.com') as HTMLInputElement).value).toBe('demo@theprysm.io')
    expect(document.querySelector('input[type="password"]')).toHaveProperty('value', 'Demo@12345')
  })
})
