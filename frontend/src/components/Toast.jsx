import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react'
import { ICON, cx } from '../lib/utils'

const ToastContext = createContext(null)

const TONES = {
  success: { icon: CheckCircle2, cls: 'text-emerald-600 dark:text-emerald-400' },
  error: { icon: AlertCircle, cls: 'text-rose-600 dark:text-rose-400' },
  info: { icon: Info, cls: 'text-fg-2' },
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const seq = useRef(0)

  const dismiss = useCallback((id) => setToasts((t) => t.filter((x) => x.id !== id)), [])
  const push = useCallback(
    (type, title, body) => {
      const id = ++seq.current
      setToasts((t) => [...t.slice(-3), { id, type, title, body }])
      setTimeout(() => dismiss(id), type === 'error' ? 6000 : 3800)
    },
    [dismiss],
  )
  const toast = useMemo(
    () => ({
      success: (title, body) => push('success', title, body),
      error: (title, body) => push('error', title, body),
      info: (title, body) => push('info', title, body),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="pointer-events-none fixed left-1/2 top-[calc(12px+env(safe-area-inset-top))] z-[100] md:bottom-4 md:top-auto flex w-[380px] max-w-[calc(100vw-2rem)] -translate-x-1/2 flex-col gap-2" aria-live="polite">
        {toasts.map((t) => {
          const tone = TONES[t.type] || TONES.info
          const Icon = tone.icon
          return (
            <div key={t.id} role="status" className="pointer-events-auto flex animate-fade-in gap-3 rounded-lg border border-line bg-surface p-3 pr-2 shadow-lg shadow-black/10 dark:shadow-black/40">
              <Icon {...ICON} className={cx('mt-0.5 shrink-0', tone.cls)} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-fg">{t.title}</p>
                {t.body && <p className="mt-0.5 text-sm text-fg-2">{t.body}</p>}
              </div>
              <button onClick={() => dismiss(t.id)} className="icon-btn h-6 w-6" aria-label="Dismiss">
                <X {...ICON} size={14} />
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  return useContext(ToastContext)
}
