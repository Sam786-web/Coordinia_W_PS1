import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CalendarClock, Check, ChevronDown, CircleAlert, CircleCheck, Clock, Flag, GitBranch, Headphones, ListChecks, MessageSquare,
  Link2, LogOut, Mic, Play, Send, Timer,
} from 'lucide-react'
import { Avatar, Dot, Logo, Spinner, StatusPill } from './ui'
import { useToast } from './Toast'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { VerificationBadge } from '../lib/tasks'
import { conversationFor } from '../lib/conversation'
import { availabilityLabel, clockTime, cx, detectMode, extractNeed, formatDeadline, isFreeNow } from '../lib/utils'

const I = { size: 16, strokeWidth: 1.75 }
const MEMBER_KEY = 'coordina-mobile-member'

const PHRASES = {
  college: ['API connected, waiting for test credentials from Meera', 'Shared the test credentials with Kabir', 'Finished testing, task is complete'],
  enterprise: ['Webhook connected, waiting for the KMS keys from Priya', 'Shared the production KMS keys with Rohan', 'Finished testing, task is complete'],
  custom: ['Started working on my task', 'Blocked, waiting on a teammate', 'Finished testing, task is complete'],
}

const AVAILABILITY = [
  { value: 'Available now', label: 'Available now', icon: CircleCheck },
  { value: 'Free in 30 mins', label: 'Free in 30 minutes', icon: Timer },
  { value: 'Free at 6 PM', label: 'Free after 6:00 PM', icon: Clock },
  { value: 'Async only', label: 'Heads-down (Async only)', icon: Headphones },
]

function readMember() {
  try {
    return localStorage.getItem(MEMBER_KEY) || ''
  } catch {
    return ''
  }
}

function titleCase(s = '') {
  return s.replace(/\b\w/g, (c) => c.toUpperCase())
}

function selectedAvailability(av) {
  const a = (av || 'Available').toLowerCase()
  if (['available', 'available now', 'free now'].includes(a)) return 'Available now'
  if (a.startsWith('async')) return 'Async only'
  if (/^free (at|after) 6(:00)?\s*pm$/.test(a)) return 'Free at 6 PM'
  if (a.startsWith('free at')) return 'Free in 30 mins'
  return null
}

/** POST a teammate's message through the coordinator brain. */
function useSender(me, toast) {
  const [busy, setBusy] = useState(null)
  const send = async (key, message, taskId) => {
    setBusy(key)
    try {
      const out = await api.post('/api/simulate-reply', { participant_id: me.id, task_id: taskId ?? null, message, source: 'android' })
      return out.result
    } catch (e) {
      toast.error('Could not send update', e.message)
      return null
    } finally {
      setBusy(null)
    }
  }
  // "Blocked" always marks the task Blocked; the coordinator relays to the named teammate or alerts the team
  const blocked = async (key, text, taskId) => {
    setBusy(key)
    try {
      const out = await api.post('/api/mobile/quick-update', { participant_id: me.id, task_id: taskId, action_type: 'SOS_BLOCKER', text })
      return out.result
    } catch (e) {
      toast.error('Could not report the blocker', e.message)
      return null
    } finally {
      setBusy(null)
    }
  }
  return { busy, send, blocked }
}

// ---------------- Header & banner ----------------

/** Telegram deep link that links this phone to the member (with the project's invite code when known). */
export function telegramLink(botUsername, me) {
  if (!botUsername || !me) return null
  // The member's own PIN verifies this phone in one tap; without it, open the bot and use /auth <PIN>
  return me.auth_pin ? `https://t.me/${botUsername}?start=auth_${encodeURIComponent(me.auth_pin)}` : `https://t.me/${botUsername}`
}

