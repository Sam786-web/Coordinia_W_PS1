import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BadgeCheck, Building2, CalendarClock, CircleAlert, Crown, Download, GitBranch, Github, GraduationCap, HeartPulse, ListChecks, Lock, OctagonAlert, Pencil, Plus, Search, Send, Trash2, Zap,
} from 'lucide-react'
import { Avatar, EmptyState, Spinner, StatusPill } from './ui'
import { useAuth } from '../lib/auth'
import { downloadReport } from '../lib/download'
import { useToast } from './Toast'
import { StatusDonut, WorkloadBars } from './TaskCharts'
import TaskRequestsInbox from './TaskRequestsInbox'
import ProjectsBoard from './ProjectsBoard'
import { useTerms } from '../lib/skin'
import { VerificationBadge, canUpdate, projectHealth } from '../lib/tasks'
import { DEADLINE_TONE, ICON, cx, formatDeadline } from '../lib/utils'

function dateRange() {
  const end = new Date()
  const start = new Date(end.getTime() - 6 * 86400000)
  const f = (d) => d.toLocaleDateString([], { month: 'short', day: 'numeric' })
  return `${f(start)} – ${f(end)}`
}

// A Team Lead with no projects yet (every new account starts here)
function WelcomeCard({ skin, isLead, onCreate }) {
  const corporate = skin === 'corporate'
  const steps = [
    ['bg-lavender', Github, 'Paste your GitHub repo', 'The AI reads it and splits the work into about two tasks per teammate.'],
    ['bg-butter', Crown, 'Hand out personal PINs', 'Every teammate gets a unique 6-digit PIN to sign in and verify on Telegram.'],
    ['bg-flamingo', Send, 'Let the coordinator run', 'Telegram check-ins, blocker relays and GitHub-verified progress.'],
  ]
  return (
    <section className="bento p-8">
      <p className="section-label">{corporate ? 'Corporate workspace' : 'College workspace'}</p>
      <h2 className="mt-2 text-2xl font-extrabold tracking-tight text-fg">Create your first {corporate ? 'sprint' : 'project'}</h2>
      <p className="mt-1 max-w-xl text-sm text-fg-2">
        This workspace is yours alone. Projects you create here are only visible to you and the teammates you invite.
      </p>
      <div className="mt-6 grid gap-3 md:grid-cols-3">
        {steps.map(([bg, Icon, title, body], i) => (
          <div key={title} className={cx('rounded-stat p-4 text-zinc-900', bg)}>
            <span className="flex items-center gap-2">
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-white/60">
                <Icon {...ICON} size={17} />
              </span>
              <span className="text-xs font-extrabold opacity-60">Step {i + 1}</span>
            </span>
            <p className="mt-3 text-sm font-extrabold">{title}</p>
            <p className="mt-0.5 text-xs font-medium text-zinc-700">{body}</p>
          </div>
        ))}
      </div>
      {isLead ? (
        <button className="btn-primary mt-6 px-6 py-3" onClick={onCreate}>
          <Github {...ICON} /> Paste GitHub Repo to start
        </button>
      ) : (
        <p className="mt-6 text-sm text-fg-2">Your Team Lead hasn't set up a project yet.</p>
      )}
    </section>
  )
}

