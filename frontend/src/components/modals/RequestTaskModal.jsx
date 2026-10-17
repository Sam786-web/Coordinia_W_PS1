import { useEffect, useState } from 'react'
import { Send } from 'lucide-react'
import Modal from '../Modal'
import { Field, Spinner, StatusPill } from '../ui'
import { api } from '../../lib/api'
import { useAuth } from '../../lib/auth'
import { ICON, cx, formatDeadline } from '../../lib/utils'
import { useToast } from '../Toast'

const REQUEST_TONE = { pending: 'Pending', approved: 'Complete', rejected: 'Blocked' }
const REQUEST_LABEL = { pending: 'Waiting for lead', approved: 'Approved', rejected: 'Not added' }

/**
 * A teammate asks the Team Lead to add a task: what, for whom, and whether it must happen
 * before or after one of their own tasks. The lead approves or rejects it on their dashboard.
 */
export default function RequestTaskModal({ open, onClose, participants = [], tasks = [], requests = [] }) {
  const auth = useAuth()
  const toast = useToast()
  const me = auth.participant
  const myTasks = tasks.filter((t) => t.owner_id === me?.id && t.status !== 'Complete')
  const empty = { title: '', description: '', assignee_id: '', relation: 'before', related_task_id: '', deadline: '' }
  const [f, setF] = useState(empty)
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }))

  useEffect(() => {
    if (!open) return
    setF({ ...empty, relation: myTasks.length ? 'before' : 'none', related_task_id: myTasks[0]?.id ?? '' })
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const needsTask = f.relation !== 'none'
  const valid = f.title.trim().length >= 3 && (!needsTask || f.related_task_id)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const out = await api.post('/api/task-requests', {
        title: f.title.trim(),
        description: f.description.trim(),
        assignee_id: f.assignee_id ? Number(f.assignee_id) : null,
        relation: f.relation,
        related_task_id: needsTask ? Number(f.related_task_id) : null,
        deadline: f.deadline,
      })
      toast.success('Request sent to your Team Lead', `"${out.title}" for ${out.assignee_name}. You'll hear back on Telegram.`)
      onClose()
    } catch (err) {
      toast.error('Could not send the request', err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Request a task"
      description="Need a teammate to do something for your work? Your Team Lead gets the request and adds it to the plan."
      accent="bg-lavender"
      width="max-w-lg"
    >
      <form className="space-y-4" onSubmit={submit}>
        <Field label="What needs doing?">
          <input className="input" value={f.title} onChange={set('title')} placeholder="e.g. Share the test credentials" autoFocus />
        </Field>
        <Field label="Details (optional)">
          <textarea className="input min-h-[64px] resize-y" value={f.description} onChange={set('description')} placeholder="Anything they need to know" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Who should do it?">
            <select className="input" value={f.assignee_id} onChange={set('assignee_id')}>
              <option value="">Me</option>
              {participants.filter((p) => p.id !== me?.id).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                  {p.role ? ` · ${p.role}` : ''}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Deadline (optional)">
            <input type="datetime-local" className="input" value={f.deadline} onChange={set('deadline')} />
          </Field>
        </div>
        <div>
          <span className="label">When is it needed?</span>
          <div className="flex flex-wrap gap-2">
            {[
              ['before', 'Before my task'],
              ['after', 'After my task'],
              ['none', 'No particular order'],
            ].map(([k, label]) => (
              <button
                key={k}
                type="button"
                disabled={k !== 'none' && !myTasks.length}
                onClick={() => setF((s) => ({ ...s, relation: k }))}
                className={cx('rounded-full px-3.5 py-1.5 text-xs font-bold transition disabled:opacity-40', f.relation === k ? 'bg-fg text-bg' : 'bg-subtle text-fg-2 ring-1 ring-line hover:text-fg')}
                aria-pressed={f.relation === k}
              >
                {label}
              </button>
            ))}
          </div>
          {needsTask && (
            <select className="input mt-2" value={f.related_task_id} onChange={set('related_task_id')} aria-label="Which of your tasks">
              {myTasks.map((t) => (
                <option key={t.id} value={t.id}>
                  {f.relation === 'before' ? 'Needed before: ' : 'Starts after: '}
                  {t.title}
                </option>
              ))}
            </select>
          )}
          {!myTasks.length && <p className="mt-1.5 text-xs text-fg-3">You have no open tasks, so the order can't be tied to one.</p>}
        </div>
        <button className="btn-primary w-full" disabled={busy || !valid}>
          {busy ? <Spinner /> : <Send {...ICON} />} Send request to Team Lead
        </button>
      </form>

      {requests.length > 0 && (
        <div className="mt-5 border-t border-line pt-4">
          <p className="section-label mb-2">Your requests</p>
          <ul className="space-y-1.5">
            {requests.slice(0, 6).map((r) => (
              <li key={r.id} className="flex items-center gap-2 rounded-row bg-panel px-3 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate font-semibold text-fg" title={r.decision_note || r.title}>
                  {r.title} <span className="font-normal text-fg-3">· {r.assignee_name}</span>
                </span>
                {r.deadline && <span className="shrink-0 text-[11px] text-fg-3">{formatDeadline(r.deadline).label}</span>}
                <StatusPill status={REQUEST_TONE[r.status]} label={REQUEST_LABEL[r.status]} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </Modal>
  )
}