function Header({ me, participants, onSelect, live, botUsername, embedded, locked, project }) {
  const toast = useToast()
  const tgLink = telegramLink(botUsername, me, project)
  return (
    <header className={cx('flex items-center gap-3 border-b border-line bg-surface px-4 pb-3', embedded ? 'pt-9' : 'pt-[calc(12px+env(safe-area-inset-top))]')}>
      {locked ? (
        <div className="min-w-0 flex-1">
          <span className="block text-[11px] text-fg-3">Signed in as</span>
          <span className="block truncate text-base font-semibold text-fg">{me?.name}</span>
        </div>
      ) : (
        <label className="relative min-w-0 flex-1">
          <span className="block text-[11px] text-fg-3">Viewing as</span>
          <select
            value={me?.name || ''}
            onChange={(e) => onSelect(e.target.value)}
            className="w-full min-h-[28px] cursor-pointer appearance-none truncate bg-transparent pr-6 text-base font-semibold text-fg outline-none"
            aria-label="Viewing as"
          >
            {participants.map((p) => (
              <option key={p.id} value={p.name}>
                {p.name}
              </option>
            ))}
          </select>
          <ChevronDown {...I} size={15} className="pointer-events-none absolute bottom-1.5 right-0 text-fg-3" />
        </label>
      )}
      <span className="flex shrink-0 items-center gap-1.5 text-xs text-fg-3">
        <Dot className={live ? 'bg-emerald-500' : 'bg-amber-500'} />
        {live ? 'Synced' : 'Offline'}
      </span>
      {tgLink ? (
        <a href={tgLink} target="_blank" rel="noreferrer" className="btn min-h-[40px] shrink-0 px-3">
          <Send {...I} /> Open Telegram
        </a>
      ) : (
        <button type="button" className="btn min-h-[40px] shrink-0 px-3 text-fg-2" onClick={() => toast.info('Telegram bot not connected', 'Ask the team to add a bot token on the laptop.')}>
          <Send {...I} /> Open Telegram
        </button>
      )}
    </header>
  )
}

function WaitingBanner({ items, me, sender }) {
  if (!items.length) return null
  return (
    <div className="space-y-3 px-4 pt-4">
      {items.map(({ task, owner, need }) => (
        <section key={task.id} className="rounded-xl border border-rose-500/25 bg-rose-500/10 p-4" aria-live="polite">
          <p className="flex items-start gap-2 text-sm font-semibold text-fg">
            <CircleAlert {...I} className="mt-0.5 shrink-0 text-rose-600 dark:text-rose-400" />
            {owner} is blocked on {task.title}
          </p>
          <p className="mt-1 pl-6 text-sm text-fg-2">
            Waiting on you for: <span className="font-medium text-fg">{need ? titleCase(need) : task.blocker_reason}</span>
          </p>
          <button
            className="btn-primary mt-3 min-h-[44px] w-full"
            disabled={sender.busy === `unblock-${task.id}`}
            onClick={() => sender.send(`unblock-${task.id}`, `Shared the ${need || 'details'} with ${owner}`)}
          >
            {sender.busy === `unblock-${task.id}` ? <Spinner /> : <Check {...I} />}
            Send {need ? titleCase(need) : 'It'} & Unblock
          </button>
        </section>
      ))}
    </div>
  )
}

// ---------------- Tab 1: My Tasks ----------------

function DeliverableForm({ task, toast }) {
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  if (task.status === 'Complete' || task.task_type !== 'design_docs') return null
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const out = await api.post(`/api/tasks/${task.id}/deliverable`, { url: url.trim() })
      toast.success(out.completed ? 'Deliverable submitted' : 'Link saved', out.completed ? 'Task marked Complete.' : 'A task this depends on is still blocked.')
      setOpen(false)
      setUrl('')
    } catch (err) {
      toast.error('Could not submit the link', err.message)
    } finally {
      setBusy(false)
    }
  }
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="mt-3 flex min-h-[40px] w-full items-center justify-center gap-1.5 rounded-ctl border border-dashed border-line text-sm text-fg-2 hover:bg-subtle">
        <Link2 {...I} size={15} /> Add deliverable link (slides, Figma, doc)
      </button>
    )
  }
  return (
    <form onSubmit={submit} className="mt-3 flex gap-2">
      <input className="input min-h-[44px] text-base" type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" autoFocus />
      <button className="btn-primary min-h-[44px] shrink-0 px-3" disabled={busy || !url.trim()}>
        {busy ? <Spinner /> : 'Submit'}
      </button>
    </form>
  )
}

