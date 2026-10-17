import { useEffect, useState } from 'react'
import { ArrowRight, Building2, Copy, Download, Github, GraduationCap, Inbox, Plus, Users } from 'lucide-react'
import { Spinner } from './ui'
import { api } from '../lib/api'
import { downloadReport } from '../lib/download'
import { ICON, cx, formatDeadline } from '../lib/utils'
import { useToast } from './Toast'

function repoName(url) {
  const m = /github\.com\/([^/]+\/[^/#?]+)/.exec(url || '')
  return m ? m[1].replace(/\.git$/, '') : null
}

function ProjectCard({ p, onOpened }) {
  const toast = useToast()
  const [busy, setBusy] = useState(null)
  const repo = repoName(p.github_repo_url)
  const Icon = p.workspace_type === 'company' ? Building2 : GraduationCap

  const open = async () => {
    setBusy('open')
    try {
      await api.post(`/api/projects/${p.id}/activate`)
      toast.info('Switched project', p.name)
      onOpened?.()
    } catch (e) {
      toast.error('Could not open the project', e.message)
    } finally {
      setBusy(null)
    }
  }
  const report = async () => {
    setBusy('report')
    try {
      const name = await downloadReport('pdf', p.id)
      toast.success('Report downloaded', name)
    } catch (e) {
      toast.error('Could not create the report', e.message)
    } finally {
      setBusy(null)
    }
  }
  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(p.invite_code)
      toast.success('Invite code copied', p.invite_code)
    } catch {
      toast.info('Invite code', p.invite_code)
    }
  }

  return (
    <article className={cx('flex w-72 shrink-0 flex-col rounded-row border bg-surface p-4 transition', p.is_current ? 'border-coral/60 ring-2 ring-coral/20' : 'border-line hover:border-line-strong')}>
      <div className="flex items-start gap-2">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-subtle text-fg-2">
          <Icon {...ICON} size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-extrabold text-fg" title={p.name}>
            {p.name}
          </p>
          {repo ? (
            <a href={p.github_repo_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 truncate text-[11px] font-semibold text-fg-3 hover:text-fg-2">
              <Github {...ICON} size={11} /> {repo}
            </a>
          ) : (
            <p className="text-[11px] text-fg-3">No repo linked</p>
          )}
        </div>
        {p.is_current && <span className="shrink-0 rounded-full bg-coral/15 px-2 py-0.5 text-[10px] font-bold text-coral">Open</span>}
      </div>

      <div className="mt-3 flex items-center gap-2">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-subtle">
          <div className="h-full rounded-full bg-fg transition-all" style={{ width: `${p.pct}%` }} />
        </div>
        <span className="text-xs font-bold tabular-nums text-fg">
          {p.done}/{p.tasks}
        </span>
      </div>

      <div className="mt-2.5 flex flex-wrap gap-1.5 text-[11px] font-semibold">
        <span className="inline-flex items-center gap-1 rounded-full bg-subtle px-2 py-0.5 text-fg-2" title={`${p.verified_members} verified on Telegram`}>
          <Users {...ICON} size={11} /> {p.members} member{p.members === 1 ? '' : 's'}
        </span>
        {p.blocked > 0 && <span className="rounded-full bg-flamingo px-2 py-0.5 text-rose-950">{p.blocked} blocked</span>}
        {p.overdue > 0 && <span className="rounded-full bg-[#FED7AA] px-2 py-0.5 text-orange-950">{p.overdue} overdue</span>}
        {p.pending_requests > 0 && (
          <span className="inline-flex items-center gap-1 rounded-full bg-lavender px-2 py-0.5 text-[#1E1B4B]">
            <Inbox {...ICON} size={11} /> {p.pending_requests} request{p.pending_requests === 1 ? '' : 's'}
          </span>
        )}
        {p.tasks > 0 && <span className="rounded-full bg-subtle px-2 py-0.5 text-fg-3">{p.verified}/{p.tasks} verified</span>}
      </div>

      <div className="mt-2.5 flex items-center justify-between gap-2 text-[11px] text-fg-3">
        <button onClick={copyCode} className="inline-flex items-center gap-1 font-mono font-bold text-fg-2 hover:text-fg" title="Copy the invite code for this project">
          <Copy {...ICON} size={11} /> {p.invite_code}
        </button>
        {p.due_at && <span>Due {formatDeadline(p.due_at).label}</span>}
      </div>

      <div className="mt-3 flex gap-2">
        {!p.is_current && (
          <button className="btn flex-1 px-3 py-1.5 text-xs" onClick={open} disabled={!!busy}>
            {busy === 'open' ? <Spinner /> : <ArrowRight {...ICON} size={13} />} Open
          </button>
        )}
        <button className="btn flex-1 px-3 py-1.5 text-xs" onClick={report} disabled={!!busy} title="Download this project's progress report (PDF)">
          {busy === 'report' ? <Spinner /> : <Download {...ICON} size={13} />} Report
        </button>
      </div>
    </article>
  )
}

/** Team Lead: every project at a glance (each with its own repo, invite code, members and PINs) and its report. */
export default function ProjectsBoard({ version, onCreate }) {
  const [projects, setProjects] = useState(null)
  useEffect(() => {
    api.get('/api/projects/summary').then(setProjects, () => setProjects([]))
  }, [version])
  if (!projects) return null
  return (
    <section className="bento p-5" aria-label="Your projects">
      <div className="mb-3 flex items-center justify-between gap-2 px-1">
        <div>
          <h2 className="text-base font-extrabold tracking-tight text-fg">Your projects</h2>
          <p className="text-xs font-semibold text-fg-3">
            {projects.length} project{projects.length === 1 ? '' : 's'} · each has its own repo, invite code, members and PINs
          </p>
        </div>
      </div>
      <div className="-mx-1 flex gap-3 overflow-x-auto px-1 pb-1">
        {projects.map((p) => (
          <ProjectCard key={p.id} p={p} />
        ))}
        <button
          onClick={onCreate}
          className="flex w-56 shrink-0 flex-col items-center justify-center gap-2 rounded-row border border-dashed border-line-strong p-4 text-sm font-bold text-fg-2 transition hover:border-coral hover:text-coral"
        >
          <span className="grid h-9 w-9 place-items-center rounded-full bg-subtle">
            <Plus {...ICON} size={16} />
          </span>
          New project from a GitHub repo
        </button>
      </div>
    </section>
  )
}
