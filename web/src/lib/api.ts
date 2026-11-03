// In dev, VITE_API_URL points to localhost:8000. In production it is unset and
// relative paths go through Vercel rewrites (see vercel.json).
export const API_BASE = import.meta.env.VITE_API_URL || ''

const RAILWAY_URL = 'https://YOUR-BACKEND-URL.example.com'

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

// File uploads go directly to Railway, bypassing Vercel's 4.5 MB serverless body limit.
// Status polling and all other calls still go through apiUrl (Vercel proxy).
export function uploadUrl(path: string): string {
  if (!import.meta.env.VITE_API_URL) {
    return `${RAILWAY_URL}${path}`
  }
  return `${API_BASE}${path}`
}
