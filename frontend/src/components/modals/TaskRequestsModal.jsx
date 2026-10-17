import { useEffect, useState } from 'react'
import { Inbox } from 'lucide-react'
import Modal from '../Modal'
import { Avatar, Spinner } from '../ui'
import { RequestRow } from '../TaskRequestsInbox'
import { api } from '../../lib/api'
import { cx, parseTs } from '../../lib/utils'

const TABS = [
  ['pending', 'Waiting'],
  ['approved', 'Approved'],
  ['rejected', 'Declined'],
]

/** Team Lead: every request teammates sent - approve or decline the waiting ones, see what was decided before. */
export default function TaskRequestsModal({ open, onClose, version }) {
  const [tab, setTab] = useState('pending')
  const [items, setItems] = useState(null)

  useEffect(() => {
    if (!open) return
    api.get('/api/task-requests').then(setItems, () => setItems([]))
  }, [open, version])

  const shown = (items || []).filter((r) => r.status === tab)
  const count = (k) => (items || []).filter((r) => r.status === k).length

  return (
    <Modal open={open} onClose={onClose} title="Task requests" description="Teammates ask you to add tasks here. Approving adds the task in the right place in the plan and tells everyone involved." accent="bg-lavender" width="max-w-2xl">
      <div className="mb-4 flex gap-1.5">
        {TABS.map(([k, label]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            aria-pressed={tab === k}
            className={cx('inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-bold transition', tab === k ? 'bg-fg text-bg' : 'bg-subtle text-fg-2 ring-1 ring-line hover:text-fg')}
          >
            {label}
            <span className={cx('rounded-full px-1.5 text-[10px] tabular-nums', tab === k ? 'bg-bg/20' : 'bg-panel')}>{count(k)}</span>
          </button>
        ))}
      </div>

      {items === null ? (
        <p className="flex items-center gap-2 py-8 text-sm text-fg-2">
          <Spinner /> Loading requests…
        </p>
      ) : !shown.length ? (
        <div className="rounded-row bg-panel px-5 py-8 text-center">
          <span className="mx-auto grid h-10 w-10 place-items-center rounded-2xl bg-lavender text-[#1E1B4B]">
            <Inbox size={18} />
          </span>
          <p className="mt-3 text-sm font-bold text-fg">{tab === 'pending' ? 'No requests waiting' : `No ${tab === 'approved' ? 'approved' : 'declined'} requests yet`}</p>
          {tab === 'pending' && (
            <p className="mx-auto mt-1 max-w-md text-xs text-fg-2">
              Teammates send requests from their dashboard (<b>Request a task</b>) or on Telegram with
              <code className="mx-1 rounded bg-subtle px-1.5 py-0.5">/request NAME: what needs doing</code>. They'll show up here, with a
              badge on the inbox icon.
            </p>
          )}
        </div>
      ) : tab === 'pending' ? (
        <ul className="space-y-2">
          {shown.map((r) => (
            <RequestRow key={r.id} r={r} />
          ))}
        </ul>
      ) : (
        <ul className="space-y-2">
          {shown.map((r) => (
            <li key={r.id} className="rounded-row border border-line bg-surface p-3.5 text-sm">
              <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-fg-2">
                <Avatar name={r.requester_name || '?'} size="sm" />
                <span className="text-fg">{r.requester_name}</span>
                <span className="text-fg-3">for</span>
                <span className="text-fg">{r.assignee_name}</span>
                {r.decided_at && <span className="text-fg-3">· decided {parseTs(r.decided_at).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}</span>}
              </div>
              <p className="mt-1.5 font-extrabold text-fg">{r.title}</p>
              {r.decision_note && <p className="mt-0.5 text-xs text-fg-2">Reason: {r.decision_note}</p>}
            </li>
          ))}
        </ul>
      )}
    </Modal>
  )
}
