import { AUTH_KEY } from './api'

const BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')

/** Download a file from the API (with the signed-in user's token) under the name the server suggests. */
export async function downloadFile(path, fallbackName) {
  let token = null
  try {
    token = JSON.parse(localStorage.getItem(AUTH_KEY) || 'null')?.token
  } catch {
    token = null
  }
  const res = await fetch(BASE + path, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      msg = (await res.json()).detail || msg
    } catch {
      /* not JSON */
    }
    throw new Error(msg)
  }
  const name = /filename="?([^"]+)"?/.exec(res.headers.get('content-disposition') || '')?.[1] || fallbackName
  const url = URL.createObjectURL(await res.blob())
  const a = Object.assign(document.createElement('a'), { href: url, download: name })
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return name
}

/** The project's progress report: 'pdf' (formatted, to share) or 'md' (Markdown, to paste or submit). */
export const downloadReport = (format = 'pdf', projectId = null) =>
  downloadFile(`${format === 'md' ? '/api/report.md' : '/api/report.pdf'}${projectId ? `?project_id=${projectId}` : ''}`, `progress-report.${format}`)
