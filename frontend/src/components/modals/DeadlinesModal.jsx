import { useEffect, useState } from 'react'
import { CalendarClock, Sparkles } from 'lucide-react'
import Modal from '../Modal'
import { Field, Spinner, Switch } from '../ui'
import { api } from '../../lib/api'
import { ICON, formatDeadline, toDateTimeInput } from '../../lib/utils'
import { useToast } from '../Toast'

const pad = (n) => String(n).padStart(2, '0')
const localInput = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`

/**
 * Team Lead sets the project's final deadline; the coordinator spreads task deadlines along the
 * dependency chain (a task is always due after the work it waits on).
 */
export default function DeadlinesModal({ open, onClose, project, tasks = [] }) {
  const toast = useToast()
  const [start, setStart] = useState('')
  const [due, setDue] = useState('')
  const [spread, setSpread] = useState(true)
  const [tell, setTell] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    const now = new Date()
    now.setSeconds(0, 0)
    setStart(toDateTimeInput(project?.start_at) || localInput(now))
    setDue(toDateTimeInput(project?.due_at) || localInput(new Date(now.getTime() + 2 * 86400000)))
    setSpread(true)
    setTell(false)
  }, [open, project?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const open_ = tasks.filter((t) => t.status !== 'Complete').length
  const valid = start && due && new Date(due) > new Date(start)

  const save = async () => {
    setBusy(true)
    try {
      const out = await api.post(`/api/projects/${project.id}/deadlines`, { start_at: start, due_at: due, distribute: spread, notify: tell })
      const sent = out.notifications.filter((n) => n.delivered).length
      toast.success(
        spread ? `Deadlines spread over ${out.updated.length} task${out.updated.length === 1 ? '' : 's'}` : 'Project deadline saved',
        `Final deadline ${formatDeadline(out.project.due_at).label}` + (tell ? ` · ${sent} owner${sent === 1 ? '' : 's'} told on Telegram` : ''),
      )
      onClose()
    } catch (e) {
      toast.error('Could not save the deadline', e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Project deadlines"
      description="Set when the project must be finished. The coordinator gives every unfinished task its own deadline, in dependency order, during working hours."
      accent="bg-butter"
      width="max-w-lg"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" onClick={save} disabled={busy || !valid}>
            {busy ? <Spinner /> : spread ? <Sparkles {...ICON} /> : <CalendarClock {...ICON} />} {spread ? 'Save & spread deadlines' : 'Save deadline'}
          </button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Work starts">
          <input type="datetime-local" className="input" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="Final deadline">
          <input type="datetime-local" className="input" value={due} onChange={(e) => setDue(e.target.value)} />
        </Field>
      </div>
      {!valid && start && due && <p className="mt-2 text-xs font-semibold text-rose-600 dark:text-rose-400">The final deadline must be after the start.</p>}
      <div className="mt-4 space-y-1 rounded-row bg-panel p-2">
        <label className="flex items-center gap-3 rounded-xl px-2.5 py-2">
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-bold text-fg">Spread deadlines across tasks</span>
            <span className="block text-xs text-fg-2">
              {open_} unfinished task{open_ === 1 ? '' : 's'} get a deadline each. Overwrites their current deadlines.
            </span>
          </span>
          <Switch checked={spread} onChange={setSpread} label="Spread deadlines across tasks" />
        </label>
        <label className="flex items-center gap-3 rounded-xl px-2.5 py-2">
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-bold text-fg">Tell owners on Telegram</span>
            <span className="block text-xs text-fg-2">Each verified teammate gets their new deadlines.</span>
          </span>
          <Switch checked={tell} onChange={setTell} disabled={!spread} label="Tell owners on Telegram" />
        </label>
      </div>
    </Modal>
  )
}
