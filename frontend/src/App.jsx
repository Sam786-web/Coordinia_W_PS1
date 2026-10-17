import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ServerCrash } from 'lucide-react'
import IconRail from './components/IconRail'
import DashboardView from './components/DashboardView'
import LimePanel from './components/LimePanel'
import ReportView from './components/ReportView'
import MobileView from './components/MobileView'
import MobileAppView from './components/MobileAppView'
import LoginScreen from './components/LoginScreen'
import ErrorBoundary from './components/ErrorBoundary'
import { useToast } from './components/Toast'
import { Logo, Spinner } from './components/ui'
import CreateProjectModal from './components/modals/CreateProjectModal'
import TeamModal from './components/modals/TeamModal'
import FeedModal from './components/modals/FeedModal'
import TaskUpdateModal from './components/modals/TaskUpdateModal'
import EditTaskModal from './components/modals/EditTaskModal'
import DeadlinesModal from './components/modals/DeadlinesModal'
import RequestTaskModal from './components/modals/RequestTaskModal'
import TaskRequestsModal from './components/modals/TaskRequestsModal'
import SlackModal from './components/modals/SlackModal'
import { AddMemberModal, AddTaskModal } from './components/modals/TeamModals'
import IntegrationsModal from './components/modals/IntegrationsModal'
import QRConnectModal from './components/modals/QRConnectModal'
import { useLiveState } from './hooks/useLiveState'
import { api } from './lib/api'
import { inviteFromUrl, useAuth } from './lib/auth'
import { SkinProvider, skinFor } from './lib/skin'
import { canUpdate } from './lib/tasks'
import { ICON, cascadingRisk, taskNumbers } from './lib/utils'

function deliverySummary(notifications = []) {
  if (!notifications.length) return 'Nobody had open tasks to ask about.'
  const sent = notifications.filter((n) => n.delivered).length
  const unlinked = notifications.filter((n) => !n.delivered && n.channel === 'simulated').length
  const failed = notifications.length - sent - unlinked
  return [
    sent && `${sent} sent on Telegram`,
    unlinked && `${unlinked} not verified on Telegram yet`,
    failed && `${failed} couldn't be delivered (check the internet)`,
  ].filter(Boolean).join(', ')
}

/** Phone experience when opened with ?mode=mobile, at /app, or on a narrow screen. */
function useMobileMode() {
  const forced = useMemo(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get('mode') === 'mobile' || /^\/app\/?$/.test(window.location.pathname)
  }, [])
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 767px)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    const onChange = (e) => setNarrow(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return forced || narrow
}

export default function App() {
  const mobile = useMobileMode()
  const auth = useAuth()
  const invite = inviteFromUrl()
  // Sign in first; an invite link for a project you haven't joined opens the Join screen
  if (!auth.session || (invite && auth.project?.invite_code !== invite)) return <LoginScreen invite={invite} mobile={mobile} />
  return mobile ? <MobileApp /> : <DesktopApp />
}

function ConnectionScreen({ error, onRetry, mobile }) {
  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-bg p-6">
      <div className={mobile ? 'flex flex-col items-center gap-4 text-center' : 'app-shell w-full max-w-sm p-8 text-center'}>
        <Logo className="mx-auto h-11 w-11" />
        {error ? (
          <>
            {!mobile && <ServerCrash {...ICON} size={18} className="mx-auto mt-3 text-rose-500" />}
            <h1 className="mt-2 text-base font-extrabold text-fg">{mobile ? "Can't reach the laptop" : "Can't reach the backend"}</h1>
            <p className="mt-1 text-sm text-fg-2">{mobile ? 'Make sure your phone is on the same Wi-Fi or hotspot as the laptop running Coordina.' : error}</p>
            {!mobile && <pre className="mt-4 overflow-x-auto rounded-2xl bg-panel p-3 text-left font-mono text-xs text-fg-2">python start_all.py</pre>}
            <button className="btn-primary mt-4 min-h-[44px] px-6" onClick={onRetry}>
              Try again
            </button>
          </>
        ) : (
          <p className="mt-5 flex items-center justify-center gap-2 text-sm text-fg-2">
            <Spinner /> Loading workspace…
          </p>
        )}
      </div>
    </div>
  )
}

