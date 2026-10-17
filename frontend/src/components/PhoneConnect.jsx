import { useEffect, useMemo, useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { Check, Copy, ExternalLink, Send, Smartphone } from 'lucide-react'
import { Spinner } from './ui'
import { api } from '../lib/api'
import { cx } from '../lib/utils'

const I16 = { size: 16, strokeWidth: 1.75 }
const LOCAL = ['localhost', '127.0.0.1', '::1', '[::1]']

function frontendPort() {
  return window.location.port || (window.location.protocol === 'https:' ? '443' : '80')
}

/** LAN address + Telegram bot info from the backend; the IP stays editable for Wi-Fi/hotspot switches. */
export function useNetworkInfo(enabled = true) {
  const [info, setInfo] = useState(null)
  const [error, setError] = useState(null)
  const [ip, setIp] = useState('')

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    api
      .get(`/api/network-info?frontend_port=${frontendPort()}`)
      .then((d) => {
        if (cancelled) return
        setInfo(d)
        // If the dashboard itself was opened over the network, that address is known to work
        setIp((cur) => cur || (!LOCAL.includes(window.location.hostname) ? window.location.hostname : d.lan_ip || ''))
      })
      .catch((e) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [enabled])

  const port = frontendPort()
  const appUrl = ip.trim() ? `http://${ip.trim()}:${port}/?mode=mobile` : ''
  return { info, error, ip, setIp, appUrl, port }
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Clipboard API needs a secure context; fall back to a hidden textarea
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    return ok
  }
}

function Qr({ value, size }) {
  return (
    <div className="inline-flex rounded-2xl border border-line bg-white p-2.5">
      <QRCodeSVG value={value} size={size} bgColor="#ffffff" fgColor="#0F172A" level="M" />
    </div>
  )
}

function QrPlaceholder({ size, children }) {
  return (
    <div style={{ width: size + 22, height: size + 22 }} className="flex items-center justify-center rounded-2xl border border-dashed border-line px-4 text-center text-xs text-fg-3">
      {children}
    </div>
  )
}

export function AppQrCard({ net, qrSize = 168, className }) {
  const { info, error, ip, setIp, appUrl } = net
  const [copied, setCopied] = useState(false)
  const candidates = useMemo(() => (info?.candidates || []).filter((c) => c !== ip), [info, ip])

  const copy = async () => {
    if (await copyText(appUrl)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    }
  }

  return (
    <section className={cx('card p-5', className)}>
      <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
        <Smartphone {...I16} className="text-fg-3" /> Android Companion App
      </h3>
      <p className="mt-1 text-sm text-fg-2">Opens the mobile app on any phone on the same Wi-Fi or hotspot.</p>

      <div className="mt-4 flex justify-center">
        {appUrl ? <Qr value={appUrl} size={qrSize} /> : <QrPlaceholder size={qrSize}>{error || (info ? 'Enter the laptop IP below' : <Spinner />)}</QrPlaceholder>}
      </div>

      <label className="mt-4 block">
        <span className="label">Laptop IP address</span>
        <input className="input font-mono" value={ip} onChange={(e) => setIp(e.target.value)} placeholder="192.168.1.20" inputMode="decimal" spellCheck={false} />
      </label>
      {candidates.length > 0 && (
        <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-fg-3">
          Other networks:
          {candidates.map((c) => (
            <button key={c} type="button" onClick={() => setIp(c)} className="rounded border border-line px-1.5 py-0.5 font-mono text-fg-2 hover:bg-subtle">
              {c}
            </button>
          ))}
        </p>
      )}

      <div className="mt-3 flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-xl bg-subtle px-2.5 py-2 font-mono text-xs text-fg-2 ring-1 ring-line">{appUrl || '—'}</code>
        <button type="button" className="btn shrink-0" onClick={copy} disabled={!appUrl}>
          {copied ? <Check {...I16} className="text-emerald-600 dark:text-emerald-400" /> : <Copy {...I16} />}
          {copied ? 'Copied' : 'Copy Link'}
        </button>
      </div>

      <ol className="mt-4 space-y-1.5 text-sm text-fg-2">
        {['Scan the code with Android Chrome', 'Tap the menu (⋮) in the top-right corner', 'Tap “Add to Home screen”'].map((s, i) => (
          <li key={s} className="flex gap-2.5">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-subtle text-[11px] font-medium text-fg-2 ring-1 ring-line">{i + 1}</span>
            {s}
          </li>
        ))}
      </ol>
      <p className="mt-3 text-xs text-fg-3">Not loading on the phone? Make sure both devices are on the same network and that Windows Firewall allows port {net.port}.</p>
    </section>
  )
}

export function TelegramQrCard({ net, participants = [], qrSize = 168, className }) {
  const url = net.info?.telegram_bot_url
  const bot = net.info?.telegram_bot_username
  return (
    <section className={cx('card p-5', className)}>
      <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
        <Send {...I16} className="text-fg-3" /> Telegram Bot
      </h3>
      <p className="mt-1 text-sm text-fg-2">Lets the coordinator message each teammate and read their replies.</p>

      <div className="mt-4 flex justify-center">
        {url ? (
          <Qr value={url} size={qrSize} />
        ) : (
          <QrPlaceholder size={qrSize}>{net.info ? 'Bot not running. Add TELEGRAM_BOT_TOKEN to backend/.env and restart the backend.' : <Spinner />}</QrPlaceholder>
        )}
      </div>

      {url && (
        <a href={url} target="_blank" rel="noreferrer" className="mt-3 flex items-center justify-center gap-1 text-sm text-indigo-600 hover:underline dark:text-indigo-400">
          @{bot} <ExternalLink size={14} strokeWidth={1.75} />
        </a>
      )}

      <ol className="mt-4 space-y-1.5 text-sm text-fg-2">
        {['Scan the code to open the bot in Telegram', 'Tap Start', 'Send /join followed by your name'].map((s, i) => (
          <li key={s} className="flex gap-2.5">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-subtle text-[11px] font-medium text-fg-2 ring-1 ring-line">{i + 1}</span>
            {s}
          </li>
        ))}
      </ol>
      {participants.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {participants.map((p) => (
            <code key={p.id} className="rounded-xl bg-subtle px-2 py-1 font-mono text-xs text-fg-2 ring-1 ring-line">
              /join {p.name}
            </code>
          ))}
        </div>
      )}
    </section>
  )
}
