import { useState } from 'react'
import { ArrowRight, Check, Inbox, X } from 'lucide-react'
import { Avatar, Spinner } from './ui'
import { api } from '../lib/api'
import { ICON, cx, formatDeadline } from '../lib/utils'
import { useToast } from './Toast'

export function RequestRow({ r }) {
  const toast = useToast()
  const [busy, setBusy] = useState(null)
  const [rejecting, setRejecting] = useState(false)
  const [note, setNote] = useState('')
  const timing =
    r.relation === 'before' ? `before "${r.related_task_title}"` : r.relation === 'after' ? `after "${r.related_task_title}"` : 'no particular order'

  const decide = async (action) => {
    setBusy(action)
    try {
      const out = await api.post(`/api/task-requests/${r.id}/decide`, { action, note })
      toast.success(action === 'approve' ? 'Task added to the plan' : 'Request declined',
        action === 'approve' ? `"${r.title}" assigned to ${out.request.assignee_name}.` : `${r.requester_name} was told why.`)
    } catch (e) {
      toast.error('Could not update the request', e.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <li className="rounded-row border border-line bg-surface p-3.5">
      <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-fg-2">
        <Avatar name={r.requester_name || '?'} size="sm" />
        <span className="text-fg">{r.requester_name}</span>
        <ArrowRight {...ICON} size={12} className="text-fg-3" />
        <Avatar name={r.assignee_name || '?'} size="sm" />
        <span className="text-fg">{r.assignee_name}</span>
        <span className="text-fg-3">· {timing}</span>
        {r.deadline && <span className="text-fg-3">· due {formatDeadline(r.deadline).label}</span>}
      </div>
      <p className="mt-2 text-sm font-extrabold text-fg">{r.title}</p>
      {r.description && <p className="mt-0.5 text-xs text-fg-2">{r.description}</p>}
      {rejecting ? (
        <div className="mt-3 flex gap-2">
          <input className="input h-9 py-1.5 text-xs" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why not? (sent to the requester)" autoFocus />
          <button className="btn shrink-0 px-3 py-1.5 text-xs" onClick={() => setRejecting(false)} disabled={!!busy}>
            Cancel
          </button>
          <button className="btn-primary shrink-0 px-3 py-1.5 text-xs" onClick={() => decide('reject')} disabled={!!busy}>
            {busy === 'reject' ? <Spinner /> : 'Decline'}
          </button>
        </div>
      ) : (
        <div className="mt-3 flex gap-2">
          <button className="btn border-transparent bg-mint px-3 py-1.5 text-xs text-emerald-950 hover:bg-mint/80" onClick={() => decide('approve')} disabled={!!busy}>
            {busy === 'approve' ? <Spinner /> : <Check {...ICON} size={14} />} Approve & add task
          </button>
          <button className="btn px-3 py-1.5 text-xs" onClick={() => setRejecting(true)} disabled={!!busy}>
            <X {...ICON} size={14} /> Decline
          </button>
        </div>
      )}
    </li>
  )
}

/** Team Lead inbox: requests from teammates to add tasks. Approving creates the task in the right order. */
export default function TaskRequestsInbox({ requests = [] }) {
  const pending = requests.filter((r) => r.status === 'pending')
  if (!pending.length) return null
  return (
    <section className="bento p-5" aria-label="Task requests">
      <div className="mb-3 flex items-center gap-2">
        <span className="grid h-8 w-8 place-items-center rounded-xl bg-lavender text-[#1E1B4B]">
          <Inbox {...ICON} size={16} />
        </span>
        <div>
          <p className="text-sm font-bold text-fg">Task requests</p>
          <p className="text-xs text-fg-3">
            {pending.length} from your team, waiting for you
          </p>
        </div>
      </div>
      <ul className={cx('space-y-2', pending.length > 3 && 'max-h-80 overflow-y-auto pr-1')}>
        {pending.map((r) => (
          <RequestRow key={r.id} r={r} />
        ))}
      </ul>
    </section>
  )
}
