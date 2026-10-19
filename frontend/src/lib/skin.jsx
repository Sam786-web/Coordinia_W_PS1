import { createContext, useCallback, useContext, useEffect, useState } from 'react'

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
const ThemeContext = createContext({ theme: 'light', toggleTheme: () => {} })

// Day/Night theme, remembered per workspace. Defaults keep the original look: College = day, Corporate = night.
const themeKey = (skin) => `coordina-theme-${skin}`
const defaultTheme = (skin) => (skin === 'corporate' ? 'dark' : 'light')
function savedTheme(skin) {
  try {
    const t = localStorage.getItem(themeKey(skin))
    if (t === 'light' || t === 'dark') return t
  } catch {
    /* fall back to the workspace default */
  }
  return defaultTheme(skin)
}

/** workspace 'company' -> Corporate mode, anything else -> College mode. */
export function skinFor(workspace) {
  return workspace === 'company' || workspace === 'corporate' ? 'corporate' : 'college'
}

/** Applies the workspace wording plus the Day/Night theme the user picked for this workspace. */
export function SkinProvider({ workspace, children }) {
  const skin = skinFor(workspace)
  const [themes, setThemes] = useState(() => ({ college: savedTheme('college'), corporate: savedTheme('corporate') }))
  const theme = themes[skin]
  useEffect(() => {
    const root = document.documentElement
    root.dataset.skin = skin
    root.classList.toggle('dark', theme === 'dark')
    document.querySelector('meta[name=theme-color]')?.setAttribute('content', theme === 'dark' ? '#07080C' : '#F2F4F7')
    try {
      localStorage.setItem('coordina-skin', skin)
      localStorage.setItem(themeKey(skin), theme)
    } catch {
      /* the look still applies for this session */
    }
  }, [skin, theme])
  const toggleTheme = useCallback(() => setThemes((t) => ({ ...t, [skin]: t[skin] === 'dark' ? 'light' : 'dark' })), [skin])
  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      <SkinContext.Provider value={TERMS[skin]}>{children}</SkinContext.Provider>
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  return useContext(ThemeContext)
}

export function useTerms() {
  return useContext(SkinContext)
}
