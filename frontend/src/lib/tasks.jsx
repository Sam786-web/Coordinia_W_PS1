import { BadgeCheck, Check, CircleAlert, GitCommitHorizontal, Link2, Paperclip } from 'lucide-react'
import { ICON } from './utils'

const TYPE_LABEL = { github_code: 'Code task', design_docs: 'Design & docs', general: 'General' }

/** Person-based auth: the Team Lead manages every task; a member only their own. */
export function canUpdate(task, auth) {
  if (!auth?.session) return false
  if (auth.isLead) return true
  return !!auth.participant && task.owner_id === auth.participant.id
}

/** Proof of progress: a real GitHub commit for code, a deliverable link for everything else. */
export function VerificationBadge({ task, numbers }) {
  if (task.github_verified && task.last_commit_info) {
    const sha = task.last_commit_info.split(' ')[0]
    return (
      <span className="inline-flex items-center gap-1 text-xs font-bold text-emerald-600 dark:text-emerald-400" title={`Verified by commit ${task.last_commit_info}`}>
        <GitCommitHorizontal {...ICON} size={13} /> GitHub Verified <Check {...ICON} size={13} />
        <span className="font-mono font-medium text-fg-3">{sha}</span>
      </span>
    )
  }
  if (isProofFile(task.deliverable_url)) {
    return (
      <a href={task.deliverable_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="inline-flex items-center gap-1 text-xs font-bold text-emerald-600 hover:underline dark:text-emerald-400" title="File sent as proof on Telegram">
        <Paperclip {...ICON} size={13} /> File attached
      </a>
    )
  }
  if (task.deliverable_url) {
    return (
      <a href={task.deliverable_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="inline-flex items-center gap-1 text-xs font-bold text-emerald-600 hover:underline dark:text-emerald-400" title={task.deliverable_url}>
        <Link2 {...ICON} size={13} /> Deliverable linked
      </a>
    )
  }
  if (task.proof === 'lead') {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-bold text-sky-600 dark:text-sky-400" title="The Team Lead reviewed and approved this completion">
        <BadgeCheck {...ICON} size={13} /> Approved by lead
      </span>
    )
  }
  if (task.proof === 'self') {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-bold text-amber-600 dark:text-amber-400" title="Marked done without a commit, link or review yet">
        <CircleAlert {...ICON} size={13} /> Needs proof
      </span>
    )
  }
  if (task.depends_on_task_id && numbers?.[task.depends_on_task_id]) {
    return <span className="text-xs font-semibold text-fg-2">Depends on #{numbers[task.depends_on_task_id]}</span>
  }
  return <span className="text-xs font-semibold text-fg-3">{TYPE_LABEL[task.task_type] || 'Task'}</span>
}

/** Same scoring as the backend report: completion, minus blockers and the work they put at risk. */
export function projectHealth(tasks, risk) {
  if (!tasks.length) return { health: 100, completion: 0, level: 'Healthy' }
  // Honest progress: a completion only counts fully once it's verified (commit, link or lead review);
  // a bare "done" counts half, the same as work in progress
  const verified = tasks.filter((t) => isVerified(t)).length
  const claimed = tasks.filter((t) => t.status === 'Complete' && !isVerified(t)).length
  const active = tasks.filter((t) => t.status === 'In Progress').length
  const blocked = tasks.filter((t) => t.status === 'Blocked').length
  const atRisk = Object.keys(risk || {}).length
  const done = verified + 0.5 * claimed
  const health = Math.max(0, Math.min(100, Math.round(50 + (50 * done) / tasks.length - 15 * blocked - 8 * atRisk)))
  const completion = Math.round((100 * (done + 0.5 * active)) / tasks.length)
  return {
    health, completion, verified, claimed,
    trulyDone: verified === tasks.length,
    level: health >= 75 ? 'Healthy' : health >= 45 ? 'At Risk' : 'Blocked Risk',
  }
}

/** A completion backed by a commit, a working link or the Team Lead's review. */
export function isVerified(task) {
  return task.status === 'Complete' && ['github', 'deliverable', 'lead'].includes(task.proof)
}

/** A file a member sent to the bot as proof (served by the backend). */
export function isProofFile(url) {
  return typeof url === 'string' && url.startsWith('/api/proof-files/')
}

export function isImageFile(url) {
  return isProofFile(url) && /\.(png|jpe?g|gif|webp)$/i.test(url)
}