/** Team Lead (College or Corporate): one click downloads the formatted PDF progress report. */
function ReportButton() {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const download = async () => {
    setBusy(true)
    try {
      const name = await downloadReport('pdf')
      toast.success('Progress report downloaded', name)
    } catch (e) {
      toast.error('Could not create the report', e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <button
      onClick={download}
      disabled={busy}
      className="inline-flex h-10 items-center gap-2 rounded-full bg-fg px-4 text-xs font-bold text-bg transition hover:opacity-90 disabled:opacity-60"
      title="Download the progress report (PDF): milestones, risks, handoffs and every task"
    >
      {busy ? <Spinner className="h-3.5 w-3.5" /> : <Download {...ICON} size={14} />} Download report
    </button>
  )
}

// Shown instead of the College/Corporate switch to anyone who can't change workspace
function WorkspaceBadge({ skin, lead }) {
  const corporate = skin === 'corporate'
  const Icon = corporate ? Building2 : GraduationCap
  return (
    <span
      className={cx(
        'inline-flex h-10 items-center gap-2 rounded-full px-3.5 text-xs font-bold',
        corporate ? 'bg-surface text-fg ring-1 ring-line' : 'bg-lavender text-[#1E1B4B]',
      )}
      title={
        !lead
          ? 'Your workspace is set by your Team Lead'
          : corporate
            ? 'Company Team Lead account: Corporate Workspace only'
            : 'College Team Lead account: College Workspace only. Corporate Workspace requires a Company Team Lead account'
      }
    >
      <Icon {...ICON} size={14} />
      Workspace: {corporate ? 'Corporate Sprint Mode' : 'College Capstone Mode'}
      {lead && <Lock {...ICON} size={12} className="opacity-60" />}
    </span>
  )
}

function Header({ query, setQuery, skin }) {
  const auth = useAuth()
  const inputRef = useRef(null)
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  const first = (auth.user?.name || 'there').split(' ')[0]
  const hour = new Date().getHours()
  const greet = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-3">
      <div className="min-w-[180px] flex-1">
        <h1 className="text-2xl font-extrabold tracking-tight text-fg">Hi, {first}</h1>
        <p className="mt-0.5 truncate text-sm text-fg-2">{greet}. Here's how your team is moving today.</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex h-10 w-52 items-center gap-2 rounded-full border border-line bg-surface px-3.5 focus-within:border-coral/60 focus-within:ring-2 focus-within:ring-coral/20 2xl:w-64">
          <Search {...ICON} className="shrink-0 text-fg-3" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search tasks, teammates, or PIN..."
            className="min-w-0 flex-1 bg-transparent text-sm font-medium text-fg outline-none placeholder:text-fg-3"
            aria-label="Search tasks, teammates, or PIN"
          />
          <kbd className="rounded-md border border-line bg-subtle px-1.5 py-0.5 text-[10px] font-bold text-fg-3">⌘K</kbd>
        </label>
        <span className="inline-flex h-10 items-center gap-2 rounded-full border border-line bg-surface pl-1.5 pr-3.5 text-xs font-bold text-fg">
          <Avatar name={auth.user?.name || '?'} size="sm" />
          {auth.isLead ? (
            <>
              <Crown {...ICON} size={13} className="text-coral" /> {auth.canCorporate ? 'Company Lead' : 'College Lead'}
            </>
          ) : (
            <>
              {auth.participant?.name}
              {auth.participant?.auth_pin && <span className="font-mono font-medium text-fg-3">• {auth.participant.auth_pin}</span>}
            </>
          )}
        </span>
        <WorkspaceBadge skin={skin} lead={auth.isLead} />
        {auth.isLead && <ReportButton />}
      </div>
    </header>
  )
}

function PulseCard({ tasks, participants, completion, project, isLead, onSyncGithub, syncing, onTakeUpdates, sending }) {
  const bars = participants.slice(0, 7).map((p) => {
    const mine = tasks.filter((t) => t.owner_id === p.id)
    const done = mine.filter((t) => t.status === 'Complete').length
    const active = mine.filter((t) => t.status === 'In Progress').length
    const pct = mine.length ? Math.round((100 * (done + 0.5 * active)) / mine.length) : 0
    return { p, pct, done, total: mine.length }
  })
  return (
    <section className="bento col-span-12 flex flex-col p-5 lg:col-span-7" aria-label="Project pulse and member activity">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-bold text-fg-2">Sync Pulse</p>
          <p className="mt-1 flex items-center gap-2 text-[28px] font-extrabold leading-none tracking-tight text-fg">
            <HeartPulse {...ICON} size={22} className="text-capsule" /> {completion}% Sync
          </p>
          <p className="mt-0.5 text-xs font-semibold text-fg-3">{dateRange()}</p>
        </div>
        {isLead && (
          <div className="flex gap-2">
            {project?.github_repo_url && (
              <button className="pill-btn" onClick={onSyncGithub} disabled={syncing} title="Match recent commits to tasks">
                {syncing ? <Spinner className="h-3.5 w-3.5" /> : <Github {...ICON} size={14} />} Sync GitHub
              </button>
            )}
            <button className="pill-btn" onClick={onTakeUpdates} disabled={sending} title="Ask everyone with open tasks for an update">
              {sending ? <Spinner className="h-3.5 w-3.5" /> : <Send {...ICON} size={14} />} Ping Telegram
            </button>
          </div>
        )}
      </div>
      <div className={cx('mt-4 flex flex-1 items-end gap-3', bars.length < 7 ? 'justify-around' : 'justify-between')}>
        {bars.map(({ p, pct, done, total }) => (
          <div key={p.id} className="group flex flex-col items-center gap-1.5" title={`${p.name}: ${pct}% (${done} of ${total} tasks done)`}>
            <span className="text-[11px] font-extrabold tabular-nums text-fg-3 transition group-hover:text-capsule">{pct}%</span>
            <div className="relative flex h-24 w-7 items-end overflow-hidden rounded-full bg-[#FFE4E6] transition group-hover:scale-105 dark:bg-rose-500/15 2xl:h-28">
              <div className="w-full rounded-full bg-capsule transition-all duration-500" style={{ height: `${Math.max(pct, total ? 8 : 0)}%` }} />
            </div>
            <span className="max-w-[64px] truncate text-xs font-bold text-fg-2">{p.name.split(' ')[0]}</span>
          </div>
        ))}
        {!bars.length && <p className="w-full pb-10 text-center text-sm text-fg-3">Add teammates to see their activity.</p>}
      </div>
    </section>
  )
}

function StatCard({ value, label, color, text, icon: Icon, active, onClick }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      title={`Show ${label.toLowerCase()} in the task list`}
      className={cx(
        'flex flex-1 items-center justify-between rounded-stat px-4 py-3 text-left transition hover:-translate-y-0.5 hover:shadow-md',
        color, text, active && 'ring-2 ring-fg/80 ring-offset-2 ring-offset-shell',
      )}
    >
      <div>
        <p className="text-[28px] font-extrabold leading-none tracking-tight">{value}</p>
        <p className="mt-1 text-[13px] font-bold opacity-80">{label}</p>
      </div>
      <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-white/60 shadow-[inset_0_1px_0_rgba(255,255,255,0.9)]">
        <Icon {...ICON} size={20} />
      </span>
    </button>
  )
}

