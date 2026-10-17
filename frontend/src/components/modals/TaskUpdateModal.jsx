import { useEffect, useState } from 'react'
import { BadgeCheck, CalendarClock, Check, Flag, Link2, Play, RotateCcw, ShieldCheck } from 'lucide-react'
import Modal from '../Modal'
import { Avatar, Field, Spinner, StatusPill } from '../ui'
import { api } from '../../lib/api'
import { useAuth } from '../../lib/auth'
import { canUpdate, isImageFile, isProofFile } from '../../lib/tasks'
import { DEADLINE_TONE, ICON, cx, formatDeadline, toDateTimeInput } from '../../lib/utils'
import { useToast } from '../Toast'

const PROOF_TEXT = {
  github: ['bg-mint text-emerald-950', 'Verified by a GitHub commit or pull request.'],
  deliverable: ['bg-mint text-emerald-950', 'Verified by a link to the finished work.'],
  lead: ['bg-sky-100 text-sky-950 dark:bg-sky-500/20 dark:text-sky-200', 'You reviewed and approved this.'],
  self: ['bg-butter text-amber-950', 'Marked done without proof. Check the work, then approve it or reopen it.'],
}

/** Team Lead review of a completion: approve (vouch for it) or reopen with a reason the teammate receives. */
function ReviewPanel({ task, busy, run, onDone }) {
  const toast = useToast()
  const [reason, setReason] = useState('')
  const [reopening, setReopening] = useState(false)
  const file = isProofFile(task.deliverable_url)
  const [tone, text] = file && task.proof === 'deliverable'
    ? ['bg-mint text-emerald-950', 'A file was sent as proof on Telegram. Check it, then approve it or reopen it.']
    : PROOF_TEXT[task.proof] || PROOF_TEXT.self
  const review = (action) =>
    run(`review-${action}`, async () => {
      const out = await api.post(`/api/tasks/${task.id}/review`, { action, reason })
      const told = out.notifications?.[0]?.delivered ? ` ${task.owner_name} was told on Telegram.` : ''
      toast.success(action === 'approve' ? 'Completion approved' : 'Task reopened', `"${task.title}".${told}`)
      onDone()
    })
  return (
    <div className="border-t border-line pt-4">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-bold text-fg-2">
        <BadgeCheck {...ICON} size={13} /> Proof of work
      </p>
      <p className={cx('rounded-row px-3.5 py-2.5 text-sm font-semibold', tone)}>{text}</p>
      {task.last_commit_info && <p className="mt-1.5 truncate font-mono text-xs text-fg-3" title={task.last_commit_info}>{task.last_commit_info}</p>}
      {isImageFile(task.deliverable_url) ? (
        <a href={task.deliverable_url} target="_blank" rel="noreferrer" className="mt-2 block overflow-hidden rounded-row ring-1 ring-line" title="Open full size">
          <img src={task.deliverable_url} alt={`Proof for ${task.title}`} className="max-h-56 w-full bg-panel object-contain" />
        </a>
      ) : task.deliverable_url ? (
        <a href={task.deliverable_url} target="_blank" rel="noreferrer" className="mt-1.5 block truncate text-xs font-semibold text-coral hover:underline">
          {file ? 'Open the file sent on Telegram' : task.deliverable_url}
        </a>
      ) : null}
      {reopening ? (
        <div className="mt-3 space-y-2">
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What still needs doing? e.g. Login fails on mobile" autoFocus />
          <div className="flex gap-2">
            <button className="btn flex-1" onClick={() => setReopening(false)} disabled={!!busy}>
              Cancel
            </button>
            <button className="btn-primary flex-1" disabled={!!busy || !reason.trim()} onClick={() => review('reopen')}>
              {busy === 'review-reopen' ? <Spinner /> : <RotateCcw {...ICON} />} Reopen & tell {task.owner_name || 'owner'}
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex gap-2">
          {task.proof !== 'lead' && (
            <button className="btn flex-1 border-transparent bg-mint text-emerald-950 hover:bg-mint/80" disabled={!!busy} onClick={() => review('approve')}>
              {busy === 'review-approve' ? <Spinner /> : <BadgeCheck {...ICON} />} Approve
            </button>
          )}
          <button className="btn flex-1" disabled={!!busy} onClick={() => setReopening(true)}>
            <RotateCcw {...ICON} /> Reopen
          </button>
        </div>
      )}
    </div>
  )
}

