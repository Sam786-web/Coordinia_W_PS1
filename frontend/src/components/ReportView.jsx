import { useCallback, useEffect, useState } from 'react'
import { AlertOctagon, AlertTriangle, ArrowRight, CalendarClock, CircleAlert, Download, Flag, RefreshCw } from 'lucide-react'
import PageHeader from './PageHeader'
import { Avatar, Spinner, StatusPill } from './ui'
import { api } from '../lib/api'
import { downloadReport } from '../lib/download'
import { ICON, availabilityLabel, cx, formatDeadline } from '../lib/utils'
import { useToast } from './Toast'
import { useAuth } from '../lib/auth'

function Section({ title, children, empty }) {
  return (
    <section className="card p-5">
      <h2 className="mb-3 text-sm font-medium text-fg">{title}</h2>
      {children || <p className="text-sm text-fg-3">{empty}</p>}
    </section>
  )
}

const SEVERITY = {
  critical: { icon: AlertOctagon, label: 'Critical', tone: 'bg-flamingo text-rose-950' },
  serious: { icon: AlertTriangle, label: 'Serious', tone: 'bg-[#FED7AA] text-orange-950' },
  warning: { icon: CircleAlert, label: 'Watch', tone: 'bg-butter text-amber-950' },
}
const PHASE_TONE = {
  Done: 'bg-mint text-emerald-950',
  'On track': 'bg-lavender text-[#1E1B4B]',
  'At risk': 'bg-[#FED7AA] text-orange-950',
  Late: 'bg-flamingo text-rose-950',
}

/** Phases along the dependency chain, each with progress, due date and status. */
function Milestones({ plan }) {
  if (!plan?.length) return null
  return (
    <section className="card p-5" aria-label="Milestones">
      <div className="mb-4 flex items-center gap-2">
        <Flag {...ICON} size={16} className="text-fg-3" />
        <h2 className="text-sm font-medium text-fg">Milestones</h2>
        <span className="text-xs text-fg-3">· phases in dependency order</span>
      </div>
      <ol className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {plan.map((ph) => (
          <li key={ph.index} className="rounded-row border border-line bg-panel p-3.5">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-semibold text-fg-3">Phase {ph.index}</p>
              <span className={cx('rounded-full px-2 py-0.5 text-[10px] font-bold', PHASE_TONE[ph.status])}>{ph.status}</span>
            </div>
            <p className="mt-1 text-sm font-extrabold text-fg">{ph.name}</p>
            <div className="mt-2.5 flex items-center gap-2">
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-subtle">
                <div className="h-full rounded-full bg-fg transition-all" style={{ width: `${ph.pct}%` }} />
              </div>
              <span className="text-xs font-bold tabular-nums text-fg">
                {ph.done}/{ph.total}
              </span>
            </div>
            <p className="mt-1.5 text-xs text-fg-3">{ph.due_at ? `Due ${formatDeadline(ph.due_at).label}` : 'No deadline'}</p>
            <ul className="mt-2 space-y-0.5">
              {ph.tasks.slice(0, 4).map((t) => (
                <li key={t.id} className={cx('truncate text-xs', t.status === 'Complete' ? 'text-fg-3 line-through' : t.status === 'Blocked' ? 'font-semibold text-rose-600 dark:text-rose-400' : 'text-fg-2')} title={`${t.title} · ${t.owner || 'Unassigned'} · ${t.status}`}>
                  {t.title}
                </li>
              ))}
              {ph.tasks.length > 4 && <li className="text-xs text-fg-3">+{ph.tasks.length - 4} more</li>}
            </ul>
          </li>
        ))}
      </ol>
    </section>
  )
}

