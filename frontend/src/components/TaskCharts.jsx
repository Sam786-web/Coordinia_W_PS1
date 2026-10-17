import { useState } from 'react'
import { Table2 } from 'lucide-react'
import { ICON, cx } from '../lib/utils'

// Task status charts: a donut (part-to-whole at a glance, 4 segments) and workload per teammate
// (horizontal stacked bars). Colors come from validated CSS tokens; text always uses text tokens.

const STATUSES = [
  { key: 'Complete', label: 'Done', color: 'var(--st-complete)' },
  { key: 'In Progress', label: 'In progress', color: 'var(--st-progress)' },
  { key: 'Blocked', label: 'Blocked', color: 'var(--st-blocked)' },
  { key: 'Pending', label: 'Not started', color: 'var(--st-pending)' },
]

function countBy(tasks) {
  return Object.fromEntries(STATUSES.map((s) => [s.key, tasks.filter((t) => t.status === s.key).length]))
}

function Legend({ counts, total }) {
  return (
    <ul className="space-y-1.5">
      {STATUSES.map((s) => (
        <li key={s.key} className="flex items-center gap-2 text-xs">
          <span className="h-2.5 w-2.5 shrink-0 rounded-[3px]" style={{ background: s.color }} />
          <span className="flex-1 font-semibold text-fg-2">{s.label}</span>
          <span className="font-bold tabular-nums text-fg">{counts[s.key]}</span>
          <span className="w-9 text-right tabular-nums text-fg-3">{total ? Math.round((100 * counts[s.key]) / total) : 0}%</span>
        </li>
      ))}
    </ul>
  )
}