function DeadlineEditor({ task, busy, onSave }) {
  const [value, setValue] = useState(toDateTimeInput(task.deadline))
  useEffect(() => {
    setValue(toDateTimeInput(task.deadline))
  }, [task.id, task.deadline])
  const changed = value !== toDateTimeInput(task.deadline)
  return (
    <div className="border-t border-line pt-4">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-bold text-fg-2">
        <CalendarClock {...ICON} size={13} /> Deadline (the owner is told on Telegram)
      </p>
      <div className="flex gap-2">
        <input type="datetime-local" className="input" value={value} onChange={(e) => setValue(e.target.value)} aria-label="Task deadline" />
        <button className="btn shrink-0" disabled={!!busy || !value || !changed} onClick={() => onSave(value)}>
          {busy === 'deadline' ? <Spinner /> : 'Save'}
        </button>
        {task.deadline && (
          <button className="btn-ghost shrink-0" disabled={!!busy} onClick={() => onSave('')} title="Remove the deadline">
            Clear
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * Update one task. Owners go through the AI coordinator (so dependency rules and relays still apply);
 * the Team Lead additionally gets direct overrides.
 */
export default function TaskUpdateModal({ task, onClose }) {
  const toast = useToast()
  const auth = useAuth()
  const [mode, setMode] = useState(null) // 'blocked' | 'deliverable'
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(null)
  const [warning, setWarning] = useState(null)

  useEffect(() => {
    setMode(null)
    setText('')
    setWarning(null)
  }, [task?.id])

  if (!task) return null
  const allowed = canUpdate(task, auth)

  const run = async (key, fn) => {
    setBusy(key)
    try {
      await fn()
    } catch (e) {
      toast.error('Update refused', e.message)
    } finally {
      setBusy(null)
    }
  }

  const viaCoordinator = (key, message) =>
    run(key, async () => {
      const out = await api.post('/api/simulate-reply', { participant_id: task.owner_id, task_id: task.id, message, source: 'simulator' })
      const r = out.result
      if (r.contradiction_warning) {
        setWarning(r.contradiction_warning)
        return
      }
      toast.success(`"${task.title}" → ${r.new_status}`, r.auto_relay_target_name ? `Asked ${r.auto_relay_target_name} for help.` : undefined)
      onClose()
    })

  const markBlocked = () =>
    run('blocked', async () => {
      const out = await api.post('/api/mobile/quick-update', { participant_id: task.owner_id, task_id: task.id, action_type: 'SOS_BLOCKER', text })
      toast.success('Blocker logged', out.result?.auto_relay_target_name ? `Asked ${out.result.auto_relay_target_name} for help.` : 'The team has been alerted.')
      onClose()
    })

  const submitDeliverable = () =>
    run('deliverable', async () => {
      const out = await api.post(`/api/tasks/${task.id}/deliverable`, { url: text.trim() })
      toast.success(out.completed ? 'Deliverable submitted · Complete' : 'Link saved', out.completed ? undefined : 'A task this depends on is still blocked.')
      onClose()
    })

  const override = (status) =>
    run(`ov-${status}`, async () => {
      await api.post(`/api/tasks/${task.id}/override`, { status })
      toast.success(`"${task.title}" marked ${status}`, 'Team Lead override')
      onClose()
    })

  const saveDeadline = (value) =>
    run('deadline', async () => {
      const out = await api.patch(`/api/tasks/${task.id}/deadline`, { deadline: value })
      const n = out.notifications?.[0]
      toast.success(value ? `Deadline set: ${formatDeadline(out.task.deadline).label}` : 'Deadline removed',
        n?.delivered ? `${task.owner_name} was told on Telegram.` : undefined)
      onClose()
    })

  const note = text.trim() ? ` - ${text.trim()}` : ''
  const due = formatDeadline(task.deadline, task.status === 'Complete')
  return (
    <Modal open={!!task} onClose={onClose} title="Update task" accent="bg-lime" width="max-w-md">
      <div className="flex items-center gap-3 rounded-row bg-panel p-3.5">
        <Avatar name={task.owner_name || '?'} size="lg" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-extrabold text-fg">{task.title}</p>
          <p className="text-xs font-semibold text-fg-2">{task.owner_name || 'Unassigned'}</p>
        </div>
        <StatusPill status={task.status} />
      </div>
      {due.label && (
        <p className={cx('mt-2 inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold', DEADLINE_TONE[due.state])}>
          <CalendarClock {...ICON} size={13} /> {due.state === 'overdue' ? 'Overdue · ' : 'Due '}{due.label}
        </p>
      )}

      {!allowed ? (
        <p className="mt-4 rounded-row bg-flamingo px-4 py-3 text-sm font-semibold text-rose-950">
          Only {task.owner_name || 'the owner'} or the Team Lead can update this task.
        </p>
      ) : (
        <div className="mt-4 space-y-4">
          {warning && <p className="rounded-row bg-[#FED7AA] px-4 py-3 text-sm font-semibold text-orange-950">{warning}</p>}
          <div className="grid grid-cols-3 gap-2">
            <button className="btn flex-col gap-1 py-3 text-xs" disabled={!!busy || !task.owner_id} onClick={() => viaCoordinator('progress', `Started working on this task${note}`)}>
              {busy === 'progress' ? <Spinner /> : <Play {...ICON} />} In Progress
            </button>
            <button className={cx('btn flex-col gap-1 py-3 text-xs', mode === 'blocked' && 'border-rose-400 bg-flamingo text-rose-950')} disabled={!!busy || !task.owner_id} onClick={() => setMode('blocked')}>
              <Flag {...ICON} /> Blocked
            </button>
            <button className="btn-primary flex-col gap-1 py-3 text-xs" disabled={!!busy || !task.owner_id} onClick={() => viaCoordinator('done', `Marked complete from the dashboard${note}`)}>
              {busy === 'done' ? <Spinner /> : <Check {...ICON} />} Done
            </button>
          </div>
          {mode === 'blocked' ? (
            <div className="space-y-2">
              <Field label="What are you waiting on, and which teammate can help?">
                <textarea className="input min-h-[72px] resize-none" value={text} onChange={(e) => setText(e.target.value)} placeholder="Waiting for the API keys from Meera" autoFocus />
              </Field>
              <button className="btn-primary w-full" disabled={!!busy || !text.trim()} onClick={markBlocked}>
                {busy === 'blocked' ? <Spinner /> : <Flag {...ICON} />} Mark blocked & alert the team
              </button>
            </div>
          ) : (
            <Field label="Note (optional)">
              <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="What changed?" />
            </Field>
          )}
          {task.task_type === 'design_docs' && task.status !== 'Complete' && (
            <div className="space-y-2 rounded-row border border-dashed border-line p-3">
              <p className="flex items-center gap-1.5 text-xs font-bold text-fg-2">
                <Link2 {...ICON} size={13} /> Verify with a deliverable link (slides, Figma, doc)
              </p>
              <div className="flex gap-2">
                <input className="input" value={mode === 'deliverable' ? text : ''} onFocus={() => setMode('deliverable')} onChange={(e) => setText(e.target.value)} placeholder="https://…" />
                <button className="btn shrink-0" disabled={!!busy || mode !== 'deliverable' || !text.trim()} onClick={submitDeliverable}>
                  {busy === 'deliverable' ? <Spinner /> : 'Submit'}
                </button>
              </div>
            </div>
          )}
          {auth.isLead && task.status === 'Complete' && <ReviewPanel task={task} busy={busy} run={run} onDone={onClose} />}
          {auth.isLead && <DeadlineEditor task={task} busy={busy} onSave={saveDeadline} />}
          {auth.isLead && (
            <div className="border-t border-line pt-4">
              <p className="mb-2 flex items-center gap-1.5 text-xs font-bold text-fg-2">
                <ShieldCheck {...ICON} size={13} /> Team Lead override (skips the AI checks)
              </p>
              <div className="flex flex-wrap gap-2">
                {['Complete', 'In Progress', 'Pending'].filter((s) => s !== task.status).map((s) => (
                  <button key={s} className="pill-btn" disabled={!!busy} onClick={() => override(s)}>
                    {busy === `ov-${s}` ? <Spinner className="h-3.5 w-3.5" /> : null} Force {s}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}