const FILTERS = [
  ['all', 'All', () => true],
  ['active', 'Active', (t) => t.status === 'In Progress' || t.status === 'Complete'],
  ['Blocked', 'Blocked', (t) => t.status === 'Blocked'],
  ['Pending', 'Pending', (t) => t.status === 'Pending'],
  ['Complete', 'Done', (t) => t.status === 'Complete'],
  ['review', 'Needs review', (t) => t.status === 'Complete' && t.proof === 'self'],
]

function HealthSpectrum({ health, level }) {
  // Blue (left) is calm, rose (right) is hot: the marker sits at the risk level, the tooltip shows health
  const pos = Math.min(97, Math.max(3, 100 - health))
  return (
    <section className="bento flex flex-col gap-4 px-5 py-4 md:flex-row md:items-center md:gap-8" aria-label="Project velocity and dependency health">
      <div className="shrink-0 text-sm md:w-56">
        <p className="font-bold leading-snug text-fg">Average project velocity & dependency health</p>
        <p className="mt-0.5 font-semibold text-fg-2">
          Level: <span className={cx('font-extrabold', level === 'Healthy' ? 'text-emerald-600 dark:text-emerald-400' : level === 'At Risk' ? 'text-amber-600 dark:text-amber-400' : 'text-rose-600 dark:text-rose-400')}>{level}</span>
        </p>
      </div>
      <div
        className="relative mt-7 h-4 flex-1 rounded-full md:mt-6"
        style={{ background: 'linear-gradient(90deg, #60A5FA 0%, #A3E635 36%, #FACC15 66%, #F43F5E 100%)' }}
        role="meter" aria-valuenow={health} aria-valuemin={0} aria-valuemax={100} aria-label="Project health">
        <div className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 transition-all duration-500" style={{ left: `${pos}%` }}>
          <span className="absolute -top-9 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-zinc-900 px-3 py-1 text-xs font-bold text-white shadow-md dark:bg-white dark:text-zinc-900">
            Health {health}%
          </span>
          <span className="block h-6 w-6 rounded-full border-4 border-white bg-zinc-900 shadow-md" />
        </div>
      </div>
    </section>
  )
}

