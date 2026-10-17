import { Loader2 } from 'lucide-react'
import { ICON, STATUS, avatarTint, cx, initials } from '../lib/utils'

const AVATAR_SIZES = {
  xs: 'h-5 w-5 text-[9px]',
  sm: 'h-7 w-7 text-[10px]',
  md: 'h-9 w-9 text-xs',
  lg: 'h-11 w-11 text-sm',
}

export function Avatar({ name, size = 'sm', className }) {
  return (
    <span title={name} className={cx('inline-flex shrink-0 select-none items-center justify-center rounded-full font-bold', avatarTint(name), AVATAR_SIZES[size], className)}>
      {initials(name)}
    </span>
  )
}

/** Pastel status pill (Complete mint, In Progress butter, Blocked flamingo, Pending lavender). */
export function StatusPill({ status, className, label }) {
  const s = STATUS[status] || STATUS.Pending
  return <span className={cx('inline-flex items-center whitespace-nowrap rounded-full px-3 py-1 text-xs font-bold', s.pill, className)}>{label || s.label}</span>
}

export function Dot({ className }) {
  return <span className={cx('inline-block h-1.5 w-1.5 shrink-0 rounded-full', className)} />
}

export function Spinner({ className }) {
  return <Loader2 {...ICON} className={cx('animate-spin', className)} />
}

/** Brand mark: coral rounded badge with two linked rings. */
export function Logo({ className }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <rect width="32" height="32" rx="10" fill="#FF6B4A" fillOpacity="0.15" />
      <circle cx="12.5" cy="16" r="5" fill="none" stroke="#FF6B4A" strokeWidth="2.4" />
      <circle cx="19.5" cy="16" r="5" fill="none" stroke="#FF6B4A" strokeOpacity=".55" strokeWidth="2.4" />
    </svg>
  )
}

export function Switch({ checked, onChange, disabled, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx('relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-50', checked ? 'bg-coral' : 'bg-line-strong')}
    >
      <span className={cx('absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-all', checked ? 'left-[18px]' : 'left-0.5')} />
    </button>
  )
}

/** Pill segmented control: options = [{ value, label, icon? }] */
export function Segmented({ value, options, onChange, disabled, size = 'md', className }) {
  return (
    <div className={cx('inline-flex rounded-full border border-line bg-subtle p-1', className)}>
      {options.map((o) => {
        const Icon = o.icon
        const active = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            disabled={disabled}
            title={o.title || o.label}
            aria-pressed={active}
            onClick={() => !active && onChange(o.value)}
            className={cx(
              'inline-flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-full font-bold transition',
              size === 'xs' ? 'px-2 py-1 text-[11px]' : size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3 py-1.5 text-xs',
              active ? 'bg-surface text-fg shadow-sm' : 'text-fg-2 hover:text-fg',
            )}
          >
            {Icon && <Icon {...ICON} size={14} />}
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-xs text-fg-3">{hint}</span>}
    </label>
  )
}

export function EmptyState({ icon: Icon, title, body, action }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-row border border-dashed border-line px-6 py-10 text-center">
      {Icon && <Icon {...ICON} size={20} className="text-fg-3" />}
      <p className="mt-3 text-sm font-bold text-fg">{title}</p>
      {body && <p className="mt-1 max-w-sm text-sm text-fg-2">{body}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
