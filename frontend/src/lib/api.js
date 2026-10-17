// Same-origin by default (Vite proxies /api and /ws). Set VITE_API_BASE to talk to a remote backend.
const BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')

export const AUTH_KEY = 'coordina-auth'

function authHeader() {
  try {
    const token = JSON.parse(localStorage.getItem(AUTH_KEY) || 'null')?.token
    return token ? { Authorization: `Bearer ${token}` } : {}
  } catch {
    return {}
  }
}

async function request(path, { method = 'GET', body } = {}) {
  const res = await fetch(BASE + path, {
    method,
    headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...authHeader() },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) {
    const detail = data && typeof data === 'object' ? data.detail : null
    const msg = typeof detail === 'string' ? detail : detail ? detail.map?.((d) => d.msg).join(', ') || JSON.stringify(detail) : `HTTP ${res.status}`
    throw new Error(msg)
  }
  return data
}

export const api = {
  get: (path) => request(path),
  post: (path, body = {}) => request(path, { method: 'POST', body }),
  patch: (path, body = {}) => request(path, { method: 'PATCH', body }),
  delete: (path) => request(path, { method: 'DELETE' }),
}

export function wsUrl() {
  if (BASE) return BASE.replace(/^http/, 'ws') + '/ws/live'
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/live`
}