function TaskRow({ task, numbers, riskRoot, pinOf, onPing, onUpdate, onEdit }) {
  const auth = useAuth()
  const [pinging, setPinging] = useState(false)
  const allowed = canUpdate(task, auth)
  const blocked = task.status === 'Blocked'
  const atRisk = !!riskRoot && !blocked
  const pin = pinOf(task.owner_id)
  const due = formatDeadline(task.deadline, task.status === 'Complete')
  return (
    <article
      id={`task-${task.id}`}
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-row border border-line bg-surface px-4 py-3 transition hover:border-line-strong hover:shadow-sm 2xl:grid 2xl:grid-cols-[minmax(0,2.1fr)_minmax(0,1.1fr)_minmax(0,1.6fr)_auto_auto] 2xl:gap-4"
    >
      <div className="flex min-w-0 flex-1 basis-[200px] items-center gap-3">
        <Avatar name={task.owner_name || '?'} size="lg" />
        <div className="min-w-0">
          <p className="truncate text-sm font-extrabold text-fg" title={task.title}>
            <span className="mr-1 font-semibold text-fg-3">#{numbers[task.id]}</span>
            {task.title}
          </p>
          <p className="truncate text-xs font-semibold text-fg-2">
            {task.owner_name || 'Unassigned'}
            {pin && <span className="font-mono text-fg-3"> • PIN: {pin}</span>}
            {due.label && (
              <span className={cx('ml-1.5 inline-flex items-center gap-1 rounded-full px-2 py-px align-middle text-[10px] font-bold', DEADLINE_TONE[due.state])} title="Deadline">
                <CalendarClock {...ICON} size={10} /> {due.state === 'overdue' ? 'Overdue · ' : ''}{due.label}
              </span>
            )}
          </p>
        </div>
      </div>
      {/* Below 2xl this wraps onto a second line; at 2xl `contents` drops it into the grid columns */}
      <div className="order-last flex w-full min-w-0 items-center gap-3 pl-[52px] 2xl:contents">
      <div className="shrink-0 2xl:min-w-0">
        <VerificationBadge task={task} numbers={numbers} />
      </div>
      <p className="min-w-0 flex-1 truncate text-xs font-semibold text-fg-2" title={blocked ? task.blocker_reason : task.progress_note}>
        {blocked ? (
          <span className="inline-flex items-center gap-1 text-rose-600 dark:text-rose-400">
            <Lock {...ICON} size={12} /> Blocker: {task.blocker_reason}
          </span>
        ) : atRisk ? (
          <span className="inline-flex items-center gap-1 text-orange-600 dark:text-orange-400">
            <GitBranch {...ICON} size={12} /> Paused until #{numbers[riskRoot.id]} is unblocked
          </span>
        ) : (
          task.progress_note || 'No update yet'
        )}
      </p>
      </div>
      <StatusPill status={atRisk ? 'At Risk' : task.status} />
      <div className="flex items-center gap-1.5 2xl:justify-end">
        {auth.isLead && task.status !== 'Complete' && (
          <button
            className="rounded-full border border-line px-4 py-1.5 text-xs font-bold text-fg transition hover:scale-105 disabled:opacity-50"
            disabled={pinging || !task.owner_id}
            onClick={async () => {
              setPinging(true)
              try {
                await onPing(task)
              } finally {
                setPinging(false)
              }
            }}
          >
            {pinging ? <Spinner className="h-3.5 w-3.5" /> : 'Ping'}
          </button>
        )}
        <button
          className={cx('inline-flex items-center gap-1 rounded-full border px-4 py-1.5 text-xs font-bold transition', allowed ? 'border-line text-fg hover:scale-105' : 'cursor-not-allowed border-dashed border-line text-fg-3')}
          onClick={() => allowed && onUpdate(task)}
          title={allowed ? 'Update this task' : `Only ${task.owner_name || 'the owner'} or the Team Lead can update this task`}
          aria-disabled={!allowed}
        >
          {!allowed && <Lock {...ICON} size={11} />} Update
        </button>
        {auth.isLead && onEdit && (
          <>
            <button className="icon-btn h-8 w-8" onClick={() => onEdit(task)} title="Edit task (title, description, owner)" aria-label={`Edit ${task.title}`}>
              <Pencil {...ICON} size={14} />
            </button>
            <button
              className="icon-btn h-8 w-8 hover:text-rose-600 dark:hover:text-rose-400"
              onClick={() => onEdit(task, true)}
              title="Delete task"
              aria-label={`Delete ${task.title}`}
            >
              <Trash2 {...ICON} size={14} />
            </button>
          </>
        )}
      </div>
    </article>
  )
}