function TaskItem({ task, byId, sender, warning, onWarning, toast }) {
  const [reporting, setReporting] = useState(false)
  const [issue, setIssue] = useState('')
  const upstream = byId[task.depends_on_task_id]
  const waiting = upstream && upstream.status !== 'Complete'
  const done = task.status === 'Complete'
  const busy = sender.busy?.endsWith(`-${task.id}`)

  const act = async (kind, message) => {
    const key = `${kind}-${task.id}`
    const r = kind === 'issue' ? await sender.blocked(key, message, task.id) : await sender.send(key, message, task.id)
    if (!r) return
    onWarning(task.id, r.contradiction_warning || null)
    if (r.contradiction_warning) toast.info('Not marked done yet', 'A task this depends on is still blocked.')
    else if (r.auto_relay_target_name) toast.success(`Asked ${r.auto_relay_target_name} for help`, 'Your task is marked Blocked.')
    else if (kind === 'issue') toast.success('Blocker reported', 'Your team has been alerted.')
    else toast.success('Update sent', `Status: ${r.new_status}`)
    return r
  }

  return (
    <article className="rounded-card border border-line bg-surface p-4">
      <div className="flex items-center gap-2">
        <StatusPill status={task.status} />
        <VerificationBadge task={task} />
        {task.deadline && (
          <span
            className={cx('ml-auto flex items-center gap-1 text-xs',
              { overdue: 'font-bold text-rose-600 dark:text-rose-400', soon: 'font-bold text-amber-600 dark:text-amber-400' }[formatDeadline(task.deadline, task.status === 'Complete').state] || 'text-fg-3')}
          >
            <Clock {...I} size={13} /> {formatDeadline(task.deadline, task.status === 'Complete').state === 'overdue' ? 'Overdue · ' : ''}
            {formatDeadline(task.deadline).label}
          </span>
        )}
      </div>
      <h3 className="mt-2.5 text-base font-semibold leading-snug text-fg">{task.title}</h3>
      {waiting && (
        <p className="mt-1.5 flex items-start gap-1.5 text-sm text-amber-700 dark:text-amber-400">
          <GitBranch {...I} size={14} className="mt-0.5 shrink-0" />
          Waiting on: {upstream.title}
          {upstream.owner_name ? ` (${upstream.owner_name})` : ''}
        </p>
      )}
      <p className="mt-2 text-sm text-fg-2">{task.progress_note || 'No update yet'}</p>
      <DeliverableForm task={task} toast={toast} />

      {task.status === 'Blocked' && (
        <p className="mt-3 rounded-lg border border-rose-500/20 bg-rose-500/10 p-3 text-sm text-fg">
          <span className="font-medium text-rose-700 dark:text-rose-400">Blocker: </span>
          {task.blocker_reason}
          {task.blocked_by_name && <span className="text-fg-2"> · waiting on {task.blocked_by_name}</span>}
        </p>
      )}
      {warning && <p className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-300">{warning}</p>}

      {done ? (
        <p className="flex items-center gap-1.5 pt-3 text-sm text-emerald-700 dark:text-emerald-400">
          <CircleCheck {...I} /> Completed
        </p>
      ) : reporting ? (
        <form
          className="space-y-2 pt-3"
          onSubmit={async (e) => {
            e.preventDefault()
            if (!issue.trim()) return
            if (await act('issue', issue.trim())) {
              setIssue('')
              setReporting(false)
            }
          }}
        >
          <textarea
            autoFocus
            rows={3}
            className="input resize-none text-base"
            value={issue}
            onChange={(e) => setIssue(e.target.value)}
            placeholder="What are you waiting on, and which teammate can help?"
          />
          <div className="grid grid-cols-2 gap-2">
            <button type="button" className="btn min-h-[44px]" onClick={() => setReporting(false)}>
              Cancel
            </button>
            <button className="btn-primary min-h-[44px]" disabled={busy || !issue.trim()}>
              {sender.busy === `issue-${task.id}` ? <Spinner /> : <Flag {...I} />} Mark blocked
            </button>
          </div>
        </form>
      ) : (
        <div className="grid grid-cols-3 gap-2 pt-3">
          <button
            className="btn min-h-[44px] flex-col gap-1 px-1 py-2 text-xs"
            disabled={busy || task.status === 'In Progress'}
            onClick={() => act('progress', 'Started working on this task')}
          >
            {sender.busy === `progress-${task.id}` ? <Spinner /> : <Play {...I} />} In Progress
          </button>
          <button className="btn min-h-[44px] flex-col gap-1 px-1 py-2 text-xs" disabled={busy} onClick={() => setReporting(true)}>
            <Flag {...I} /> Blocked
          </button>
          <button className="btn-primary min-h-[44px] flex-col gap-1 px-1 py-2 text-xs" disabled={busy} onClick={() => act('done', 'Marked complete from the mobile app')}>
            {sender.busy === `done-${task.id}` ? <Spinner /> : <Check {...I} />} Done
          </button>
        </div>
      )}
    </article>
  )
}

function MyTasks({ me, tasks, sender, toast, tgLink }) {
  const [warnings, setWarnings] = useState({})
  const byId = useMemo(() => Object.fromEntries(tasks.map((t) => [t.id, t])), [tasks])
  const mine = tasks.filter((t) => t.owner_id === me.id)
  const order = { Blocked: 0, 'In Progress': 1, Pending: 2, Complete: 3 }
  mine.sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9))
  return (
    <div className="space-y-3 p-4">
      {!(me.is_verified && me.telegram_chat_id) && tgLink && (
        <a href={tgLink} target="_blank" rel="noreferrer" className="flex min-h-[56px] items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3 hover:bg-subtle">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-sky-500/15 text-sky-600 dark:text-sky-400">
            <Send {...I} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[15px] font-medium text-fg">Connect Telegram</span>
            <span className="block text-xs text-fg-3">Get reminders and reply to the coordinator from Telegram</span>
          </span>
          <ChevronDown {...I} className="-rotate-90 text-fg-3" />
        </a>
      )}
      <h2 className="text-sm font-medium text-fg-2">
        {mine.filter((t) => t.status !== 'Complete').length} open task{mine.filter((t) => t.status !== 'Complete').length === 1 ? '' : 's'}
      </h2>
      {mine.map((t) => (
        <TaskItem
          key={t.id}
          task={t}
          byId={byId}
          sender={sender}
          toast={toast}
          warning={warnings[t.id]}
          onWarning={(id, w) => setWarnings((x) => ({ ...x, [id]: w }))}
        />
      ))}
      {!mine.length && <p className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-fg-3">No tasks assigned to {me.name} yet.</p>}
    </div>
  )
}