function MobileApp() {
  const auth = useAuth()
  const { state, error, refresh, ws } = useLiveState(auth.role === 'member' ? auth.project?.id : null)
  if (!state) return <ConnectionScreen error={error} onRetry={refresh} mobile />
  return (
    <SkinProvider workspace={state.active_project?.workspace_type}>
      <ErrorBoundary fullscreen title="The mobile app hit an error">
        <MobileAppView state={state} ws={ws} />
      </ErrorBoundary>
    </SkinProvider>
  )
}

function DesktopApp() {
  const toast = useToast()
  const auth = useAuth()
  const { state, error, refresh, autopilot, refreshAutopilot, ws } = useLiveState(auth.role === 'member' ? auth.project?.id : null)
  const [view, setView] = useState('dashboard')
  const [modal, setModal] = useState(null)
  const [createOpts, setCreateOpts] = useState({ tab: 'github', repo: '' })
  const [updating, setUpdating] = useState(null)
  const [editing, setEditing] = useState(null) // { id, del }
  const [busy, setBusy] = useState({})
  const [limeCollapsed, setLimeCollapsed] = useState(false)

  const project = state?.active_project
  const tasks = state?.tasks || []
  const participants = state?.participants || []
  const logs = state?.logs || []
  const numbers = useMemo(() => taskNumbers(tasks), [tasks])
  const risk = useMemo(() => cascadingRisk(tasks), [tasks])
  // College mode = the white window, Corporate mode = the black one.
  // Each Team Lead works in exactly one workspace (Company -> Corporate, College -> College);
  // members follow their project's workspace.
  const workspace = auth.isLead ? (auth.canCorporate ? 'company' : 'college') : project?.workspace_type || 'college'
  const skin = skinFor(workspace)
  // Demo accounts share the seeded demo projects; everyone else only ever sees their own
  const isDemoAccount = /@coordina\.demo$/.test(auth.user?.email || '')

  // Keep the open update dialog in sync with live data
  const updatingTask = updating ? tasks.find((t) => t.id === updating) : null
  const editingTask = editing ? tasks.find((t) => t.id === editing.id) : null

  // Team Lead: a popup whenever a teammate sends a new task request (live, over the WebSocket refresh)
  const seenRequests = useRef(null)
  useEffect(() => {
    if (!auth.isLead || !state) return
    const pending = (state.task_requests || []).filter((r) => r.status === 'pending')
    const ids = new Set(pending.map((r) => r.id))
    if (seenRequests.current) {
      const fresh = pending.filter((r) => !seenRequests.current.has(r.id))
      if (fresh.length === 1) toast.info(`New task request from ${fresh[0].requester_name}`, `"${fresh[0].title}" for ${fresh[0].assignee_name}`)
      else if (fresh.length > 1) toast.info(`${fresh.length} new task requests`, 'Review them above your task list.')
    }
    seenRequests.current = ids
  }, [state, auth.isLead]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = useCallback(
    async (key, fn) => {
      setBusy((b) => ({ ...b, [key]: true }))
      try {
        return await fn()
      } catch (e) {
        toast.error('Something went wrong', e.message)
      } finally {
        setBusy((b) => ({ ...b, [key]: false }))
        refresh()
      }
    },
    [toast, refresh],
  )

  const reset = () =>
    run('reset', async () => {
      await api.post('/api/presets/load', { mode: project?.workspace_type === 'company' ? 'enterprise' : 'college' })
      toast.success('Demo data reset')
    })

  const toggleAutopilot = (on, cadence = 'live') =>
    run('autopilot', async () => {
      const st = await api.post('/api/autopilot/toggle', { enabled: on, cadence })
      await refreshAutopilot()
      toast.info(on ? `Auto-Pilot reminders: ${st.cadence_label}` : 'Auto-Pilot reminders off')
    })

  const takeUpdates = () =>
    run('updates', async () => {
      const out = await api.post('/api/take-updates', { project_id: project?.id })
      toast.success(out.asked.length ? `Asked ${out.asked.length} member${out.asked.length === 1 ? '' : 's'} for updates` : 'Everything is complete', deliverySummary(out.notifications))
    })

  const syncGithub = () =>
    run('sync', async () => {
      const out = await api.post(`/api/projects/${project.id}/sync-github`)
      if (out.matched.length) toast.success(`${out.matched.length} task${out.matched.length === 1 ? '' : 's'} verified by commits`, out.matched.map((m) => `${m.sha} → ${m.task}`).join(' · '))
      else toast.info(`Read ${out.commits} recent commit${out.commits === 1 ? '' : 's'}, none named a task yet`, out.unmatched.length ? `Latest: "${out.unmatched[0].message}". Mention a task in commit messages to verify it.` : undefined)
    })

  const setStrict = (on) =>
    run('strict', async () => {
      await api.patch(`/api/projects/${project.id}`, { strict_dependencies: on })
      toast.info(on ? 'Strict dependency lock on' : 'Strict dependency lock off')
    })

  const toggleSlack = async (on) => {
    setBusy((b) => ({ ...b, slack: true }))
    try {
      await api.post('/api/integrations', { slack_enabled: on })
      toast.info(on ? 'Slack on' : 'Slack off')
      refresh()
    } catch (e) {
      if (on && /webhook URL/i.test(e.message)) setModal('slack')
      else toast.error('Could not update Slack', e.message)
    } finally {
      setBusy((b) => ({ ...b, slack: false }))
    }
  }

  const pingTask = async (task) => {
    try {
      const out = await api.post(`/api/ping-task/${task.id}`)
      const n = out.notifications?.[0]
      // On a Blocked task the reminder goes to whoever can unblock it, not the blocked owner
      const who = n?.to || task.owner_name
      const unblock = out.action?.type === 'BLOCKER_REMINDER'
      if (n?.delivered)
        toast.success(unblock ? `Reminded ${who} to unblock ${out.action.blocked}` : `Check-in sent to ${who}`,
          unblock ? task.blocker_reason || undefined : undefined)
      else if (n?.error === 'network' || (n?.channel === 'telegram' && !n?.error))
        toast.error(`Couldn't reach Telegram`, `Check the laptop's internet and ping ${who} again.`)
      else if (n?.error === 'telegram')
        toast.error(`Telegram refused the message`, `${who} may have blocked or stopped the bot. Ask them to open it and send /start.`)
      else toast.info(unblock ? `Reminder for ${who} logged` : `Check-in logged for ${who}`, `${who} hasn't verified Telegram yet.`)
    } catch (e) {
      toast.error('Check-in failed', e.message)
    }
  }

  // Lime checklist: tick / untick a task (Team Lead overrides, owners go through the coordinator)
  const toggleTask = async (task) => {
    if (!canUpdate(task, auth)) return toast.error('Access denied', `Only ${task.owner_name || 'the owner'} or the Team Lead can update this task.`)
    const done = task.status === 'Complete'
    try {
      if (auth.isLead) {
        await api.post(`/api/tasks/${task.id}/override`, { status: done ? 'In Progress' : 'Complete' })
        toast.success(`"${task.title}" ${done ? 'reopened' : 'marked Complete'}`)
      } else {
        const out = await api.post('/api/simulate-reply', {
          participant_id: task.owner_id, task_id: task.id, source: 'simulator',
          message: done ? 'Reopened this task, working on it again' : 'Marked complete from my task list',
        })
        if (out.result.contradiction_warning) toast.info('Not marked done yet', 'A task this depends on is still blocked.')
        else toast.success(`"${task.title}" → ${out.result.new_status}`)
      }
      refresh()
    } catch (e) {
      toast.error('Update refused', e.message)
    }
  }

  const openCreate = (tab = 'github', repo = '') => {
    setCreateOpts({ tab, repo })
    setModal('create')
  }

  if (!state) return <ConnectionScreen error={error} onRetry={refresh} />

  const lime = (
    <LimePanel
      state={state}
      tasks={tasks}
      participants={participants}
      logs={logs}
      numbers={numbers}
      onOpen={setModal}
      onTakeUpdates={takeUpdates}
      sending={busy.updates}
      collapsed={limeCollapsed}
      onCollapse={() => setLimeCollapsed((c) => !c)}
      onToggleTask={toggleTask}
    />
  )

  return (
    <SkinProvider workspace={workspace}>
      <div className="min-h-screen bg-bg p-3 lg:p-5">
        <div className="app-shell flex h-[calc(100vh-24px)] overflow-hidden lg:h-[calc(100vh-40px)]">
          <IconRail
            view={view}
            onView={setView}
            onOpen={(m) => (m === 'create' ? openCreate('github') : setModal(m))}
            skin={skin}
            state={state}
            autopilot={autopilot}
            busy={busy}
            onToggleAutopilot={toggleAutopilot}
            onStrictChange={setStrict}
            onToggleSlack={toggleSlack}
            onOpenImport={() => setModal('import')}
            onReset={isDemoAccount ? reset : null}
          />
          <div className="flex min-w-0 flex-1 gap-6 py-5 pr-5">
            <main className="min-w-0 flex-1 overflow-y-auto pr-1">
              {error && (
                <div className="mb-4 rounded-row bg-butter px-4 py-2.5 text-sm font-semibold text-amber-950">Connection lost. Showing the last known state and retrying.</div>
              )}
              <ErrorBoundary key={view} title="This view failed to load">
                {view === 'dashboard' && (
                  <DashboardView
                    state={state}
                    tasks={tasks}
                    participants={participants}
                    numbers={numbers}
                    risk={risk}
                    skin={skin}
                    onSyncGithub={syncGithub}
                    syncing={busy.sync}
                    onTakeUpdates={takeUpdates}
                    sending={busy.updates}
                    onPing={pingTask}
                    onUpdate={(t) => setUpdating(t.id)}
                    onEdit={(t, del = false) => setEditing({ id: t.id, del })}
                    onNewTask={() => setModal('task')}
                    onCreate={() => openCreate('github')}
                    onDeadlines={() => setModal('deadlines')}
                    onRequest={() => setModal('request')}
                    version={ws.lastEventAt}
                  />
                )}
                {view === 'report' && <ReportView state={state} version={ws.lastEventAt} />}
                {view === 'mobile' && <MobileView state={state} ws={ws} onOpenConnect={() => setModal('qr')} />}
              </ErrorBoundary>
              <div className="mt-6 xl:hidden">{lime}</div>
            </main>
            <div className={limeCollapsed ? 'hidden xl:flex' : 'hidden w-[min(34%,420px)] shrink-0 xl:flex'}>{lime}</div>
          </div>
        </div>
      </div>

      <TaskUpdateModal task={updatingTask} onClose={() => setUpdating(null)} />
      <EditTaskModal task={auth.isLead ? editingTask : null} participants={participants} startWithDelete={!!editing?.del} onClose={() => setEditing(null)} />
      <DeadlinesModal open={modal === 'deadlines'} onClose={() => setModal(null)} project={project} tasks={tasks} />
      <TaskRequestsModal open={modal === 'requests'} onClose={() => setModal(null)} version={ws.lastEventAt} />
      <RequestTaskModal open={modal === 'request'} onClose={() => setModal(null)} participants={participants} tasks={tasks} requests={state.task_requests || []} />
      <CreateProjectModal open={modal === 'create'} onClose={() => setModal(null)} participants={participants} workspace={workspace} canCorporate={auth.canCorporate} initialTab={createOpts.tab} initialRepo={createOpts.repo} />
      <TeamModal open={modal === 'team'} onClose={() => setModal(null)} project={project} participants={participants} bot={state.integrations?.telegram_bot} onAddMember={() => setModal('member')} />
      <FeedModal open={modal === 'feed'} onClose={() => setModal(null)} logs={logs} />
      <SlackModal open={modal === 'slack'} onClose={() => setModal(null)} onSaved={refresh} />
      <AddMemberModal open={modal === 'member'} onClose={() => setModal(null)} />
      <AddTaskModal open={modal === 'task'} onClose={() => setModal(null)} participants={participants} tasks={tasks} numbers={numbers} />
      <IntegrationsModal open={modal === 'import'} onClose={() => setModal(null)} participants={participants} tasks={tasks} canCorporate={auth.canCorporate} onCreateFromRepo={(url) => openCreate('github', url)} />
      <QRConnectModal open={modal === 'qr'} onClose={() => setModal(null)} participants={participants} />
    </SkinProvider>
  )
}
