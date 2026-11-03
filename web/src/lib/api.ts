// In dev, VITE_API_URL points to localhost:8000. In production it is unset and
// relative paths go through Vercel rewrites (see vercel.json).
export const API_BASE = import.meta.env.VITE_API_URL || ''

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

// File uploads can go directly to the backend (VITE_UPLOAD_URL), bypassing Vercel's 4.5 MB serverless body limit.
// Status polling and all other calls still go through apiUrl (the host's proxy rewrites).
export function uploadUrl(path: string): string {
  const direct = import.meta.env.VITE_UPLOAD_URL || import.meta.env.VITE_API_URL || ''
  return `${direct}${path}`
}