export default function DashboardView({
  state, tasks, participants, numbers, risk, skin, onSyncGithub, syncing, onTakeUpdates, sending, onPing, onUpdate, onEdit, onNewTask, onCreate, onDeadlines, onRequest, version,
}) {
  const auth = useAuth()
  const terms = useTerms()
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const listRef = useRef(null)
  const { health, completion, level, verified, claimed, trulyDone } = projectHealth(tasks, risk)
  const pins = useMemo(() => Object.fromEntries(participants.map((p) => [p.id, p.auth_pin])), [participants])
  const q = query.trim().toLowerCase()
  const test = (FILTERS.find(([k]) => k === filter) || FILTERS[0])[2]
  const shown = tasks
    .filter(test)
    .filter((t) => !q || [t.title, t.owner_name, pins[t.owner_id], t.progress_note, t.blocker_reason].filter(Boolean).some((s) => String(s).toLowerCase().includes(q)))
  const count = (s) => tasks.filter((t) => t.status === s).length
  if (!state.active_project) {
    return (
      <div className="space-y-4">
        <Header query={query} setQuery={setQuery} skin={skin} />
        <WelcomeCard skin={skin} isLead={auth.isLead} onCreate={onCreate} />
      </div>
    )
  }
  const pick = (k) => {
    setFilter((cur) => (cur === k && k !== 'all' ? 'all' : k))
    listRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="space-y-4">
      <Header query={query} setQuery={setQuery} skin={skin} />

      {auth.isLead && <TaskRequestsInbox requests={state.task_requests} />}

      {auth.isLead && <ProjectsBoard version={version} onCreate={onCreate} />}

      <div className="grid grid-cols-12 gap-4">
        <PulseCard
          tasks={tasks}
          participants={participants}
          completion={completion}
          project={state.active_project}
          isLead={auth.isLead}
          onSyncGithub={onSyncGithub}
          syncing={syncing}
          onTakeUpdates={onTakeUpdates}
          sending={sending}
        />
        <div className="col-span-12 flex flex-col gap-3 lg:col-span-5">
          <StatCard value={tasks.length} label="Total Project Tasks" color="bg-lavender" text="text-[#1E1B4B]" icon={ListChecks} onClick={() => pick('all')} />
          <StatCard value={count('In Progress') + count('Complete')} label="Active & Synced" color="bg-butter" text="text-[#422006]" icon={Zap} active={filter === 'active'} onClick={() => pick('active')} />
          <StatCard value={count('Blocked')} label="Blocked / Waiting" color="bg-flamingo" text="text-[#831843]" icon={OctagonAlert} active={filter === 'Blocked'} onClick={() => pick('Blocked')} />
        </div>
      </div>

      {tasks.length > 0 && (
        <div className="grid grid-cols-12 gap-4">
          <StatusDonut tasks={tasks} />
          <WorkloadBars tasks={tasks} participants={participants} />
        </div>
      )}

      <HealthSpectrum health={health} level={level} />

      {tasks.length > 0 && (trulyDone || claimed > 0) && (
        <button
          onClick={() => !trulyDone && pick('review')}
          className={cx(
            'flex w-full items-center gap-3 rounded-bento px-5 py-3.5 text-left text-sm transition',
            trulyDone ? 'cursor-default bg-mint text-emerald-950' : 'bg-butter text-amber-950 hover:brightness-[.98]',
          )}
        >
          {trulyDone ? <BadgeCheck {...ICON} size={20} /> : <CircleAlert {...ICON} size={20} />}
          <span className="min-w-0 flex-1">
            <span className="block font-extrabold">
              {trulyDone ? 'Verified done: every task is backed by proof' : `${claimed} task${claimed === 1 ? ' is' : 's are'} marked done without proof`}
            </span>
            <span className="block text-xs font-semibold opacity-80">
              {trulyDone
                ? 'Each completion has a GitHub commit, a working link or your approval.'
                : 'They count half toward progress until there is a commit, a working link or your approval. Click to review.'}
            </span>
          </span>
        </button>
      )}

      <section ref={listRef} className="bento scroll-mt-2 p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
          <div>
            <h2 className="text-base font-extrabold tracking-tight text-fg">{terms.tasks}</h2>
            <p className="text-xs font-semibold text-fg-3">
              {state.active_project?.name} · {terms.workspace}
              {state.active_project?.due_at && ` · Final deadline ${formatDeadline(state.active_project.due_at).label}`}
              {tasks.length > 0 && ` · ${verified}/${tasks.length} verified`}
              {claimed > 0 && ` · ${claimed} need${claimed === 1 ? 's' : ''} proof`}
              {q && ` · ${shown.length} match${shown.length === 1 ? '' : 'es'}`}
            </p>
          </div>
          {auth.isLead && (
            <div className="flex items-center gap-3 text-sm font-bold">
              <button className="inline-flex items-center gap-1 text-coral hover:underline" onClick={onDeadlines} title="Set the project deadline and spread task deadlines">
                <CalendarClock {...ICON} size={14} /> Deadlines
              </button>
              <button className="inline-flex items-center gap-1 text-coral hover:underline" onClick={onNewTask}>
                <Plus {...ICON} size={14} /> Add Task
              </button>
              <button className="inline-flex items-center gap-1 text-coral hover:underline" onClick={onCreate}>
                <Plus {...ICON} size={14} /> Paste GitHub Repo
              </button>
            </div>
          )}
          {!auth.isLead && (
            <button className="inline-flex items-center gap-1 text-sm font-bold text-coral hover:underline" onClick={onRequest} title="Ask your Team Lead to add a task for a teammate">
              <Plus {...ICON} size={14} /> Request a task
            </button>
          )}
        </div>
        {tasks.length > 0 && (
          <div className="mb-3 flex gap-1.5 overflow-x-auto px-1 pb-0.5" role="tablist" aria-label="Filter tasks by status">
            {FILTERS.map(([k, label, fn]) => {
              const n = tasks.filter(fn).length
              const on = filter === k
              return (
                <button
                  key={k}
                  role="tab"
                  aria-selected={on}
                  onClick={() => setFilter(k)}
                  className={cx(
                    'inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-bold transition',
                    on ? 'bg-fg text-bg' : 'bg-surface text-fg-2 ring-1 ring-line hover:text-fg',
                  )}
                >
                  {label}
                  <span className={cx('rounded-full px-1.5 text-[10px] tabular-nums', on ? 'bg-bg/20' : 'bg-subtle')}>{n}</span>
                </button>
              )
            })}
          </div>
        )}
        <div className="space-y-2">
          {shown.map((t) => (
            <TaskRow key={t.id} task={t} numbers={numbers} riskRoot={risk[t.id]} pinOf={(id) => pins[id]} onPing={onPing} onUpdate={onUpdate} onEdit={onEdit} />
          ))}
          {!tasks.length && (
            <EmptyState
              icon={ListChecks}
              title="No tasks yet"
              body="Paste a GitHub repo and the AI will split it into tasks for your team."
              action={auth.isLead && (
                <button className="btn-primary" onClick={onCreate}>
                  <Github {...ICON} /> Paste GitHub Repo
                </button>
              )}
            />
          )}
          {tasks.length > 0 && !shown.length && (
            <p className="py-6 text-center text-sm text-fg-3">
              {q ? `Nothing matches "${query}".` : 'No tasks in this view.'}{' '}
              <button className="font-bold text-coral hover:underline" onClick={() => { setFilter('all'); setQuery('') }}>Show all</button>
            </p>
          )}
        </div>
      </section>
    </div>
  )
}
