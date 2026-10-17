import { useEffect, useState } from 'react'
import {
  Building2, Crown, FileBarChart2, FileUp, Github, GraduationCap, Inbox, KeyRound, LayoutDashboard, LogOut, QrCode, RotateCcw,
  Slack, Smartphone, UserCog, Users,
} from 'lucide-react'
import { Avatar, Logo, Spinner, Switch } from './ui'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { ICON, cx } from '../lib/utils'
import { useToast } from './Toast'

const providerName = (url = '') =>
  /groq/.test(url) ? 'Groq' : /googleapis/.test(url) ? 'Gemini' : /openrouter/.test(url) ? 'OpenRouter' : /cerebras/.test(url) ? 'Cerebras' : 'Cloud'

// Which AI answered last, and what's next in line: cloud -> backup -> laptop (Ollama) -> offline rules
function AiEngineStatus() {
  const [st, setSt] = useState(null)
  useEffect(() => {
    api.get('/api/llm-status').then(setSt, () => setSt(false))
  }, [])
  if (st === false) return null
  const layers = st
    ? [
        { name: 'Primary Cloud LLM', label: `${providerName(st.opencode?.base_url)} · ${st.opencode?.models?.[0] || ''}`, ready: st.opencode?.configured && !st.opencode?.circuit_open },
        { name: 'Backup Cloud LLM', label: `${providerName(st.backup?.base_url)} · ${st.backup?.model || ''}`, ready: st.backup?.configured && !st.backup?.circuit_open },
        {
          name: 'Local LLM (Ollama)',
          label: st.local?.configured ? `Ollama · ${st.local.model}${st.local.warm ? '' : ' (loading)'}` : 'Ollama · not set up',
          ready: st.local?.configured && !st.local?.circuit_open,
        },
        { name: 'Offline Smart Engine', label: 'Offline rules · always on', ready: true },
      ].filter((l) => l.name !== 'Backup Cloud LLM' || st.backup?.configured) // the cloud backup slot is optional
    : []
  return (
    <div className="rounded-row px-2.5 py-2">
      <p className="mb-1.5 text-sm font-bold text-fg">AI engine</p>
      {!st ? (
        <Spinner />
      ) : (
        <ol className="space-y-1">
          {layers.map((l, i) => {
            const active = st.active_layer === l.name
            return (
              <li key={l.name} className={cx('flex items-center gap-2 rounded-xl px-2 py-1 text-xs', active && 'bg-lime/40 dark:bg-lime/15')}>
                <span className={cx('h-2 w-2 shrink-0 rounded-full', l.ready ? 'bg-emerald-500' : 'bg-zinc-300 dark:bg-zinc-600')} />
                <span className="w-3 shrink-0 font-bold text-fg-3">{i + 1}</span>
                <span className={cx('min-w-0 flex-1 truncate font-semibold', l.ready ? 'text-fg' : 'text-fg-3')} title={l.label}>
                  {l.label}
                </span>
                {active && <span className="shrink-0 text-[10px] font-extrabold text-fg">in use</span>}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}

function RailButton({ icon: Icon, label, active, onClick, disabled, badge }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      aria-current={active ? 'page' : undefined}
      className={cx(
        'relative flex h-11 w-11 items-center justify-center rounded-2xl transition-all disabled:pointer-events-none disabled:opacity-30',
        active ? 'bg-coral/15 text-coral' : 'text-zinc-400 hover:bg-zinc-500/10 hover:text-fg',
      )}
    >
      <Icon {...ICON} size={19} />
      {badge > 0 && (
        <span className="absolute -right-0.5 -top-0.5 grid h-[18px] min-w-[18px] place-items-center rounded-full bg-coral px-1 text-[10px] font-extrabold text-white ring-2 ring-shell">
          {badge > 9 ? '9+' : badge}
        </span>
      )}
    </button>
  )
}

/** Workspace menu: your projects in this workspace (click to switch) and "New project". */
function ProjectMenu({ skin, state, onClose, onNew }) {
  const auth = useAuth()
  const toast = useToast()
  const [busy, setBusy] = useState(null)
  const projects = state?.projects || []
  const current = state?.active_project?.id
  const corporate = skin === 'corporate'
  const open = async (p) => {
    if (p.id === current) return onClose()
    setBusy(p.id)
    try {
      await api.post(`/api/projects/${p.id}/activate`)
      toast.info('Switched project', p.name)
      onClose()
    } catch (e) {
      toast.error('Could not switch project', e.message)
    } finally {
      setBusy(null)
    }
  }
  return (
    <>
      <div className="fixed inset-0 z-30" onClick={onClose} />
      <div role="menu" className="absolute bottom-14 left-full z-40 ml-3 w-72 animate-fade-in rounded-bento border border-line bg-shell p-2 shadow-2xl shadow-black/15 dark:shadow-black/60">
        <div className={cx('flex items-center gap-3 rounded-row px-3 py-3', corporate ? 'bg-panel' : 'bg-lavender text-[#1E1B4B]')}>
          {corporate ? <Building2 {...ICON} size={18} className="text-fg" /> : <GraduationCap {...ICON} size={18} />}
          <div className="min-w-0">
            <p className={cx('text-sm font-extrabold', corporate && 'text-fg')}>{corporate ? 'Corporate Sprint Mode' : 'College Capstone Mode'}</p>
            <p className={cx('text-xs font-semibold', corporate ? 'text-fg-2' : 'opacity-70')}>
              {auth.isLead ? `Your ${projects.length} project${projects.length === 1 ? '' : 's'}` : 'Set by your Team Lead'}
            </p>
          </div>
        </div>
        <div className="mt-1 max-h-72 space-y-0.5 overflow-y-auto px-1 py-1">
          {projects.map((p) => (
            <button
              key={p.id}
              role="menuitem"
              disabled={!auth.isLead || !!busy}
              onClick={() => open(p)}
              className={cx(
                'flex w-full items-center gap-2.5 rounded-row px-2.5 py-2 text-left text-sm transition',
                p.id === current ? 'bg-subtle font-extrabold text-fg' : 'font-semibold text-fg-2 hover:bg-subtle hover:text-fg',
              )}
            >
              <span className={cx('h-2 w-2 shrink-0 rounded-full', p.id === current ? 'bg-coral' : 'bg-line-strong')} />
              <span className="min-w-0 flex-1 truncate">{p.name}</span>
              {busy === p.id ? <Spinner className="h-3.5 w-3.5" /> : p.id === current && <span className="text-[10px] font-bold text-fg-3">Open</span>}
            </button>
          ))}
          {!projects.length && <p className="px-2.5 py-2 text-sm text-fg-3">No projects yet.</p>}
        </div>
        {auth.isLead && (
          <button
            role="menuitem"
            onClick={() => {
              onClose()
              onNew()
            }}
            className="mt-1 flex w-full items-center gap-2 rounded-row border-t border-line px-2.5 py-2.5 text-left text-sm font-bold text-coral hover:bg-subtle"
          >
            <Github {...ICON} /> New project from a GitHub repo
          </button>
        )}
      </div>
    </>
  )
}

function UserMenu({ state, autopilot, busy, onClose, onToggleAutopilot, onStrictChange, onToggleSlack, onOpenImport, onReset }) {
  const auth = useAuth()
  const [cadence, setCadence] = useState(autopilot?.cadence && autopilot.cadence !== 'custom' ? autopilot.cadence : 'live')
  const [switching, setSwitching] = useState(false)
  const project = state?.active_project
  const integrations = state?.integrations || {}
  const switchTo = auth.isLead ? 'member' : 'lead'

  const doSwitch = async () => {
    setSwitching(true)
    try {
      await auth.switchRole(switchTo)
      onClose()
    } finally {
      setSwitching(false)
    }
  }

  return (
    <>
      <div className="fixed inset-0 z-30" onClick={onClose} />
      <div role="menu" className="absolute bottom-0 left-full z-40 ml-3 w-72 animate-fade-in rounded-bento border border-line bg-shell p-2 shadow-2xl shadow-black/15 dark:shadow-black/60">
        <div className="flex items-center gap-3 rounded-row bg-panel px-3 py-3">
          <Avatar name={auth.user?.name || '?'} size="md" />
          <div className="min-w-0">
            <p className="truncate text-sm font-extrabold text-fg">{auth.user?.name}</p>
            <p className="text-xs font-semibold text-fg-2">{auth.isLead ? (auth.canCorporate ? 'Company Team Lead' : 'College Team Lead') : `Team Member${auth.participant?.auth_pin ? ` • ${auth.participant.auth_pin}` : ''}`}</p>
          </div>
        </div>

        {auth.isLead && (
          <div className="mt-2 space-y-1 px-1">
            <div className="rounded-row px-2.5 py-2">
              <div className="flex items-center gap-2">
                <span className="flex-1 text-sm font-bold text-fg">Auto-Pilot reminders</span>
                <Switch checked={!!autopilot?.enabled} onChange={(on) => onToggleAutopilot(on, cadence)} disabled={busy.autopilot} label="Auto-Pilot reminders" />
              </div>
              <select
                className="input mt-2 py-1.5 text-xs"
                value={cadence}
                aria-label="Reminder cadence"
                onChange={(e) => {
                  setCadence(e.target.value)
                  if (autopilot?.enabled) onToggleAutopilot(true, e.target.value)
                }}
              >
                {Object.entries(autopilot?.cadences || { live: '45s Live Demo', hourly: 'Every 1 Hour', '6h': 'Every 6 Hours', daily10: 'Daily 10 AM' }).map(([k, l]) => (
                  <option key={k} value={k}>
                    {l}
                  </option>
                ))}
              </select>
            </div>
            <AiEngineStatus />
            <label className="flex items-center gap-2 rounded-row px-2.5 py-2 text-sm font-bold text-fg" title="When on, a task can't be completed until everything it depends on is Complete">
              <span className="flex-1">Strict dependency lock</span>
              <Switch checked={!!project?.strict_dependencies} onChange={onStrictChange} label="Strict dependency lock" />
            </label>
            {auth.canCorporate && (
              <label className="flex items-center gap-2 rounded-row px-2.5 py-2 text-sm font-bold text-fg">
                <Slack {...ICON} className="text-fg-3" />
                <span className="flex-1">Slack (company add-on)</span>
                <Switch checked={!!integrations.slack} onChange={onToggleSlack} disabled={busy.slack} label="Slack webhook" />
              </label>
            )}
            <button role="menuitem" onClick={() => { onClose(); onOpenImport() }} className="flex w-full items-center gap-2 rounded-row px-2.5 py-2 text-left text-sm font-semibold text-fg-2 hover:bg-subtle hover:text-fg">
              <FileUp {...ICON} /> Import CSV / Test a commit
            </button>
            {onReset && (
              <button role="menuitem" onClick={() => { onClose(); onReset() }} disabled={busy.reset} className="flex w-full items-center gap-2 rounded-row px-2.5 py-2 text-left text-sm font-semibold text-fg-2 hover:bg-subtle hover:text-fg">
                {busy.reset ? <Spinner /> : <RotateCcw {...ICON} />} Reset demo data
              </button>
            )}
          </div>
        )}

        <div className="mt-1 space-y-1 border-t border-line px-1 pt-2">
          <button role="menuitem" onClick={doSwitch} disabled={switching} className="flex w-full items-center gap-2 rounded-row px-2.5 py-2 text-left text-sm font-semibold text-fg hover:bg-subtle">
            {switching ? <Spinner /> : switchTo === 'lead' ? <Crown {...ICON} /> : <Users {...ICON} />} Switch to {switchTo === 'lead' ? 'Team Lead' : 'Team Member'} view
          </button>
          <button role="menuitem" onClick={auth.logout} className="flex w-full items-center gap-2 rounded-row px-2.5 py-2 text-left text-sm font-semibold text-fg-2 hover:bg-subtle hover:text-fg">
            <LogOut {...ICON} /> Sign out
          </button>
        </div>
      </div>
    </>
  )
}

export default function IconRail({ view, onView, onOpen, skin, ...menuProps }) {
  const auth = useAuth()
  const [menu, setMenu] = useState(null) // 'projects' | 'user' | null
  return (
    <nav className="flex w-16 shrink-0 flex-col items-center justify-between py-5" aria-label="Main">
      <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-coral/15" title="Coordina">
        <Logo className="h-8 w-8" />
      </div>

      <div className="flex flex-col items-center gap-2">
        <RailButton icon={LayoutDashboard} label="Dashboard" active={view === 'dashboard'} onClick={() => onView('dashboard')} />
        <RailButton icon={Github} label="Paste GitHub Repo (auto-divide tasks)" onClick={() => onOpen('create')} disabled={!auth.isLead} />
        <RailButton icon={KeyRound} label="Team Members & Auth PINs" onClick={() => onOpen('team')} />
        <RailButton icon={Smartphone} label="Mobile Companion Preview" active={view === 'mobile'} onClick={() => onView('mobile')} />
        <RailButton icon={FileBarChart2} label="Standup Report" active={view === 'report'} onClick={() => onView('report')} />
        <RailButton
          icon={Inbox}
          label={auth.isLead ? 'Task requests from your team' : 'Request a task'}
          badge={auth.isLead ? (menuProps.state?.task_requests || []).filter((r) => r.status === 'pending').length : 0}
          onClick={() => onOpen(auth.isLead ? 'requests' : 'request')}
        />
        <RailButton icon={QrCode} label="QR Connect" onClick={() => onOpen('qr')} />
      </div>

      <div className="relative flex flex-col items-center gap-2">
        <RailButton
          icon={skin === 'corporate' ? Building2 : GraduationCap}
          label={`${skin === 'corporate' ? 'Corporate' : 'College'} workspace: switch project`}
          active={menu === 'projects'}
          onClick={() => setMenu((m) => (m === 'projects' ? null : 'projects'))}
        />
        <RailButton icon={UserCog} label="Switch user / settings / log out" active={menu === 'user'} onClick={() => setMenu((m) => (m === 'user' ? null : 'user'))} />
        {menu === 'projects' && <ProjectMenu skin={skin} state={menuProps.state} onClose={() => setMenu(null)} onNew={() => onOpen('create')} />}
        {menu === 'user' && <UserMenu onClose={() => setMenu(null)} {...menuProps} />}
      </div>
    </nav>
  )
}
