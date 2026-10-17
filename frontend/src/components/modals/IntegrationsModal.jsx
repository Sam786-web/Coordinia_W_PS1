import { useEffect, useState } from 'react'
import Modal from '../Modal'
import { Field, Segmented, Spinner, StatusPill } from '../ui'
import { api } from '../../lib/api'
import { useToast } from '../Toast'

const SAMPLE_CSV = `Title,Owner,Role,Status,Deadline,Depends On
Design auth API,Asha,Backend Dev,In Review,Today 6:00 PM,
Build login screen,Dev,Frontend Dev,To Do,Tomorrow 11:00 AM,Design auth API
Wire OAuth providers,Asha,Backend Dev,Blocked,Tomorrow 1:00 PM,Design auth API
Release to staging,Ishaan,DevOps,To Do,Tomorrow 5:00 PM,Build login screen`

export default function IntegrationsModal({ open, onClose, participants, tasks = [], onCreateFromRepo, initialTab, canCorporate = false }) {
  const toast = useToast()
  const [tab, setTab] = useState('csv')
  const [csv, setCsv] = useState(SAMPLE_CSV)
  const [newProject, setAsNew] = useState(true)
  // An imported sprint becomes a Corporate project, so College leads import into their current project
  const asNew = canCorporate && newProject
  const [projectName, setProjectName] = useState('Sprint 42 (Jira import)')
  const [author, setAuthor] = useState('')
  const repo = 'dashboard-test'
  const [commit, setCommit] = useState('')
  const [taskId, setTaskId] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  useEffect(() => {
    if (open) {
      setAuthor((a) => (participants.some((p) => p.name === a) ? a : participants[0]?.name || ''))
      setResult(null)
      if (initialTab) setTab(initialTab)
    }
  }, [open, participants]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setTaskId('')
  }, [author])

  const importCsv = async () => {
    setBusy(true)
    try {
      const out = await api.post('/api/import-csv', { csv_text: csv, project_name: asNew ? projectName.trim() : null })
      setResult({ kind: 'csv', out })
      toast.success(`Imported ${out.imported} tasks`, `Into "${out.project.name}"`)
    } catch (e) {
      toast.error('Import failed', e.message)
    } finally {
      setBusy(false)
    }
  }

  // A pasted repository link is not a commit message: offer project creation instead
  const repoLink = /^\s*(https?:\/\/)?(www\.)?github\.com\/[\w.-]+\/[\w.-]+\/?\s*$/i.test(commit) ? commit.trim() : null
  const authorTasks = tasks.filter((t) => t.owner_name === author)

  const sendCommit = async () => {
    if (repoLink) return
    setBusy(true)
    try {
      const out = await api.post('/api/webhooks/github', { author, commit_message: commit, repo, task_id: taskId ? Number(taskId) : null })
      const r = out.results[0]
      setResult({ kind: 'github', r })
      if (r.status === 'processed') toast.success(`Commit from ${r.participant} processed`, `Task status: ${r.result.new_status}`)
      else toast.error('Commit ignored', r.reason)
    } catch (e) {
      toast.error('Webhook failed', e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Import CSV / Test a commit"
      description="Bring in a sprint from Jira or Linear, or test how the coordinator reads a commit message. To split a repository into tasks, use Create Project from GitHub Repo."
      width="max-w-2xl"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Close
          </button>
          {tab === 'csv' ? (
            <button className="btn-primary" onClick={importCsv} disabled={busy || !csv.trim() || (asNew && !projectName.trim())}>
              {busy && <Spinner />} Import tasks
            </button>
          ) : (
            <button className="btn-primary" onClick={sendCommit} disabled={busy || !commit.trim() || !author || !!repoLink}>
              {busy && <Spinner />} Send commit
            </button>
          )}
        </>
      }
    >
      <Segmented
        className="mb-4"
        value={tab}
        onChange={(t) => {
          setTab(t)
          setResult(null)
        }}
        options={[
          { value: 'csv', label: 'CSV import' },
          { value: 'github', label: 'Test a commit message' },
        ]}
      />

      {tab === 'csv' ? (
        <div className="space-y-4">
          <Field label="CSV" hint="Columns: Title, Owner, Role, Status, Deadline, Depends On. Jira statuses like To Do, In Review and Done are mapped automatically.">
            <textarea className="input min-h-[150px] resize-y font-mono text-xs" value={csv} onChange={(e) => setCsv(e.target.value)} spellCheck={false} />
          </Field>
          {canCorporate ? (
            <label className="flex items-center gap-2 text-sm text-fg-2">
              <input type="checkbox" checked={asNew} onChange={(e) => setAsNew(e.target.checked)} className="h-4 w-4 accent-indigo-600" />
              Import as a new Corporate sprint project
            </label>
          ) : (
            <p className="text-xs text-fg-3">Tasks are added to your current College project. New sprint projects need a Company Team Lead account.</p>
          )}
          {asNew && (
            <Field label="Project name">
              <input className="input" value={projectName} onChange={(e) => setProjectName(e.target.value)} />
            </Field>
          )}
          {result?.kind === 'csv' && (
            <div className="rounded-2xl border border-line bg-subtle p-3 text-sm text-fg-2">
              Imported <b className="font-medium text-fg">{result.out.imported}</b> tasks for {result.out.participants.length} members into "{result.out.project.name}".
              {result.out.errors.map((e) => (
                <p key={e} className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                  {e}
                </p>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Author">
              <select className="input" value={author} onChange={(e) => setAuthor(e.target.value)}>
                {participants.map((p) => (
                  <option key={p.id} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Updates task">
              <select className="input" value={taskId} onChange={(e) => setTaskId(e.target.value)}>
                <option value="">Author's most urgent task</option>
                {authorTasks.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.title}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="Commit message" hint="Real GitHub push webhooks sent to /api/webhooks/github work the same way.">
            <textarea
              className="input min-h-[80px] resize-y font-mono text-xs"
              value={commit}
              onChange={(e) => setCommit(e.target.value)}
              placeholder="Connected payment webhook, blocked on AWS KMS keys from Priya"
            />
          </Field>
          {repoLink && (
            <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-indigo-500/25 bg-indigo-500/10 p-3 text-sm">
              <p className="min-w-0 flex-1 text-fg">That's a repository link, not a commit message.</p>
              <button
                className="btn-primary"
                onClick={() => {
                  onClose()
                  onCreateFromRepo?.(repoLink)
                }}
              >
                Create project from this repo
              </button>
            </div>
          )}
          {result?.kind === 'github' && (
            <div className="rounded-2xl border border-line bg-subtle p-3 text-sm text-fg-2">
              {result.r.status === 'processed' ? (
                <div className="space-y-1.5">
                  <p className="flex items-center gap-2">
                    {result.r.participant}'s task is now <StatusPill status={result.r.result.new_status} />
                  </p>
                  {result.r.result.blocker_reason && <p>Blocker: {result.r.result.blocker_reason}</p>}
                  {result.r.result.auto_relay_target_name && <p>Asked {result.r.result.auto_relay_target_name} for help.</p>}
                </div>
              ) : (
                <p>Ignored: {result.r.reason}</p>
              )}
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}
