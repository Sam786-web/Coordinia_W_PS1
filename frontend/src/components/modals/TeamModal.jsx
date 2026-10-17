import { useEffect, useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { Check, Copy, Send, ShieldCheck, UserPlus } from 'lucide-react'
import Modal from '../Modal'
import { Avatar, Spinner } from '../ui'
import { useNetworkInfo } from '../PhoneConnect'
import { api } from '../../lib/api'
import { useAuth } from '../../lib/auth'
import { useTerms } from '../../lib/skin'
import { ICON } from '../../lib/utils'

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const ta = Object.assign(document.createElement('textarea'), { value: text })
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    ta.remove()
  }
}

function CopyButton({ value, label = 'Copy' }) {
  const [done, setDone] = useState(false)
  return (
    <button
      className="pill-btn shrink-0"
      disabled={!value}
      onClick={async () => {
        await copyText(value)
        setDone(true)
        setTimeout(() => setDone(false), 1400)
      }}
    >
      {done ? <Check {...ICON} size={13} className="text-emerald-600" /> : <Copy {...ICON} size={13} />} {done ? 'Copied' : label}
    </button>
  )
}

export default function TeamModal({ open, onClose, project, participants, bot, onAddMember }) {
  const auth = useAuth()
  const terms = useTerms()
  const net = useNetworkInfo(open)
  const [invite, setInvite] = useState(null)

  useEffect(() => {
    if (!open || !project) return
    setInvite(null)
    api.get(`/api/projects/${project.id}/invite`).then(setInvite, () => setInvite({ invite_code: project.invite_code, invite_path: `/?invite=${project.invite_code}` }))
  }, [open, project])

  const base = net.ip ? `http://${net.ip}:${net.port}` : window.location.origin
  const link = invite ? `${base}${invite.invite_path}` : ''

  return (
    <Modal open={open} onClose={onClose} title={`${terms.members} & Auth PINs`} description="Every teammate has their own unique 6-digit PIN. They sign in with the invite code plus their PIN, and verify on Telegram by sending the PIN to the bot." accent="bg-lavender" width="max-w-2xl">
      <section className="flex flex-wrap items-center gap-5 rounded-bento bg-lime p-5 text-zinc-900">
        <div className="rounded-2xl bg-white p-2.5 shadow-sm">{link ? <QRCodeSVG value={link} size={112} level="M" /> : <Spinner />}</div>
        <div className="min-w-[200px] flex-1">
          <p className="text-xs font-bold opacity-70">Project invite code</p>
          <p className="font-mono text-2xl font-bold tracking-wide">{invite?.invite_code || project?.invite_code}</p>
          <p className="mt-1 truncate text-xs font-semibold opacity-70" title={link}>
            {link}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button className="inline-flex items-center gap-1.5 rounded-full bg-zinc-900 px-3.5 py-1.5 text-xs font-bold text-white" onClick={() => copyText(link)}>
              <Copy {...ICON} size={13} /> Copy invite link
            </button>
            {bot && (
              <button className="inline-flex items-center gap-1.5 rounded-full bg-white px-3.5 py-1.5 text-xs font-bold text-zinc-900" onClick={() => copyText(`https://t.me/${bot}`)}>
                <Send {...ICON} size={13} /> Copy Telegram bot link
              </button>
            )}
          </div>
        </div>
      </section>

      <div className="mt-5 flex items-center justify-between">
        <h3 className="text-sm font-extrabold text-fg">
          {terms.members} · {participants.length}
        </h3>
        {auth.isLead && (
          <button className="pill-btn" onClick={onAddMember}>
            <UserPlus {...ICON} size={13} /> Add member
          </button>
        )}
      </div>
      <ul className="mt-3 space-y-2">
        {participants.map((p) => {
          const verified = p.is_verified && p.telegram_chat_id
          const authLink = bot && p.auth_pin ? `https://t.me/${bot}?start=auth_${p.auth_pin}` : null
          return (
            <li key={p.id} className="flex flex-wrap items-center gap-3 rounded-row border border-line bg-surface px-4 py-3">
              <Avatar name={p.name} size="md" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-extrabold text-fg">{p.name}</p>
                <p className="truncate text-xs font-semibold text-fg-2">{p.role || 'Team member'}</p>
              </div>
              <span className={verified ? 'inline-flex items-center gap-1 rounded-full bg-mint px-2.5 py-1 text-[11px] font-bold text-emerald-950' : 'rounded-full bg-subtle px-2.5 py-1 text-[11px] font-bold text-fg-3'}>
                {verified ? <ShieldCheck {...ICON} size={12} /> : null}
                {verified ? 'Telegram verified' : 'Not verified'}
              </span>
              {p.auth_pin ? (
                <>
                  <code className="rounded-full bg-lavender px-3 py-1 font-mono text-xs font-medium text-[#1E1B4B]">{p.auth_pin}</code>
                  <CopyButton value={authLink ? `${p.auth_pin} (or tap ${authLink})` : p.auth_pin} label="PIN" />
                  {authLink && (
                    <a className="pill-btn" href={authLink} target="_blank" rel="noreferrer" title={`Telegram link that verifies ${p.name}`}>
                      <Send {...ICON} size={13} /> Link
                    </a>
                  )}
                </>
              ) : (
                <span className="text-xs font-semibold text-fg-3">PIN visible to the Team Lead</span>
              )}
            </li>
          )
        })}
      </ul>
    </Modal>
  )
}
