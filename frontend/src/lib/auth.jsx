import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { AUTH_KEY, api } from './api'

const AuthContext = createContext(null)

function readStored() {
  try {
    return JSON.parse(localStorage.getItem(AUTH_KEY) || 'null')
  } catch {
    return null
  }
}

const LAST_LEAD_KEY = 'coordina-last-lead-type'

// Lets "switch to member and back" return to the same kind of Team Lead
function lastLeadType() {
  try {
    return localStorage.getItem(LAST_LEAD_KEY) || 'college_lead'
  } catch {
    return 'college_lead'
  }
}

function store(session) {
  try {
    const type = session?.account_type || session?.user?.account_type
    if (session?.role === 'lead' && type) localStorage.setItem(LAST_LEAD_KEY, type)
    if (session) localStorage.setItem(AUTH_KEY, JSON.stringify(session))
    else localStorage.removeItem(AUTH_KEY)
  } catch {
    /* private mode: the session lasts for this tab only */
  }
}

/** Invite code from the URL (?invite=CODE), if any. */
export function inviteFromUrl() {
  return new URLSearchParams(window.location.search).get('invite')?.trim().toUpperCase() || null
}

export function clearInviteFromUrl() {
  const url = new URL(window.location.href)
  url.searchParams.delete('invite')
  window.history.replaceState({}, '', url.pathname + url.search + url.hash)
}

export function AuthProvider({ children }) {
  const [session, setSession] = useState(readStored)

  const save = useCallback((s) => {
    store(s)
    setSession(s)
    return s
  }, [])

  // Validate a stored session once (the backend may have been reset)
  useEffect(() => {
    if (!session?.token) return
    api.get('/api/auth/me').then(
      (me) => save({ ...session, ...me }),
      (e) => /401|Not signed in|no longer/.test(e.message) && save(null),
    )
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const value = useMemo(() => {
    const isLead = session?.role === 'lead'
    // 'college_lead' | 'company_lead' for Team Leads, null for members
    const accountType = isLead
      ? session?.account_type || session?.user?.account_type || (session?.user?.org_type === 'corporate' ? 'company_lead' : 'college_lead')
      : null
    return {
      session,
      role: session?.role || null,
      isLead,
      accountType,
      // Corporate workspaces (and Slack / enterprise views) are for Company Team Leads only
      canCorporate: accountType === 'company_lead',
      user: session?.user || null,
      participant: session?.participant || null,
      project: session?.project || null,
      login: async (body) => save(await api.post('/api/auth/login', body)),
      registerLead: async (body) => save(await api.post('/api/auth/register-lead', body)),
      loginLead: async (body) => save(await api.post('/api/auth/login-lead', body)),
      joinMember: async (body) => save(await api.post('/api/auth/join-member', body)),
      demo: async (role, account_type = 'college_lead') => save(await api.post('/api/auth/demo', { role, account_type })),
      // Judging shortcut: jump between the Team Lead and a member of the active project
      switchRole: async (role) => save(await api.post('/api/auth/demo', { role, account_type: accountType || lastLeadType() })),
      logout: () => save(null),
    }
  }, [session, save])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  return useContext(AuthContext)
}
