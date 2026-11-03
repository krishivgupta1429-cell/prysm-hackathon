// Auth utility — stores JWT tokens, checks login state
import { apiUrl } from './api'

const TOKEN_KEY = 'prysm_token'
const REFRESH_KEY = 'prysm_refresh'
const USER_KEY = 'prysm_user'

export interface User {
  id: string
  email: string
  username: string
  full_name?: string
  plan: string
  credits: number
  is_verified: boolean
  auth_provider: string
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY)
}

export function setRefreshToken(token: string) {
  localStorage.setItem(REFRESH_KEY, token)
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_KEY)
  localStorage.removeItem(USER_KEY)
}

export function getUser(): User | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try { return JSON.parse(raw) } catch { return null }
}

export function setUser(user: User) {
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function isLoggedIn(): boolean {
  return !!getToken()
}

// Refresh the access token using the refresh token
let refreshPromise: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const refresh = getRefreshToken()
  if (!refresh) return null

  try {
    const res = await fetch(apiUrl('/auth/refresh'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    })
    if (!res.ok) {
      clearAuth()
      return null
    }
    const data = await res.json()
    setToken(data.access_token)
    if (data.refresh_token) setRefreshToken(data.refresh_token)
    return data.access_token
  } catch {
    clearAuth()
    return null
  }
}

// Authenticated fetch — auto-refreshes on 401
export async function authFetch(url: string, opts: RequestInit = {}): Promise<Response> {
  const token = getToken()
  const headers = new Headers(opts.headers || {})
  if (token) headers.set('Authorization', `Bearer ${token}`)

  let res = await fetch(url, { ...opts, headers })

  if (res.status === 401 && getRefreshToken()) {
    // Deduplicate concurrent refresh attempts
    if (!refreshPromise) {
      refreshPromise = refreshAccessToken().finally(() => { refreshPromise = null })
    }
    const newToken = await refreshPromise
    if (newToken) {
      headers.set('Authorization', `Bearer ${newToken}`)
      res = await fetch(url, { ...opts, headers })
    } else {
      clearAuth()
      window.location.href = '/login'
    }
  }

  return res
}

export async function fetchUser(): Promise<User | null> {
  const token = getToken()
  if (!token) return null
  try {
    const res = await authFetch(apiUrl('/auth/me'))
    if (!res.ok) { clearAuth(); return null }
    const user = await res.json()
    setUser(user)
    return user
  } catch {
    return null
  }
}