// ---------------- Tab 2: Send Update ----------------

function SendUpdate({ me, logs, mode, sender, toast }) {
  const [text, setText] = useState('')
  const [listening, setListening] = useState(false)
  const recRef = useRef(null)
  const baseRef = useRef('')
  const endRef = useRef(null)
  const items = useMemo(() => conversationFor(logs, me.name), [logs, me.name])
  const SR = typeof window !== 'undefined' ? window.SpeechRecognition || window.webkitSpeechRecognition : null

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [items.length])
  useEffect(() => () => recRef.current?.abort(), [])

  const [choice, setChoice] = useState(null) // { message, options } when the coordinator asks which task

  const submit = async (message, taskId = null) => {
    if (!message.trim()) return
    recRef.current?.stop()
    const r = await sender.send('chat', message.trim(), taskId)
    if (!r) return
    setText('')
    if (r.needs_task_choice) {
      setChoice({ message: message.trim(), options: r.task_options })
      return
    }
    setChoice(null)
    if (r.contradiction_warning) toast.info('Not marked complete yet', 'A task this depends on is still open.')
    else if (r.unblocked_downstream_task_id) toast.success('Teammate unblocked')
    else if (r.auto_relay_target_name) toast.success(`Asked ${r.auto_relay_target_name} for help`)
    else toast.success('Update sent', `Status: ${r.new_status}`)
  }

  const toggleMic = () => {
    if (listening) return recRef.current?.stop()
    if (!SR) return toast.info('Voice input not supported', 'Use Chrome on Android for dictation.')
    if (!window.isSecureContext) {
      return toast.info('Voice input needs a secure connection', 'Open the app over HTTPS, or allow this address in chrome://flags (Insecure origins treated as secure).')
    }
    const rec = new SR()
    rec.lang = 'en-IN' // understands English and Hinglish
    rec.interimResults = true
    rec.continuous = false
    baseRef.current = text ? text.trimEnd() + ' ' : ''
    rec.onresult = (e) => setText(baseRef.current + Array.from(e.results).map((r) => r[0].transcript).join(' '))
    rec.onerror = (e) => {
      setListening(false)
      if (e.error === 'not-allowed') toast.error('Microphone blocked', 'Allow microphone access for this site in Chrome settings.')
    }
    rec.onend = () => setListening(false)
    recRef.current = rec
    rec.start()
    setListening(true)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {!items.length && <p className="pt-10 text-center text-sm text-fg-3">No messages yet. Send your first update below.</p>}
        {items.map((it) =>
          it.kind === 'in' ? (
            <div key={it.key} className="flex flex-col items-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-md bg-indigo-600 px-3.5 py-2 text-[15px] text-white">{it.text}</div>
              <span className="mt-1 text-[11px] text-fg-3">
                {it.via} · {clockTime(it.ts)}
              </span>
            </div>
          ) : (
            <div key={it.key} className="flex gap-2">
              <Logo className="mt-0.5 h-7 w-7 shrink-0" />
              <div className="min-w-0 max-w-[85%]">
                <div className="rounded-2xl rounded-tl-md bg-surface px-3.5 py-2 text-[15px] text-fg ring-1 ring-line whitespace-pre-line">{it.text}</div>
                <span className="mt-1 block text-[11px] text-fg-3">Coordinator · {clockTime(it.ts)}</span>
              </div>
            </div>
          ),
        )}
        {choice && (
          <div className="flex gap-2">
            <Logo className="mt-0.5 h-7 w-7 shrink-0" />
            <div className="min-w-0 max-w-[85%] rounded-2xl rounded-tl-md bg-surface px-3.5 py-2.5 ring-1 ring-line">
              <p className="text-[15px] text-fg">Which task is this about?</p>
              <div className="mt-2 space-y-1.5">
                {choice.options.map((o) => (
                  <button
                    key={o.id}
                    disabled={sender.busy === 'chat'}
                    onClick={() => submit(choice.message, o.id)}
                    className="flex min-h-[44px] w-full items-center rounded-ctl border border-line px-3 text-left text-sm text-fg hover:bg-subtle"
                  >
                    {o.title}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="border-t border-line bg-surface px-3 pb-2 pt-2.5">
        <div className="-mx-3 mb-2 flex gap-2 overflow-x-auto px-3 pb-0.5">
          {(PHRASES[mode] || PHRASES.custom).map((p) => (
            <button key={p} type="button" onClick={() => setText(p)} className="min-h-[36px] shrink-0 rounded-full border border-line px-3 text-sm text-fg-2 hover:bg-subtle">
              {p}
            </button>
          ))}
        </div>
        {listening && (
          <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-rose-600 dark:text-rose-400" role="status">
            <span className="h-2 w-2 animate-pulse rounded-full bg-rose-500" /> Listening…
          </p>
        )}
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            submit(text)
          }}
        >
          <input
            className="input min-h-[44px] text-base"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Write a quick status update..."
            enterKeyHint="send"
          />
          <button
            type="button"
            onClick={toggleMic}
            aria-label={listening ? 'Stop dictation' : 'Dictate an update'}
            aria-pressed={listening}
            className={cx(
              'inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg border transition-colors',
              listening ? 'border-rose-500/40 bg-rose-500/10 text-rose-600 dark:text-rose-400' : 'border-line text-fg-2 hover:bg-subtle',
            )}
          >
            <Mic {...I} size={18} />
          </button>
          <button className="btn-primary min-h-[44px] min-w-[44px] px-0" disabled={sender.busy === 'chat' || !text.trim()} aria-label="Send message">
            {sender.busy === 'chat' ? <Spinner /> : <Send {...I} size={18} />}
          </button>
        </form>
      </div>
    </div>
  )
}

// ---------------- Tab 3: Availability ----------------

function Availability({ me, participants, version, embedded, onSignOut, toast }) {
  const [saving, setSaving] = useState(null)
  const [sync, setSync] = useState(null)
  const current = selectedAvailability(me.availability)

  useEffect(() => {
    api
      .get(`/api/mobile/my-dashboard/${me.id}`)
      .then((d) => setSync(d.team_sync))
      .catch(() => setSync(null))
  }, [me.id, me.availability, version])

  const choose = async (value) => {
    setSaving(value)
    try {
      await api.post('/api/mobile/quick-update', { participant_id: me.id, availability: value, text: '' })
      toast.success('Availability updated')
    } catch (e) {
      toast.error('Could not update availability', e.message)
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="space-y-6 p-4">
      <section>
        <h2 className="mb-2 text-sm font-medium text-fg-2">Your status</h2>
        <div role="radiogroup" aria-label="Your availability" className="space-y-2">
          {AVAILABILITY.map((o) => {
            const Icon = o.icon
            const on = current === o.value
            return (
              <button
                key={o.value}
                role="radio"
                aria-checked={on}
                onClick={() => !on && choose(o.value)}
                disabled={!!saving}
                className={cx(
                  'flex min-h-[56px] w-full items-center gap-3 rounded-xl border bg-surface px-4 text-left transition-colors',
                  on ? 'border-indigo-500 ring-1 ring-indigo-500' : 'border-line hover:bg-subtle',
                )}
              >
                <Icon {...I} className={on ? 'text-indigo-600 dark:text-indigo-400' : 'text-fg-3'} />
                <span className="min-w-0 flex-1">
                  <span className="block text-[15px] font-medium text-fg">{o.label}</span>
                  {on && o.value === 'Free in 30 mins' && <span className="block text-xs text-fg-3">{me.availability}</span>}
                </span>
                {saving === o.value ? (
                  <Spinner className="text-fg-3" />
                ) : (
                  <span className={cx('flex h-5 w-5 items-center justify-center rounded-full border-2', on ? 'border-indigo-600 dark:border-indigo-400' : 'border-line-strong')}>
                    {on && <span className="h-2.5 w-2.5 rounded-full bg-indigo-600 dark:bg-indigo-400" />}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-medium text-fg-2">Team</h2>
        <ul className="divide-y divide-line rounded-xl border border-line bg-surface">
          {participants
            .filter((p) => p.id !== me.id)
            .map((p) => (
              <li key={p.id} className="flex min-h-[56px] items-center gap-3 px-4">
                <Avatar name={p.name} size="md" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[15px] font-medium text-fg">{p.name}</span>
                  <span className="block truncate text-xs text-fg-3">{p.role}</span>
                </span>
                <span className="flex shrink-0 items-center gap-1.5 text-sm text-fg-2">
                  <Dot className={isFreeNow(p.availability) ? 'bg-emerald-500' : 'bg-line-strong'} />
                  {availabilityLabel(p.availability)}
                </span>
              </li>
            ))}
        </ul>
        {sync?.recommended_slot && (
          <div className="mt-3 flex items-center gap-3 rounded-xl border border-line bg-surface p-4">
            <CalendarClock {...I} className="shrink-0 text-fg-3" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-fg">Best time to sync: {sync.recommended_slot.label}</p>
              <p className="truncate text-xs text-fg-3">with {sync.attendees.join(', ')}</p>
            </div>
          </div>
        )}
      </section>

      {!embedded && onSignOut && (
        <button onClick={onSignOut} className="flex min-h-[48px] w-full items-center gap-3 rounded-full border border-line bg-surface px-5 text-left text-[15px] font-semibold text-fg hover:bg-subtle">
          <LogOut {...I} className="text-fg-3" /> Sign out
        </button>
      )}
    </div>
  )
}

// ---------------- Shell ----------------

const TABS = [
  { key: 'tasks', label: 'My Tasks', icon: ListChecks },
  { key: 'update', label: 'Send Update', icon: MessageSquare },
  { key: 'availability', label: 'Availability', icon: CalendarClock },
]

export default function MobileAppView({ state, ws, embedded = false }) {
  const toast = useToast()
  const auth = useAuth()
  const participants = state?.participants || []
  const tasks = state?.tasks || []
  const logs = state?.logs || []
  const mode = detectMode(state?.active_project)
  const [memberName, setMemberName] = useState(readMember)
  const [tab, setTab] = useState('tasks')

  // A signed-in member always sees themselves; leads (and the desktop preview) can switch people.
  // Members are matched by name too, so the choice survives demo resets (ids change).
  const locked = !embedded && auth?.role === 'member' && !!auth.participant
  const signedIn = locked ? participants.find((p) => p.id === auth.participant.id || p.name === auth.participant.name) : null
  const me = signedIn || participants.find((p) => p.name === memberName) || participants[0]
  const sender = useSender(me || {}, toast)
  const tgLink = telegramLink(state?.integrations?.telegram_bot, me)

  const select = (name) => {
    setMemberName(name)
    try {
      localStorage.setItem(MEMBER_KEY, name)
    } catch {
      /* ignore */
    }
  }

  const waitingOnMe = useMemo(() => {
    if (!me) return []
    return tasks
      .filter((t) => t.status === 'Blocked' && t.blocked_by_participant_id === me.id && t.owner_id !== me.id)
      .map((t) => ({ task: t, owner: t.owner_name || 'A teammate', need: extractNeed(t.blocker_reason || '') }))
  }, [tasks, me])

  const openCount = me ? tasks.filter((t) => t.owner_id === me.id && t.status !== 'Complete').length : 0

  return (
    <div className={cx('flex flex-col bg-bg text-fg', embedded ? 'h-full' : 'h-[100dvh]')}>
      <Header
        me={me}
        participants={participants}
        onSelect={select}
        live={ws?.status === 'live'}
        botUsername={state?.integrations?.telegram_bot}
        embedded={embedded}
        locked={locked}
        project={state?.active_project}
      />

      {!me ? (
        <div className="flex flex-1 flex-col items-center justify-center p-8 text-center">
          <p className="text-sm font-medium text-fg">No teammates in this project yet</p>
          <p className="mt-1 text-sm text-fg-2">Add members from the dashboard on the laptop.</p>
        </div>
      ) : (
        <main className="flex min-h-0 flex-1 flex-col">
          <WaitingBanner items={waitingOnMe} me={me} sender={sender} />
          {tab === 'update' ? (
            <SendUpdate me={me} logs={logs} mode={mode} sender={sender} toast={toast} />
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto">
              {tab === 'tasks' && <MyTasks me={me} tasks={tasks} sender={sender} toast={toast} tgLink={tgLink} />}
              {tab === 'availability' && (
                <Availability
                  me={me}
                  participants={participants}
                  version={ws?.lastEventAt}
                  embedded={embedded}
                  onSignOut={auth?.session ? auth.logout : null}
                  toast={toast}
                />
              )}
            </div>
          )}
        </main>
      )}

      <nav className={cx('grid grid-cols-3 border-t border-line bg-surface', !embedded && 'pb-[env(safe-area-inset-bottom)]')} aria-label="App sections">
        {TABS.map((t) => {
          const Icon = t.icon
          const active = tab === t.key
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              aria-current={active ? 'page' : undefined}
              className={cx('relative flex min-h-[56px] flex-col items-center justify-center gap-1 text-[11px] font-medium transition-colors', active ? 'text-indigo-600 dark:text-indigo-400' : 'text-fg-3')}
            >
              <span className="relative">
                <Icon {...I} size={20} />
                {t.key === 'tasks' && openCount > 0 && (
                  <span className="absolute -right-2.5 -top-1.5 min-w-[16px] rounded-full bg-fg px-1 text-center text-[10px] font-semibold leading-4 text-bg">{openCount}</span>
                )}
                {t.key === 'tasks' && waitingOnMe.length > 0 && <span className="absolute -left-1 -top-0.5 h-2 w-2 rounded-full bg-rose-500" />}
              </span>
              {t.label}
            </button>
          )
        })}
      </nav>
    </div>
  )
}
