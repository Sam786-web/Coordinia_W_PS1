import { useEffect, useState } from 'react'
import { Building2, Github, GraduationCap, Plus, X } from 'lucide-react'
import Modal from '../Modal'
import { Field, Segmented, Spinner, Switch } from '../ui'
import { api } from '../../lib/api'
import { ICON } from '../../lib/utils'
import { useToast } from '../Toast'

const STEPS = ['Reading the repository on GitHub…', 'Dividing the work by role…', 'Briefing each member…']

function MembersEditor({ members, onChange }) {
  const update = (i, key, value) => onChange(members.map((m, j) => (j === i ? { ...m, [key]: value } : m)))
  return (
    <div className="space-y-2">
      {members.map((m, i) => (
        <div key={i} className="flex gap-2">
          <input className="input" value={m.name} onChange={(e) => update(i, 'name', e.target.value)} placeholder="Name" aria-label={`Member ${i + 1} name`} />
          <input className="input" value={m.role} onChange={(e) => update(i, 'role', e.target.value)} placeholder="Role, e.g. Frontend Developer" aria-label={`Member ${i + 1} role`} />
          <button type="button" className="icon-btn h-[38px] w-[38px] shrink-0" onClick={() => onChange(members.filter((_, j) => j !== i))} aria-label={`Remove member ${i + 1}`} disabled={members.length === 1}>
            <X {...ICON} />
          </button>
        </div>
      ))}
      <button type="button" className="btn-ghost px-1.5 text-xs" onClick={() => onChange([...members, { name: '', role: '' }])}>
        <Plus {...ICON} size={14} /> Add member
      </button>
    </div>
  )
}

