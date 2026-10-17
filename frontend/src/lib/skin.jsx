import { createContext, useContext, useEffect } from 'react'

/** Wording per workspace mode. College mode is the white window, Corporate mode the black one. */
const TERMS = {
  college: {
    skin: 'college',
    modeName: 'College',
    workspace: 'Group Project',
    members: 'Teammates',
    deadline: 'Submission Deadline',
    due: 'Due',
    reminder: 'Telegram Group Check-in',
    tasks: 'Project Tasks & GitHub Assignments',
  },
  corporate: {
    skin: 'corporate',
    modeName: 'Corporate',
    workspace: 'Sprint Workspace',
    members: 'Engineering Roster',
    deadline: 'SLA & Deliverables',
    due: 'SLA',
    reminder: 'GitHub + Slack + Telegram Sync',
    tasks: 'Sprint Work Items & GitHub Assignments',
  },
}

const SkinContext = createContext(TERMS.college)

/** workspace 'company' -> Corporate mode, anything else -> College mode. */
export function skinFor(workspace) {
  return workspace === 'company' || workspace === 'corporate' ? 'corporate' : 'college'
}

/** Applies the mode to the whole page: Corporate is the dark theme, College the light one. */
export function SkinProvider({ workspace, children }) {
  const skin = skinFor(workspace)
  useEffect(() => {
    const root = document.documentElement
    root.dataset.skin = skin
    root.classList.toggle('dark', skin === 'corporate')
    document.querySelector('meta[name=theme-color]')?.setAttribute('content', skin === 'corporate' ? '#07080C' : '#F2F4F7')
    try {
      localStorage.setItem('coordina-skin', skin)
    } catch {
      /* the look still applies for this session */
    }
  }, [skin])
  return <SkinContext.Provider value={TERMS[skin]}>{children}</SkinContext.Provider>
}

export function useTerms() {
  return useContext(SkinContext)
}