/** Ranked risks, each with severity (icon + label, never color alone) and a next step. */
function Risks({ risks }) {
  return (
    <section className="card p-5" aria-label="Risks">
      <div className="mb-3 flex items-center gap-2">
        <AlertTriangle {...ICON} size={16} className="text-fg-3" />
        <h2 className="text-sm font-medium text-fg">Risks</h2>
        <span className="text-xs text-fg-3">· most serious first</span>
      </div>
      {!risks?.length ? (
        <p className="text-sm text-fg-3">No risks right now: nothing overdue, blocked or unproven.</p>
      ) : (
        <ul className="space-y-2">
          {risks.map((r, i) => {
            const sev = SEVERITY[r.severity] || SEVERITY.warning
            return (
              <li key={i} className="flex items-start gap-3 rounded-row border border-line bg-panel px-3.5 py-2.5">
                <span className={cx('mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold', sev.tone)}>
                  <sev.icon {...ICON} size={11} /> {sev.label}
                </span>
                <div className="min-w-0 flex-1 text-sm">
                  <p className="text-fg">
                    <span className="font-bold">{r.kind}</span>
                    {r.task && <span className="text-fg-2"> · "{r.task}"</span>}
                    {r.owner && <span className="text-fg-3"> · {r.owner}</span>}
                  </p>
                  <p className="text-xs text-fg-2">{r.detail}</p>
                  <p className="mt-0.5 text-xs font-semibold text-fg-3">Next: {r.action}</p>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

function Standup({ participants, tasks }) {
  return (
    <section className="card overflow-x-auto">
      <div className="px-5 pb-2 pt-5">
        <h2 className="text-sm font-medium text-fg">Standup</h2>
        <p className="mt-0.5 text-xs text-fg-3">What each teammate is working on, built from their latest replies.</p>
      </div>
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead>
          <tr className="border-y border-line text-xs text-fg-3">
            <th className="px-5 py-2.5 font-medium">Member</th>
            <th className="px-5 py-2.5 font-medium">Working on</th>
            <th className="px-5 py-2.5 font-medium">Latest update</th>
            <th className="px-5 py-2.5 font-medium">Availability</th>
          </tr>
        </thead>
        <tbody>
          {participants.map((p) => {
            const mine = tasks.filter((t) => t.owner_id === p.id)
            const focus = mine.find((t) => t.status === 'Blocked') || mine.find((t) => t.status === 'In Progress') || mine.find((t) => t.status === 'Pending') || mine[0]
            return (
              <tr key={p.id} className="border-b border-line last:border-0 align-top">
                <td className="px-5 py-3">
                  <span className="flex items-center gap-2.5">
                    <Avatar name={p.name} size="md" />
                    <span>
                      <span className="block font-medium text-fg">{p.name}</span>
                      <span className="block text-xs text-fg-3">{p.role}</span>
                    </span>
                  </span>
                </td>
                <td className="px-5 py-3">
                  {focus ? (
                    <>
                      <p className="text-fg">{focus.title}</p>
                      <StatusPill status={focus.status} className="mt-1.5" />
                    </>
                  ) : (
                    <span className="text-fg-3">No tasks</span>
                  )}
                </td>
                <td className="max-w-[320px] px-5 py-3 text-fg-2">
                  {focus?.progress_note || <span className="text-fg-3">No update yet</span>}
                  {focus?.status === 'Blocked' && <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">Blocker: {focus.blocker_reason}</p>}
                </td>
                <td className="whitespace-nowrap px-5 py-3 text-fg-2">{availabilityLabel(p.availability)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

export default function ReportView({ state, version }) {
  const toast = useToast()
  const auth = useAuth()
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [slot, setSlot] = useState(null)
  const [finding, setFinding] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setReport(await api.get('/api/report'))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load, version])

  const [downloading, setDownloading] = useState(false)
  const download = async (format = 'pdf') => {
    setDownloading(format)
    try {
      const name = await downloadReport(format)
      toast.success(format === 'pdf' ? 'PDF report downloaded' : 'Markdown report downloaded', name)
    } catch (e) {
      toast.error('Could not download the report', e.message)
    } finally {
      setDownloading(false)
    }
  }

  const findSlot = async () => {
    setFinding(true)
    try {
      setSlot(await api.post('/api/coordinate-slots', { availability: {}, notify: false }))
    } catch (e) {
      toast.error('Could not find a meeting time', e.message)
    } finally {
      setFinding(false)
    }
  }

  const m = report?.milestones
  const tone = !report ? '' : report.health_score >= 75 ? 'bg-emerald-500' : report.health_score >= 45 ? 'bg-amber-500' : 'bg-rose-500'
  return (
    <div className="space-y-6">
      <PageHeader
        title="Standup & Risk Report"
        description={`${state.active_project?.name}${report ? ` · generated ${new Date(report.generated_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}` : ''}`}
        actions={
          <>
            {auth.isLead && (
              <>
            <button className="btn-primary" onClick={() => download('pdf')} disabled={!!downloading || !report} title="Download the formatted progress report (PDF)">
              {downloading === 'pdf' ? <Spinner /> : <Download {...ICON} />} Download PDF
            </button>
            <button className="btn" onClick={() => download('md')} disabled={!!downloading || !report} title="Download as Markdown (to paste in a chat or submit as text)">
              {downloading === 'md' ? <Spinner /> : null} Markdown
            </button>
              </>
            )}
            <button className="btn" onClick={findSlot} disabled={finding}>
              {finding ? <Spinner /> : <CalendarClock {...ICON} />} Find meeting time
            </button>
            <button className="btn" onClick={load} disabled={loading}>
              {loading ? <Spinner /> : <RefreshCw {...ICON} />} Refresh
            </button>
          </>
        }
      />

      {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
      {!report && !error && (
        <p className="flex items-center gap-2 text-sm text-fg-2">
          <Spinner /> Building report…
        </p>
      )}

      {slot && (
        <section className="card flex flex-wrap items-center gap-4 p-5">
          <CalendarClock {...ICON} size={18} className="text-fg-3" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-fg">
              {slot.recommended_slot ? `${slot.purpose}: ${slot.recommended_slot.label}` : 'No overlapping time found'}
            </p>
            <p className="mt-0.5 text-sm text-fg-2">{slot.reason}</p>
          </div>
          {slot.recommended_slot && <span className="text-sm text-fg-3">with {slot.attendees.join(', ')}</span>}
        </section>
      )}

      {report && (
        <>
          <section className="card p-5">
            <div className="flex flex-wrap items-end gap-x-8 gap-y-4">
              <div>
                <p className="text-xs text-fg-3">Project health</p>
                <p className="mt-1 text-3xl font-semibold tabular-nums text-fg">
                  {report.health_score}
                  <span className="text-base font-normal text-fg-3"> / 100</span>
                </p>
              </div>
              <span className={cx('mb-1.5 rounded-xl px-2 py-0.5 text-xs font-medium text-white', tone)}>{report.health_label}</span>
              <div className="flex flex-1 flex-wrap justify-end gap-x-6 gap-y-1 text-sm text-fg-2">
                <span><b className="font-semibold text-fg">{m.complete}</b> complete</span>
                <span><b className="font-semibold text-fg">{m.in_progress}</b> in progress</span>
                <span><b className="font-semibold text-fg">{m.blocked}</b> blocked</span>
                <span><b className="font-semibold text-fg">{m.pending}</b> pending</span>
              </div>
            </div>
            <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-subtle">
              <div className={cx('h-full rounded-full transition-all', tone)} style={{ width: `${report.health_score}%` }} />
            </div>
            <p className="mt-4 text-sm text-fg-2">{report.executive_summary}</p>
          </section>

          <Milestones plan={report.milestone_plan} />
          <Risks risks={report.risks} />

          {report.verification && (
            <section className={cx('card flex flex-wrap items-center gap-x-8 gap-y-2 p-5', report.verification.truly_done && 'bg-mint/40')}>
              <div>
                <p className="text-xs text-fg-3">Proof of work</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums text-fg">
                  {report.verification.verified}
                  <span className="text-base font-normal text-fg-3"> / {report.verification.total} verified</span>
                </p>
              </div>
              <p className="min-w-0 flex-1 text-sm text-fg-2">
                {report.verification.truly_done
                  ? 'Every task is backed by a GitHub commit, a working link or a Team Lead review.'
                  : `${report.verification.complete} marked complete, ${report.verification.needs_proof} of them without proof yet. ` +
                    'Completions are verified by a matching commit, a link that opens, or the Team Lead approving them.'}
              </p>
            </section>
          )}

          <Standup participants={state.participants} tasks={state.tasks} />

          <div className="grid gap-4 lg:grid-cols-2">
            <Section title="Recommended next actions">
              <ol className="list-decimal space-y-2 pl-5 text-sm text-fg-2 marker:text-fg-3">
                {report.recommended_next_actions.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ol>
            </Section>
            <Section title="Bottlenecks" empty="Nothing is holding up other work.">
              {report.critical_path_bottlenecks.length > 0 && (
                <ul className="space-y-3">
                  {report.critical_path_bottlenecks.map((b) => (
                    <li key={b.task_id} className="text-sm">
                      <p className="font-medium text-fg">
                        {b.title} <span className="font-normal text-fg-3">· {b.owner}</span>
                      </p>
                      <p className={b.status === 'Blocked' ? 'text-rose-600 dark:text-rose-400' : 'text-fg-2'}>{b.reason}</p>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            <Section title="Work at risk" empty="No downstream work is at risk.">
              {report.cascading_risks.length > 0 && (
                <ul className="space-y-3">
                  {report.cascading_risks.map((c) => (
                    <li key={c.at_risk_task_id} className="text-sm text-fg-2">
                      <span className="font-medium text-fg">{c.at_risk_task}</span> ({c.owner}) is waiting on {c.caused_by_task}
                      {c.blocker_reason ? `: ${c.blocker_reason.toLowerCase()}` : ''}.
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            <Section title="Handoffs" empty="No handoffs between teammates.">
              {report.active_handoffs.length > 0 && (
                <ul className="space-y-2.5">
                  {report.active_handoffs.map((h, i) => (
                    <li key={i} className="flex items-center gap-2 text-sm">
                      <span className="font-medium text-fg">{h.from_owner}</span>
                      <ArrowRight {...ICON} size={13} className="shrink-0 text-fg-3" />
                      <span className="font-medium text-fg">{h.to_owner}</span>
                      <span className="min-w-0 flex-1 truncate text-fg-2">{h.type === 'BLOCKER' ? h.need : h.to_task}</span>
                      <span className={cx('shrink-0 text-xs', h.state === 'Delivered' ? 'text-emerald-600 dark:text-emerald-400' : h.state === 'Waiting' ? 'text-fg-3' : 'text-rose-600 dark:text-rose-400')}>
                        {h.state}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          </div>
        </>
      )}
    </div>
  )
}
