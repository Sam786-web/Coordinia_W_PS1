import { useMemo, useState } from 'react'
import { ArrowUp, Bot, Check, Lock, MessagesSquare, PanelRightClose, PanelRightOpen } from 'lucide-react'
import { Avatar, Spinner } from './ui'
import { useToast } from './Toast'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { buildConversation } from '../lib/conversation'
import { suggestionsFor } from '../lib/suggestions'
import { canUpdate } from '../lib/tasks'
import { ICON, clockTime, cx, detectMode, formatDeadline } from '../lib/utils'

function ChecklistItem({ task, numbers, onToggle }) {
  const auth = useAuth()
  const [busy, setBusy] = useState(false)
  const done = task.status === 'Complete'
  const allowed = canUpdate(task, auth)
  return (
    <button
      onClick={async () => {
        setBusy(true)
        try {
          await onToggle(task)
        } finally {
          setBusy(false)
        }
      }}
      disabled={busy}
      title={allowed ? (done ? 'Reopen this task' : 'Mark this task done') : `Only ${task.owner_name || 'the owner'} or the Team Lead can update this task`}
      className="group flex w-full items-center gap-3 rounded-2xl bg-white/95 px-3.5 py-2.5 text-left text-zinc-900 shadow-sm transition hover:-translate-y-px hover:bg-white hover:shadow"
    >
      <span
        className={cx(
          'flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 transition',
          done ? 'border-zinc-900 bg-zinc-900 text-white' : 'border-zinc-300 group-hover:border-zinc-500',
        )}
      >
        {busy ? <Spinner className="h-3 w-3" /> : done ? <Check {...ICON} size={12} strokeWidth={3} /> : null}
      </span>
      <span className="min-w-0 flex-1">
        <span className={cx('block truncate text-[13px] font-bold', done && 'text-zinc-400 line-through')}>{task.title}</span>
        <span className="block truncate text-[11px] font-semibold text-zinc-500">
          #{numbers[task.id]} · {task.owner_name || 'Unassigned'}
          {task.deadline && !done && (
            <span className={formatDeadline(task.deadline).state === 'overdue' ? 'font-bold text-rose-700' : ''}> · due {formatDeadline(task.deadline).label}</span>
          )}
        </span>
      </span>
      {task.status === 'Blocked' && <span className="shrink-0 rounded-full bg-flamingo px-2 py-0.5 text-[10px] font-bold text-rose-950">Blocked</span>}
      {task.proof === 'self' && <span className="shrink-0 rounded-full bg-butter px-2 py-0.5 text-[10px] font-bold text-amber-950" title="Marked done without a commit, link or review">Needs proof</span>}
      {task.status === 'In Progress' && <span className="shrink-0 rounded-full bg-butter px-2 py-0.5 text-[10px] font-bold text-amber-950">Active</span>}
      {!allowed && <Lock {...ICON} size={13} className="shrink-0 text-zinc-400" />}
    </button>
  )
}

