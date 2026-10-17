export const ENTERPRISE_PROJECT = 'FinTech Production Release v2.4'

/** Shared lucide icon props */
export const ICON = { size: 15, strokeWidth: 1.75 }

export function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

/** Backend timestamps from SQLite are naive UTC ("2026-10-16T13:05:39"); make them explicit. */
export function parseTs(ts) {
  if (!ts) return null
  const s = String(ts)
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z')
}

// ----- Deadlines: stored as local ISO ("2026-10-18T18:00"); older free-text labels are shown as written -----

/** Date for an ISO deadline (local time), or null for free text like "Friday". */
export function deadlineDate(text) {
  if (!text || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(text)) return null
  const d = new Date(text)
  return Number.isNaN(d.getTime()) ? null : d
}

/** { label: 'Sat 18 Oct, 6:00 PM', state: 'overdue' | 'soon' | 'later' | 'none' } */
export function formatDeadline(text, done = false, now = Date.now()) {
  if (!text) return { label: '', state: 'none' }
  const d = deadlineDate(text)
  if (!d) return { label: text, state: 'later' }
  const day = d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const left = d.getTime() - now
  const state = done ? 'later' : left < 0 ? 'overdue' : left < 6 * 3600 * 1000 ? 'soon' : 'later'
  return { label: `${day}, ${time}`, state }
}

/** Value for <input type="datetime-local"> */
export function toDateTimeInput(text) {
  const d = deadlineDate(text)
  if (!d) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export const DEADLINE_TONE = {
  overdue: 'bg-flamingo text-rose-950',
  soon: 'bg-butter text-amber-950',
  later: 'bg-subtle text-fg-2 ring-1 ring-line',
}

export function clockTime(ts) {
  const d = parseTs(ts)
  return d ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''
}

export function duration(seconds) {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m}m ${seconds % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

// ---------- Status ----------

// Pastel pills: same vivid colours in College (white) and Corporate (black) mode
export const STATUS = {
  Complete: { label: 'Complete', pill: 'bg-mint text-emerald-950', dot: 'bg-emerald-500' },
  'In Progress': { label: 'In Progress', pill: 'bg-butter text-amber-950', dot: 'bg-amber-400' },
  Blocked: { label: 'Blocked', pill: 'bg-flamingo text-rose-950', dot: 'bg-rose-500' },
  Pending: { label: 'Pending', pill: 'bg-lavender text-[#1E1B4B]', dot: 'bg-violet-400' },
  'At Risk': { label: 'At Risk', pill: 'bg-[#FED7AA] text-orange-950', dot: 'bg-orange-400' },
}

// ---------- People ----------

const TINTS = [
  'bg-lavender text-[#1E1B4B]',
  'bg-butter text-amber-950',
  'bg-flamingo text-rose-950',
  'bg-mint text-emerald-950',
  'bg-[#BAE6FD] text-sky-950',
  'bg-[#FED7AA] text-orange-950',
  'bg-[#D9F99D] text-lime-950',
  'bg-[#FECDD3] text-rose-950',
]

export function avatarTint(name = '') {
  let h = 0
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return TINTS[h % TINTS.length]
}

export function initials(name = '') {
  const parts = name.trim().split(/\s+/)
  return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '?'
}

export function availabilityLabel(av) {
  const a = (av || 'Available').trim()
  const low = a.toLowerCase()
  if (['available', 'available now', 'free now'].includes(low)) return 'Free now'
  if (low.startsWith('async')) return 'Async only'
  return a
}

function parseClock(str) {
  const m = /(\d{1,2})(?::(\d{2}))?\s*(am|pm)?/i.exec(str || '')
  if (!m) return null
  let h = parseInt(m[1], 10)
  const ap = (m[3] || '').toLowerCase()
  if (ap === 'pm' && h < 12) h += 12
  if (ap === 'am' && h === 12) h = 0
  const d = new Date()
  d.setHours(h, parseInt(m[2] || '0', 10), 0, 0)
  return d
}

export function isFreeNow(av) {
  const low = (av || 'Available').toLowerCase()
  if (['available', 'available now', 'free now'].includes(low)) return true
  if (low.startsWith('free at') || low.startsWith('busy until')) {
    const t = parseClock(low)
    return t ? t.getTime() <= Date.now() : false
  }
  return false
}

// ---------- Tasks & dependencies ----------

/** Display number per task (1-based, project order). */
export function taskNumbers(tasks) {
  return Object.fromEntries(tasks.map((t, i) => [t.id, i + 1]))
}

/** Tasks transitively downstream of a Blocked task (and not Complete) -> { taskId: rootBlockedTask }. */
export function cascadingRisk(tasks) {
  const byId = Object.fromEntries(tasks.map((t) => [t.id, t]))
  const risk = {}
  for (const t of tasks) {
    if (t.status === 'Complete' || t.status === 'Blocked') continue
    let up = byId[t.depends_on_task_id]
    const seen = new Set()
    while (up && !seen.has(up.id)) {
      seen.add(up.id)
      if (up.status === 'Blocked') {
        risk[t.id] = up
        break
      }
      up = byId[up.depends_on_task_id]
    }
  }
  return risk
}

/** "Waiting for test credentials" -> "test credentials" */
export function extractNeed(reason = '') {
  const m = /(?:waiting (?:for|on)|blocked (?:on|by)|need(?:s|ed)?|require[sd]?|missing)\s+(?:the\s+)?(.+)/i.exec(reason)
  if (m) return m[1].replace(/[.!]+$/, '')
  const p = /^(?:the\s+)?(.+?)\s+(?:still\s+)?(?:pending|needed|not received)\b/i.exec(reason)
  return p ? p[1] : null
}

export function detectMode(project) {
  if (!project) return 'custom'
  if (project.name === ENTERPRISE_PROJECT) return 'enterprise'
  if (project.id === 1) return 'college'
  return 'custom'
}

export function parseDetails(json) {
  if (!json) return {}
  try {
    return typeof json === 'string' ? JSON.parse(json) : json
  } catch {
    return {}
  }
}
