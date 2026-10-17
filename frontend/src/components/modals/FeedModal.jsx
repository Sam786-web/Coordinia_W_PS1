import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRightLeft, ChevronDown, ChevronRight, CircleAlert } from 'lucide-react'
import Modal from '../Modal'
import { Avatar, Logo, Segmented } from '../ui'
import { buildConversation } from '../../lib/conversation'
import { ICON, clockTime, cx, parseDetails } from '../../lib/utils'

function JsonBlock({ value }) {
  return (
    <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-2xl border border-line bg-panel p-3 font-mono text-[11px] leading-relaxed text-fg-2">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function ActionLine({ item }) {
  const [open, setOpen] = useState(false)
  const r = item.result
  return (
    <div className="px-1">
      <div className="flex items-center gap-2 text-xs font-semibold text-fg-2">
        <ArrowRightLeft {...ICON} size={13} className="shrink-0 text-fg-3" />
        <span className="min-w-0 flex-1">{item.text}</span>
        <button onClick={() => setOpen((o) => !o)} className="shrink-0 text-xs font-bold text-coral hover:underline">
          {open ? 'Hide' : 'Details'}
        </button>
      </div>
      {open && (
        <JsonBlock
          value={{
            new_status: r.new_status, is_blocked: r.is_blocked, progress_note: r.progress_note, blocker_reason: r.blocker_reason,
            blocked_by_teammate_name: r.blocked_by_teammate_name, contradiction_warning: r.contradiction_warning,
            confidence_score: r.confidence_score, auto_relay_target_name: r.auto_relay_target_name,
            unblocked_downstream_task_id: r.unblocked_downstream_task_id, ai_layer: item.llm?.layer,
          }}
        />
      )}
    </div>
  )
}

function Conversation({ logs }) {
  const items = useMemo(() => buildConversation(logs), [logs])
  const endRef = useRef(null)
  // Block body: newer browsers return a Promise from scrollIntoView, and an effect must return nothing or a cleanup
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [items.length])
  if (!items.length) return <p className="py-10 text-center text-sm text-fg-3">No messages yet.</p>
  return (
    <div className="space-y-4 py-2">
      {items.map((it) => {
        if (it.kind === 'in') {
          return (
            <div key={it.key} className="flex gap-2.5">
              <Avatar name={it.who} size="md" />
              <div className="min-w-0 max-w-[85%]">
                <p className="mb-1 text-xs text-fg-3">
                  <span className="font-bold text-fg">{it.who}</span> · {it.via} · {clockTime(it.ts)}
                </p>
                <div className="rounded-2xl rounded-tl-md bg-panel px-3.5 py-2 text-sm text-fg">{it.text}</div>
              </div>
            </div>
          )
        }
        if (it.kind === 'out') {
          return (
            <div key={it.key} className="flex flex-row-reverse gap-2.5">
              <Logo className="h-8 w-8 shrink-0" />
              <div className="flex min-w-0 max-w-[85%] flex-col items-end">
                <p className="mb-1 text-xs text-fg-3">
                  <span className="font-bold text-fg">Coordinator</span> to {it.to} · {clockTime(it.ts)}
                </p>
                <div className="whitespace-pre-line rounded-2xl rounded-tr-md bg-lime px-3.5 py-2 text-sm font-semibold text-zinc-900">{it.text}</div>
                {it.status && it.status !== 'Reply' && (
                  <p className={cx('mt-1 text-[11px] font-semibold', it.failed ? 'text-fg-3' : 'text-emerald-600 dark:text-emerald-400')}>{it.status}</p>
                )}
              </div>
            </div>
          )
        }
        if (it.kind === 'action') return <ActionLine key={it.key} item={it} />
        if (it.kind === 'warn') {
          return (
            <div key={it.key} className="flex gap-2 rounded-2xl bg-[#FED7AA] px-3.5 py-2.5 text-xs font-semibold text-orange-950">
              <CircleAlert {...ICON} size={14} className="mt-px shrink-0" />
              <span>{it.text}</span>
            </div>
          )
        }
        return (
          <p key={it.key} className="text-center text-xs text-fg-3">
            {it.text}
          </p>
        )
      })}
      <div ref={endRef} />
    </div>
  )
}

function SystemLogs({ logs }) {
  const [open, setOpen] = useState(null)
  if (!logs.length) return <p className="py-10 text-center text-sm text-fg-3">No logs yet.</p>
  return (
    <ul className="divide-y divide-line">
      {logs.map((log) => {
        const expanded = open === log.id
        return (
          <li key={log.id}>
            <button onClick={() => setOpen(expanded ? null : log.id)} className="flex w-full items-start gap-2 py-2.5 text-left">
              {expanded ? <ChevronDown {...ICON} size={14} className="mt-0.5 shrink-0 text-fg-3" /> : <ChevronRight {...ICON} size={14} className="mt-0.5 shrink-0 text-fg-3" />}
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 font-mono text-[11px] text-fg-3">
                  <span className="text-fg-2">{log.log_type}</span>
                  <span>{clockTime(log.timestamp)}</span>
                </p>
                <p className="mt-0.5 text-xs font-semibold text-fg-2">{log.summary}</p>
              </div>
            </button>
            {expanded && <JsonBlock value={parseDetails(log.details_json)} />}
          </li>
        )
      })}
    </ul>
  )
}

/** Full AI coordinator history: human-readable conversation, plus raw system logs for judges. */
export default function FeedModal({ open, onClose, logs }) {
  const [tab, setTab] = useState('conversation')
  return (
    <Modal open={open} onClose={onClose} title="Telegram Coordinator Feed" description="Everything the AI coordinator sent, received and decided." accent="bg-lime" width="max-w-2xl">
      <Segmented
        className="mb-4"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'conversation', label: 'Conversation' },
          { value: 'logs', label: 'System logs (JSON)' },
        ]}
      />
      {tab === 'conversation' ? <Conversation logs={logs} /> : <SystemLogs logs={logs} />}
    </Modal>
  )
}