function CoordinatorCard({ logs, participants, mode, onTakeUpdates, sending, onOpen }) {
  const toast = useToast()
  const auth = useAuth()
  const [text, setText] = useState('')
  const [senderId, setSenderId] = useState('')
  const [busy, setBusy] = useState(false)
  const [choice, setChoice] = useState(null)
  const items = useMemo(() => buildConversation(logs), [logs])
  const latest = [...items].reverse().find((it) => it.kind === 'out' || it.kind === 'warn' || it.kind === 'in')
  // A member can only post as themselves; the Team Lead can test replies as anyone
  const people = auth.isLead ? participants : participants.filter((p) => p.id === auth.participant?.id)
  const sender = people.find((p) => String(p.id) === String(senderId)) || people[0]
  const chips = auth.isLead ? suggestionsFor(mode, participants).slice(0, 2) : []

  const send = async (who, message, taskId = null) => {
    if (!who || !message.trim()) return
    setBusy(true)
    try {
      const out = await api.post('/api/simulate-reply', { participant_id: who.id, message, source: 'simulator', task_id: taskId })
      const r = out.result
      setText('')
      if (r.needs_task_choice) return setChoice({ who, message, options: r.task_options })
      setChoice(null)
      if (r.contradiction_warning) toast.info('Completion refused', 'An upstream task is still blocked.')
      else if (r.unblocked_downstream_task_id) toast.success('Teammate unblocked')
      else if (r.auto_relay_target_name) toast.success(`Asked ${r.auto_relay_target_name} for help`, `${who.name}'s task is Blocked.`)
      else toast.success(`${who.name}'s update processed`, `Status: ${r.new_status}`)
    } catch (e) {
      toast.error('Update refused', e.message)
    } finally {
      setBusy(false)
    }
  }

  const primary = () => {
    if (text.trim()) send(sender, text)
    else if (auth.isLead) onTakeUpdates()
  }

  const meta = latest
    ? `${latest.kind === 'out' ? `Coordinator → ${latest.to}` : latest.kind === 'in' ? `${latest.who} via ${latest.via}` : 'Contradiction guard'} · ${clockTime(latest.ts)}`
    : ''

  return (
    <section className="rounded-[26px] bg-white p-4 text-zinc-900 shadow-md" aria-label="Telegram Coordinator Feed">
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-lavender text-[#1E1B4B]">
          <Bot {...ICON} size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-extrabold">Telegram Coordinator</p>
          <p className="flex items-center gap-1 text-[11px] font-semibold text-zinc-500">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> Live feed
          </p>
        </div>
        <button onClick={() => onOpen('feed')} className="flex shrink-0 items-center gap-1.5 rounded-full bg-zinc-100 py-1 pl-3 pr-1 text-[11px] font-bold transition hover:bg-zinc-200" title="Open the full feed">
          Full feed <span className="rounded-full bg-zinc-900 px-1.5 py-0.5 text-[10px] text-white">{items.length}</span>
        </button>
      </div>

      {choice ? (
        <div className="mt-3 rounded-2xl bg-butter/60 p-3">
          <p className="text-[13px] font-bold">{choice.who.name} has {choice.options.length} open tasks. Which one?</p>
          <div className="mt-2 space-y-1.5">
            {choice.options.map((o) => (
              <button key={o.id} disabled={busy} onClick={() => send(choice.who, choice.message, o.id)} className="w-full truncate rounded-full bg-white px-3 py-1.5 text-left text-xs font-bold shadow-sm hover:bg-zinc-50">
                {o.title}
              </button>
            ))}
          </div>
        </div>
      ) : latest ? (
        <button onClick={() => onOpen('feed')} className="mt-3 block w-full rounded-2xl rounded-tl-md bg-zinc-50 p-3 text-left ring-1 ring-zinc-100 transition hover:bg-zinc-100" title="Open the full feed">
          <p className="line-clamp-3 whitespace-pre-line text-[13px] font-medium leading-snug text-zinc-800">{latest.text}</p>
          <p className="mt-1.5 truncate text-[11px] font-semibold text-zinc-500">{meta}</p>
        </button>
      ) : (
        <p className="mt-3 rounded-2xl bg-zinc-50 p-3 text-[13px] font-semibold text-zinc-500">No coordinator messages yet. Send the first update below.</p>
      )}

      {chips.length > 0 && (
        <div className="mt-3 flex gap-1.5 overflow-x-auto pb-0.5">
          {chips.map((c) => (
            <button
              key={c.label}
              disabled={busy}
              onClick={() => {
                const who = participants.find((p) => p.name.toLowerCase() === c.sender.toLowerCase())
                if (!who) return toast.error(`${c.sender} isn't in this project`)
                setSenderId(who.id)
                send(who, c.message)
              }}
              className="shrink-0 whitespace-nowrap rounded-full border border-zinc-200 px-2.5 py-1 text-[11px] font-bold text-zinc-700 transition hover:border-zinc-300 hover:bg-zinc-50"
            >
              {c.label}
            </button>
          ))}
        </div>
      )}

      <div className="mt-3 flex items-center gap-1.5 rounded-full border border-zinc-200 bg-white p-1 focus-within:border-coral focus-within:ring-2 focus-within:ring-coral/20">
        {auth.isLead && people.length > 0 && (
          <select className="h-8 max-w-[96px] shrink-0 rounded-full bg-zinc-100 px-2 text-xs font-bold text-zinc-800 outline-none" value={sender?.id ?? ''} onChange={(e) => setSenderId(e.target.value)} aria-label="Reply as">
            {people.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        )}
        <input
          className="h-8 min-w-0 flex-1 bg-transparent px-2 text-[13px] font-medium text-zinc-900 outline-none placeholder:text-zinc-400"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && primary()}
          placeholder={sender ? `Reply as ${sender.name}…` : 'Type an update…'}
          aria-label="Update message"
        />
        <button
          onClick={() => send(sender, text)}
          disabled={busy || !text.trim()}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-white transition hover:bg-zinc-700 disabled:bg-zinc-200 disabled:text-zinc-400"
          aria-label="Send message"
        >
          {busy ? <Spinner className="h-3.5 w-3.5" /> : <ArrowUp {...ICON} size={15} strokeWidth={2.5} />}
        </button>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <button
          onClick={primary}
          disabled={busy || sending || (!text.trim() && !auth.isLead)}
          className="flex items-center justify-center gap-1.5 rounded-full bg-terracotta px-4 py-2.5 text-[13px] font-bold text-white transition hover:bg-[#d0553b] disabled:opacity-50"
        >
          {sending ? <Spinner /> : null} {text.trim() || !auth.isLead ? 'Send Update' : 'Ping Team'}
        </button>
        <button onClick={() => onOpen('team')} className="rounded-full bg-[#E5E7EB] px-4 py-2.5 text-[13px] font-bold text-zinc-800 transition hover:bg-zinc-300">
          Invite / PINs
        </button>
      </div>
    </section>
  )
}

export default function LimePanel({ state, tasks, participants, logs, numbers, onOpen, onTakeUpdates, sending, collapsed, onCollapse, onToggleTask }) {
  const auth = useAuth()
  const mine = auth.isLead ? tasks : tasks.filter((t) => t.owner_id === auth.participant?.id)
  const done = mine.filter((t) => t.status === 'Complete').length
  const remaining = mine.length - done
  const pct = mine.length ? Math.round((100 * done) / mine.length) : 0
  // Open work first, finished tasks sink to the bottom
  const ordered = [...mine].sort((a, b) => (a.status === 'Complete') - (b.status === 'Complete'))

  if (collapsed) {
    return (
      <aside className="flex w-14 shrink-0 flex-col items-center rounded-[32px] bg-lime py-5 text-[#111827] shadow-xl">
        <button className="flex h-10 w-10 items-center justify-center rounded-full hover:bg-black/5" onClick={onCollapse} aria-label="Open my daily tasks" title="Open my daily tasks">
          <PanelRightOpen {...ICON} size={18} />
        </button>
        <span className="mt-3 rounded-full bg-zinc-900 px-2 py-0.5 text-xs font-bold text-white">{remaining}</span>
        <MessagesSquare {...ICON} className="mt-4 opacity-60" />
      </aside>
    )
  }

  return (
    <aside className="lime-scroll flex h-full w-full min-h-0 flex-col gap-4 overflow-y-auto rounded-[32px] bg-lime p-5 text-[#111827] shadow-xl" aria-label="My daily tasks">
      <div className="flex shrink-0 items-center justify-between">
        <button className="flex h-9 w-9 items-center justify-center rounded-full hover:bg-black/5" onClick={onCollapse} aria-label="Collapse panel" title="Collapse panel">
          <PanelRightClose {...ICON} size={18} />
        </button>
        <p className="text-base font-extrabold">My daily tasks</p>
        <Avatar name={auth.user?.name || '?'} size="md" className="ring-2 ring-white/70" />
      </div>

      <div className="shrink-0 px-1">
        <p className="text-lg font-extrabold leading-snug tracking-tight 2xl:text-xl">
          {remaining ? (
            <>
              You have {remaining} active task{remaining === 1 ? '' : 's'} remaining
            </>
          ) : mine.length ? (
            'All caught up. Nice work!'
          ) : (
            'Nothing on your plate yet'
          )}
        </p>
        <div className="mt-3 flex items-center gap-3">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-black/10">
            <div className="h-full rounded-full bg-zinc-900 transition-all duration-500" style={{ width: `${pct}%` }} />
          </div>
          <span className="text-xs font-extrabold tabular-nums">{pct}%</span>
        </div>
      </div>

      <div className="shrink-0">
        <div className="mb-2 flex items-center justify-between gap-2 px-1 text-[13px]">
          <p className="truncate font-extrabold">{state.active_project?.name}</p>
          <p className="shrink-0 font-bold opacity-70">
            Done {done}/{mine.length}
          </p>
        </div>
        <div className="lime-scroll -mr-1.5 max-h-[30vh] space-y-2 2xl:max-h-[38vh] overflow-y-auto pr-1.5">
          {ordered.map((t) => (
            <ChecklistItem key={t.id} task={t} numbers={numbers} onToggle={onToggleTask} />
          ))}
          {!mine.length && <p className="rounded-2xl bg-white/60 px-4 py-6 text-center text-sm font-semibold">No tasks assigned yet.</p>}
        </div>
      </div>

      <div className="mt-auto shrink-0">
        <CoordinatorCard logs={logs} participants={participants} mode={detectMode(state.active_project)} onTakeUpdates={onTakeUpdates} sending={sending} onOpen={onOpen} />
      </div>
    </aside>
  )
}
