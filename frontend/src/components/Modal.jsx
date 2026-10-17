import { useEffect } from 'react'
import { X } from 'lucide-react'
import { ICON, cx } from '../lib/utils'

export default function Modal({ open, onClose, title, description, width = 'max-w-lg', children, footer, accent }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 pt-[8vh]" role="dialog" aria-modal="true" aria-label={title}>
      <div className="fixed inset-0 bg-zinc-900/30 backdrop-blur-[2px] dark:bg-black/60" onClick={onClose} />
      <div className={cx('relative w-full animate-fade-in overflow-hidden rounded-bento border border-line bg-shell shadow-2xl shadow-black/10 dark:shadow-black/60', width)}>
        {accent && <div className={cx('h-1.5 w-full', accent)} />}
        <div className="flex items-start gap-3 px-6 pb-1 pt-6">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-extrabold tracking-tight text-fg">{title}</h2>
            {description && <p className="mt-1 text-sm text-fg-2">{description}</p>}
          </div>
          <button onClick={onClose} className="icon-btn -mr-1 -mt-1" aria-label="Close">
            <X {...ICON} />
          </button>
        </div>
        <div className="max-h-[68vh] overflow-y-auto px-6 py-4">{children}</div>
        {footer && <div className="flex items-center justify-end gap-2 border-t border-line px-6 py-4">{footer}</div>}
      </div>
    </div>
  )
}