function StatusTable({ rows, columns }) {
  return (
    <table className="w-full text-left text-xs">
      <thead>
        <tr className="border-b border-line text-fg-3">
          {columns.map((c, i) => (
            <th key={c} className={cx('py-1.5 font-semibold', i > 0 && 'text-right')}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r[0]} className="border-b border-line/60 last:border-0">
            {r.map((v, i) => (
              <td key={i} className={cx('py-1.5', i === 0 ? 'font-semibold text-fg' : 'text-right tabular-nums text-fg-2')}>
                {v}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function CardHeader({ title, sub, table, onTable }) {
  return (
    <div className="mb-3 flex items-start gap-2">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-bold text-fg">{title}</p>
        <p className="text-xs text-fg-3">{sub}</p>
      </div>
      <button
        onClick={onTable}
        aria-pressed={table}
        className={cx('icon-btn h-7 w-7', table && 'bg-subtle text-fg')}
        title={table ? 'Show chart' : 'Show as table'}
        aria-label={table ? 'Show chart' : 'Show as table'}
      >
        <Table2 {...ICON} size={14} />
      </button>
    </div>
  )
}

/** Arc path for a donut segment from angle a0 to a1 (radians, 0 = 12 o'clock). */
function arc(cx0, cy0, r, a0, a1) {
  const p = (a) => [cx0 + r * Math.sin(a), cy0 - r * Math.cos(a)]
  const [x0, y0] = p(a0)
  const [x1, y1] = p(a1)
  return `M ${x0} ${y0} A ${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1} ${y1}`
}

export function StatusDonut({ tasks }) {
  const [hover, setHover] = useState(null)
  const [table, setTable] = useState(false)
  const counts = countBy(tasks)
  const total = tasks.length
  const R = 46
  const GAP = 0.045 // radians: the 2px surface gap between segments
  const segments = []
  let a = 0
  for (const s of STATUSES) {
    const n = counts[s.key]
    if (!n) continue
    const sweep = (2 * Math.PI * n) / total
    const only = n === total
    segments.push({ ...s, n, a0: a + (only ? 0 : GAP / 2), a1: a + sweep - (only ? 0.0001 : GAP / 2) })
    a += sweep
  }
  const focus = hover ? segments.find((s) => s.key === hover) : null
  const pctDone = total ? Math.round((100 * counts.Complete) / total) : 0

  return (
    <section className="bento col-span-12 p-5 md:col-span-5" aria-label="Task status">
      <CardHeader title="Task status" sub={`${total} task${total === 1 ? '' : 's'} in this project`} table={table} onTable={() => setTable((t) => !t)} />
      {table ? (
        <StatusTable columns={['Status', 'Tasks', 'Share']} rows={STATUSES.map((s) => [s.label, counts[s.key], `${total ? Math.round((100 * counts[s.key]) / total) : 0}%`])} />
      ) : (
        <div className="flex items-center gap-5">
          <svg viewBox="0 0 120 120" className="h-32 w-32 shrink-0" role="img" aria-label={STATUSES.map((s) => `${s.label} ${counts[s.key]}`).join(', ')}>
            <circle cx="60" cy="60" r={R} fill="none" stroke="rgb(var(--line))" strokeWidth="14" opacity={total ? 0 : 1} />
            {segments.map((s) => (
              <path
                key={s.key}
                d={arc(60, 60, R, s.a0, s.a1)}
                fill="none"
                stroke={s.color}
                strokeWidth={hover === s.key ? 17 : 14}
                strokeLinecap="butt"
                opacity={hover && hover !== s.key ? 0.35 : 1}
                className="cursor-default transition-all duration-200"
                onMouseEnter={() => setHover(s.key)}
                onMouseLeave={() => setHover(null)}
              >
                <title>{`${s.label}: ${s.n} of ${total}`}</title>
              </path>
            ))}
            <text x="60" y="58" textAnchor="middle" className="fill-fg text-[22px] font-extrabold">
              {focus ? focus.n : `${pctDone}%`}
            </text>
            <text x="60" y="74" textAnchor="middle" className="fill-fg-3 text-[9px] font-semibold">
              {focus ? focus.label.toLowerCase() : 'done'}
            </text>
          </svg>
          <div className="min-w-0 flex-1">
            <Legend counts={counts} total={total} />
          </div>
        </div>
      )}
    </section>
  )
}

export function WorkloadBars({ tasks, participants }) {
  const [tip, setTip] = useState(null)
  const [table, setTable] = useState(false)
  const rows = participants
    .map((p) => {
      const mine = tasks.filter((t) => t.owner_id === p.id)
      return { p, counts: countBy(mine), total: mine.length }
    })
    .filter((r) => r.total)
  const max = Math.max(1, ...rows.map((r) => r.total))

  return (
    <section className="bento relative col-span-12 p-5 md:col-span-7" aria-label="Workload by teammate">
      <CardHeader title="Workload by teammate" sub="Tasks per person, by status" table={table} onTable={() => setTable((t) => !t)} />
      {table ? (
        <StatusTable
          columns={['Teammate', ...STATUSES.map((s) => s.label), 'Total']}
          rows={rows.map((r) => [r.p.name, ...STATUSES.map((s) => r.counts[s.key]), r.total])}
        />
      ) : (
        <>
          <div className="mb-3 flex flex-wrap gap-x-3 gap-y-1">
            {STATUSES.map((s) => (
              <span key={s.key} className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-fg-2">
                <span className="h-2 w-2 rounded-[3px]" style={{ background: s.color }} /> {s.label}
              </span>
            ))}
          </div>
          <div className="space-y-2.5">
            {rows.map((r) => (
              <div key={r.p.id} className="flex items-center gap-3">
                <span className="w-16 shrink-0 truncate text-xs font-semibold text-fg-2" title={r.p.name}>
                  {r.p.name.split(' ')[0]}
                </span>
                <div className="flex h-3 min-w-0 flex-1">
                  <div className="flex h-full gap-[2px]" style={{ width: `${(100 * r.total) / max}%` }}>
                    {STATUSES.filter((s) => r.counts[s.key]).map((s, i, arr) => (
                      <div
                        key={s.key}
                        className={cx('h-full transition-opacity', i === 0 && 'rounded-l-[4px]', i === arr.length - 1 && 'rounded-r-[4px]', tip && tip.key !== `${r.p.id}-${s.key}` && 'opacity-40')}
                        style={{ flexGrow: r.counts[s.key], flexBasis: 0, background: s.color }}
                        onMouseEnter={(e) => {
                          const box = e.currentTarget.closest('section').getBoundingClientRect()
                          const me = e.currentTarget.getBoundingClientRect()
                          setTip({ key: `${r.p.id}-${s.key}`, x: me.left - box.left + me.width / 2, y: me.top - box.top, text: `${r.p.name} · ${s.label}: ${r.counts[s.key]}` })
                        }}
                        onMouseLeave={() => setTip(null)}
                      />
                    ))}
                  </div>
                </div>
                <span className="w-12 shrink-0 text-right text-xs font-bold tabular-nums text-fg">{r.total}</span>
              </div>
            ))}
            {!rows.length && <p className="py-4 text-center text-sm text-fg-3">No tasks assigned yet.</p>}
          </div>
          {tip && (
            <span
              className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-lg bg-fg px-2.5 py-1 text-[11px] font-semibold text-bg shadow-lg"
              style={{ left: tip.x, top: tip.y - 6 }}
            >
              {tip.text}
            </span>
          )}
        </>
      )}
    </section>
  )
}
