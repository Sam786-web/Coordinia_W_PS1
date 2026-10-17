import { useState } from 'react'
import Modal from '../Modal'
import { Field, Spinner } from '../ui'
import { api } from '../../lib/api'
import { useToast } from '../Toast'

export function AddMemberModal({ open, onClose }) {
  const toast = useToast()
  const empty = { name: '', role: '', telegram_username: '' }
  const [form, setForm] = useState(empty)
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

  const save = async (e) => {
    e?.preventDefault()
    if (!form.name.trim()) return
    setBusy(true)
    try {
      const p = await api.post('/api/participants', {
        name: form.name.trim(),
        role: form.role.trim(),
        telegram_username: form.telegram_username.replace(/^@/, '').trim() || null,
      })
      toast.success(`${p.name} added · PIN ${p.auth_pin}`, `Share the invite code and this PIN. On Telegram they send /auth ${p.auth_pin}.`)
      setForm(empty)
      onClose()
    } catch (err) {
      toast.error('Could not add member', err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add member"
      width="max-w-md"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" onClick={save} disabled={busy || !form.name.trim()}>
            {busy && <Spinner />} Add member
          </button>
        </>
      }
    >
      <form className="space-y-4" onSubmit={save}>
        <Field label="Name">
          <input className="input" value={form.name} onChange={set('name')} placeholder="e.g. Aarav" autoFocus />
        </Field>
        <Field label="Role">
          <input className="input" value={form.role} onChange={set('role')} placeholder="e.g. Frontend Developer" />
        </Field>
        <Field label="Telegram username" hint="Optional. Members can also connect later from their phone.">
          <input className="input" value={form.telegram_username} onChange={set('telegram_username')} placeholder="@username" />
        </Field>
      </form>
    </Modal>
  )
}

export function AddTaskModal({ open, onClose, participants, tasks, numbers }) {
  const toast = useToast()
  const empty = { title: '', description: '', owner_id: '', depends_on_task_id: '', deadline: '', task_type: '' }
  const [form, setForm] = useState(empty)
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

  const save = async (e) => {
    e?.preventDefault()
    if (!form.title.trim()) return
    setBusy(true)
    try {
      const t = await api.post('/api/tasks', {
        title: form.title.trim(),
        description: form.description.trim(),
        owner_id: form.owner_id ? Number(form.owner_id) : null,
        depends_on_task_id: form.depends_on_task_id ? Number(form.depends_on_task_id) : null,
        deadline: form.deadline.trim(),
        task_type: form.task_type || null,
      })
      toast.success('Task created', t.title)
      setForm(empty)
      onClose()
    } catch (err) {
      toast.error('Could not create task', err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New task"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" onClick={save} disabled={busy || !form.title.trim()}>
            {busy && <Spinner />} Create task
          </button>
        </>
      }
    >
      <form className="space-y-4" onSubmit={save}>
        <Field label="Title">
          <input className="input" value={form.title} onChange={set('title')} placeholder="e.g. Deploy backend to staging" autoFocus />
        </Field>
        <Field label="Description">
          <input className="input" value={form.description} onChange={set('description')} placeholder="Optional" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Owner">
            <select className="input" value={form.owner_id} onChange={set('owner_id')}>
              <option value="">Unassigned</option>
              {participants.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Depends on">
            <select className="input" value={form.depends_on_task_id} onChange={set('depends_on_task_id')}>
              <option value="">Nothing</option>
              {tasks.map((t) => (
                <option key={t.id} value={t.id}>
                  #{numbers[t.id]} {t.title}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Deadline">
            <input type="datetime-local" className="input" value={form.deadline} onChange={set('deadline')} />
          </Field>
          <Field label="Type" hint="Decides how it's verified.">
            <select className="input" value={form.task_type} onChange={set('task_type')}>
              <option value="">Detect from the title</option>
              <option value="github_code">Code (verified by GitHub commits)</option>
              <option value="design_docs">Design & docs (verified by a link)</option>
              <option value="general">General</option>
            </select>
          </Field>
        </div>
      </form>
    </Modal>
  )
}
