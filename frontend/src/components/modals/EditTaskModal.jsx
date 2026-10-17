import { useEffect, useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import Modal from '../Modal'
import { Field, Spinner } from '../ui'
import { api } from '../../lib/api'
import { ICON } from '../../lib/utils'
import { useToast } from '../Toast'

/** Team Lead: rename a task, change its description or owner, or discard it. */
export default function EditTaskModal({ task, participants, startWithDelete = false, onClose }) {
  const toast = useToast()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [ownerId, setOwnerId] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(null)

  useEffect(() => {
    if (!task) return
    setTitle(task.title || '')
    setDescription(task.description || '')
    setOwnerId(task.owner_id ? String(task.owner_id) : '')
    setConfirming(startWithDelete)
    setBusy(null)
  }, [task?.id, startWithDelete]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!task) return null
  const changes = {}
  if (title.trim() !== task.title) changes.title = title.trim()
  if (description.trim() !== (task.description || '').trim()) changes.description = description.trim()
  if ((ownerId || null) !== (task.owner_id ? String(task.owner_id) : null)) changes.owner_id = ownerId ? Number(ownerId) : null
  const dirty = Object.keys(changes).length > 0

  const save = async () => {
    setBusy('save')
    try {
      const out = await api.patch(`/api/tasks/${task.id}`, changes)
      const told = out.notifications?.filter((n) => n.delivered).map((n) => n.to)
      toast.success('Task updated', told?.length ? `Told ${told.join(' and ')} on Telegram.` : `"${out.title}"`)
      onClose()
    } catch (e) {
      toast.error('Could not save the task', e.message)
    } finally {
      setBusy(null)
    }
  }

  const remove = async () => {
    setBusy('delete')
    try {
      const out = await api.delete(`/api/tasks/${task.id}`)
      const told = out.notifications?.[0]?.delivered ? ` ${task.owner_name} was told on Telegram.` : ''
      const relinked = out.relinked ? ` ${out.relinked} task${out.relinked === 1 ? '' : 's'} that waited on it moved up.` : ''
      toast.success('Task deleted', `"${out.title}".${relinked}${told}`)
      onClose()
    } catch (e) {
      toast.error('Could not delete the task', e.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Modal open={!!task} onClose={onClose} title={confirming ? 'Delete this task?' : 'Edit task'} accent={confirming ? 'bg-flamingo' : 'bg-lavender'} width="max-w-md">
      {confirming ? (
        <div className="space-y-4">
          <div className="rounded-row bg-panel p-3.5">
            <p className="text-sm font-extrabold text-fg">{task.title}</p>
            <p className="text-xs font-semibold text-fg-2">
              {task.owner_name || 'Unassigned'} · {task.status}
            </p>
          </div>
          <p className="text-sm text-fg-2">
            The task and its deadline are removed for everyone. Tasks that were waiting on it will wait on the task before it instead.
            {task.owner_name && task.status !== 'Complete' ? ` ${task.owner_name} is told on Telegram.` : ''} This can't be undone.
          </p>
          <div className="flex gap-2">
            <button className="btn flex-1" onClick={() => (startWithDelete ? onClose() : setConfirming(false))} disabled={!!busy}>
              Keep it
            </button>
            <button className="btn flex-1 border-rose-300 bg-flamingo font-bold text-rose-950 hover:border-rose-400" onClick={remove} disabled={!!busy}>
              {busy === 'delete' ? <Spinner /> : <Trash2 {...ICON} size={14} />} Delete task
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <Field label="Title">
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={160} autoFocus />
          </Field>
          <Field label="Description (optional)">
            <textarea className="input min-h-[80px] resize-none" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What does done look like?" />
          </Field>
          <Field label="Assigned to" hint="Both the old and the new owner are told on Telegram.">
            <select className="input" value={ownerId} onChange={(e) => setOwnerId(e.target.value)}>
              <option value="">Unassigned</option>
              {participants.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          <p className="text-xs text-fg-3">To change the deadline or status, use Update on the task.</p>
          <div className="flex gap-2 border-t border-line pt-4">
            <button className="btn text-rose-600 hover:border-rose-300 dark:text-rose-400" onClick={() => setConfirming(true)} disabled={!!busy}>
              <Trash2 {...ICON} size={14} /> Delete
            </button>
            <div className="flex-1" />
            <button className="btn" onClick={onClose} disabled={!!busy}>
              Cancel
            </button>
            <button className="btn-primary" onClick={save} disabled={!!busy || !dirty || title.trim().length < 3}>
              {busy === 'save' ? <Spinner /> : <Pencil {...ICON} size={14} />} Save
            </button>
          </div>
        </div>
      )}
    </Modal>
  )
}