export default function CreateProjectModal({ open, onClose, participants, workspace, canCorporate = false, initialTab = 'github', initialRepo = '' }) {
  const toast = useToast()
  const [tab, setTab] = useState(initialTab)
  const [repo, setRepo] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [dueAt, setDueAt] = useState('')
  const [ws, setWs] = useState(workspace || 'college')
  const [members, setMembers] = useState([{ name: '', role: '' }])
  const [planWithAI, setPlanWithAI] = useState(true)
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState(0)

  useEffect(() => {
    if (!open) return
    setTab(initialTab)
    setRepo(initialRepo || '')
    setWs(canCorporate ? 'company' : 'college')
    setMembers(participants.length ? participants.map((p) => ({ name: p.name, role: p.role || '' })) : [{ name: '', role: '' }])
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!busy || tab !== 'github') return
    setStep(0)
    const id = setInterval(() => setStep((s) => Math.min(s + 1, STEPS.length - 1)), 1400)
    return () => clearInterval(id)
  }, [busy, tab])

  const team = members.map((m) => ({ name: m.name.trim(), role: m.role.trim() })).filter((m) => m.name)
  const validRepo = /github\.com\/[\w.-]+\/[\w.-]+|^[\w.-]+\/[\w.-]+$/.test(repo.trim())

  const submit = async () => {
    setBusy(true)
    try {
      if (tab === 'github') {
        const out = await api.post('/api/projects/from-github', {
          project_name: name.trim(), description: description.trim(), github_repo_url: repo.trim(), workspace_type: ws, team_members: team, due_at: dueAt || null,
        })
        const sent = out.notifications.filter((n) => n.delivered).length
        toast.success(`Created "${out.project.name}"`, `${out.tasks.length} tasks across ${out.participants.length} members. ${sent ? `${sent} briefed on Telegram.` : 'Assignments logged (Telegram not linked yet).'}`)
        if (out.repo.resolved_from) toast.info('Repository found', `Used ${out.repo.full_name} (closest match to the link you pasted).`)
        else if (!out.repo.fetched) toast.info("Couldn't read the repository", 'It may be private. Tasks were planned from its name and your description.')
      } else if (planWithAI) {
        const out = await api.post('/api/ai/generate-project', { project_name: name.trim(), description: description.trim(), teammates: team.map((m) => m.name), workspace_type: ws, due_at: dueAt || null })
        toast.success(`Created "${out.project.name}"`, `${out.tasks.length} tasks planned.`)
      } else {
        const p = await api.post('/api/projects', { name: name.trim(), description: description.trim(), workspace_type: ws, due_at: dueAt || null })
        for (const m of team) await api.post('/api/participants', { name: m.name, role: m.role })
        toast.success(`Created "${p.name}"`, 'Add tasks to get started.')
      }
      setName('')
      setDescription('')
      setDueAt('')
      setRepo('')
      onClose()
    } catch (e) {
      toast.error('Could not create project', e.message)
    } finally {
      setBusy(false)
    }
  }

  const canSubmit = team.length > 0 && (tab === 'github' ? validRepo : name.trim())
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New project"
      description="Paste a GitHub repo and the AI splits it into tasks for your team, or describe the project instead."
      width="max-w-2xl"
      footer={
        <>
          {busy && tab === 'github' && <span className="mr-auto flex items-center gap-2 text-sm text-fg-2"><Spinner /> {STEPS[step]}</span>}
          <button className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn-primary" onClick={submit} disabled={busy || !canSubmit}>
            {busy ? <Spinner /> : tab === 'github' ? <Github {...ICON} /> : null}
            {tab === 'github' ? 'Analyze repo & split tasks' : planWithAI ? 'Create & plan tasks' : 'Create project'}
          </button>
        </>
      }
    >
      <Segmented
        className="mb-5"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'github', label: 'From GitHub repo', icon: Github },
          { value: 'describe', label: 'From a description' },
        ]}
      />
      <div className="space-y-4">
        {tab === 'github' && (
          <Field label="GitHub repository URL" hint="Public repos are read live: files, languages, README and dependencies.">
            <input className="input font-mono text-[13px]" value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="https://github.com/owner/repository" autoFocus spellCheck={false} />
          </Field>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={tab === 'github' ? 'Project name (optional)' : 'Project name'}>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={tab === 'github' ? 'Taken from the repo if empty' : 'e.g. Smart Canteen'} />
          </Field>
          <Field label="Workspace">
            {canCorporate ? (
              <p className="flex h-[42px] items-center gap-2 rounded-2xl bg-butter px-3.5 text-xs font-bold text-amber-950" title="Company Team Lead accounts work in the Corporate Workspace">
                <Building2 {...ICON} size={14} /> Corporate sprint project
              </p>
            ) : (
              <p className="flex h-[42px] items-center gap-2 rounded-2xl bg-lavender px-3.5 text-xs font-bold text-[#1E1B4B]" title="Corporate Workspace requires a Company Team Lead account">
                <GraduationCap {...ICON} size={14} /> College Capstone project
              </p>
            )}
          </Field>
        </div>
        <Field label="Project deadline (optional)" hint="The coordinator spreads task deadlines up to this date, in dependency order. You can change it later.">
          <input type="datetime-local" className="input" value={dueAt} onChange={(e) => setDueAt(e.target.value)} />
        </Field>
        <Field label={tab === 'github' ? 'Goal (optional)' : 'Description'}>
          <textarea className="input min-h-[64px] resize-y" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What should the team deliver?" />
        </Field>
        <div>
          <span className="label">Team members</span>
          <MembersEditor members={members} onChange={setMembers} />
          <p className="mt-1.5 text-xs text-fg-3">Roles decide who gets which task. Members already linked to Telegram are briefed automatically.</p>
        </div>
        {tab === 'describe' && (
          <div className="flex items-start gap-3 rounded-2xl border border-line p-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-fg">Plan tasks automatically</p>
              <p className="mt-0.5 text-xs text-fg-2">Creates 4-5 dependent tasks across the members. Turn off for a blank project.</p>
            </div>
            <Switch checked={planWithAI} onChange={setPlanWithAI} label="Plan tasks automatically" />
          </div>
        )}
      </div>
    </Modal>
  )
}
