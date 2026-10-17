import { useEffect, useState } from 'react'
import { ArrowRight, Building2, Crown, GraduationCap, KeyRound, Users, Zap } from 'lucide-react'
import { Field, Logo, Spinner } from './ui'
import LoginShowcase from './LoginShowcase'
import { useToast } from './Toast'
import { api } from '../lib/api'
import { clearInviteFromUrl, useAuth } from '../lib/auth'
import { SkinProvider } from '../lib/skin'
import { ICON, cx } from '../lib/utils'

function cleanCode(value) {
  const m = /[?&]invite=([A-Za-z0-9-]+)/.exec(value || '')
  return (m ? m[1] : value || '').trim().toUpperCase()
}

const ACCOUNT_TYPES = [
  {
    org: 'college',
    icon: GraduationCap,
    bg: 'bg-lavender',
    title: 'Register as College Team Lead',
    unlocks: 'Unlocks College Workspace & GitHub Repo Splitter',
  },
  {
    org: 'corporate',
    icon: Building2,
    bg: 'bg-butter',
    title: 'Register as Company Team Lead / Enterprise Admin',
    unlocks: 'Unlocks Corporate Workspace, Slack integrations & Enterprise SLA views',
  },
]

export default function LoginScreen({ invite, mobile = false }) {
  const toast = useToast()
  const auth = useAuth()
  const [tab, setTab] = useState(invite ? 'member' : 'lead')
  const [leadMode, setLeadMode] = useState('signin')
  const [busy, setBusy] = useState(null)
  const [f, setF] = useState({ name: '', email: '', password: '', org: 'college', code: invite || '', pin: '', newName: '', newRole: '' })
  const [joinNew, setJoinNew] = useState(false)
  const [inviteInfo, setInviteInfo] = useState(null)
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }))

  useEffect(() => {
    const code = cleanCode(f.code)
    if (!/^[A-Z]+-[A-Z0-9]+$/.test(code)) return setInviteInfo(null)
    const t = setTimeout(() => api.get(`/api/invites/${code}`).then(setInviteInfo, () => setInviteInfo({ error: true })), 250)
    return () => clearTimeout(t)
  }, [f.code])

  const run = async (key, fn, success) => {
    setBusy(key)
    try {
      const s = await fn()
      clearInviteFromUrl()
      toast.success(success(s))
    } catch (e) {
      toast.error('Could not sign in', e.message)
    } finally {
      setBusy(null)
    }
  }

  const submitLead = (e) => {
    e.preventDefault()
    if (leadMode === 'register') {
      run(
        'lead',
        () => auth.registerLead({ name: f.name, email: f.email, password: f.password, account_type: f.org === 'corporate' ? 'company_lead' : 'college_lead' }),
        (s) => `Welcome, ${s.user.name}`,
      )
    } else {
      run('lead', () => auth.loginLead({ email: f.email, password: f.password }), (s) => `Welcome back, ${s.user.name}`)
    }
  }
  const submitMember = (e) => {
    e.preventDefault()
    if (joinNew) {
      run('member', () => auth.login({ name: f.newName, role: 'member', invite_code: f.code, member_role: f.newRole }), (s) => `Joined ${s.project.name}`)
    } else {
      run('member', () => auth.joinMember({ invite_code: f.code, auth_pin: f.pin }), (s) => `Signed in as ${s.user.name}`)
    }
  }

  // Preview the Corporate look while registering a corporate account
  const previewWorkspace =
    tab === 'lead' && leadMode === 'register' ? (f.org === 'corporate' ? 'company' : 'college') : inviteInfo?.project?.workspace_type || 'college'
  const leadValid = leadMode === 'register' ? f.name.trim() && f.email.trim() && f.password.length >= 6 : f.email.trim() && f.password
  const memberValid = cleanCode(f.code) && (joinNew ? f.newName.trim() : f.pin.trim())

  const tabBtn = (active) =>
    cx('inline-flex flex-1 items-center justify-center gap-1.5 rounded-full px-4 py-2 text-sm font-bold transition-colors', active ? 'bg-fg text-bg shadow-sm' : 'text-fg-2 hover:text-fg')

  return (
    <SkinProvider workspace={previewWorkspace}>
      <div className={cx('flex min-h-[100dvh] items-center justify-center bg-bg p-3 lg:p-6', mobile && 'items-start pt-[max(12px,env(safe-area-inset-top))]')}>
        <div className={cx('app-shell grid w-full overflow-hidden', mobile ? 'max-w-md' : 'max-w-6xl lg:grid-cols-[1.1fr_1fr]')}>
          {!mobile && <LoginShowcase />}

          <section className="flex flex-col justify-center p-6 lg:p-10">
            {mobile && (
              <div className="mb-6 flex items-center gap-3">
                <Logo className="h-10 w-10" />
                <span className="text-lg font-extrabold tracking-tight text-fg">Coordina</span>
              </div>
            )}
            <h2 className="text-2xl font-extrabold tracking-tight text-fg">Welcome back</h2>
            <p className="mt-1 text-sm text-fg-2">Sign in as a Team Lead, or join your team with your personal PIN.</p>

            <div className="mt-6 flex gap-1 rounded-full bg-panel p-1 ring-1 ring-line">
              <button type="button" className={tabBtn(tab === 'lead')} onClick={() => setTab('lead')} aria-pressed={tab === 'lead'}>
                <Crown {...ICON} size={14} /> Team Lead
              </button>
              <button type="button" className={tabBtn(tab === 'member')} onClick={() => setTab('member')} aria-pressed={tab === 'member'}>
                <Users {...ICON} size={14} /> Team Member
              </button>
            </div>

            {inviteInfo?.project && tab === 'member' && (
              <div className="mt-4 rounded-row bg-lime p-4 text-sm text-zinc-900">
                <p className="font-extrabold">You're invited to join {inviteInfo.project.name}</p>
                <p className="mt-0.5 font-medium text-zinc-700">Enter the personal PIN your Team Lead gave you.</p>
              </div>
            )}

            <div className="mt-5">
              {tab === 'lead' ? (
                <form className="space-y-4" onSubmit={submitLead}>
                  <div className="flex gap-2">
                    {[
                      ['signin', 'Sign in'],
                      ['register', 'Create account'],
                    ].map(([k, label]) => (
                      <button
                        key={k}
                        type="button"
                        onClick={() => setLeadMode(k)}
                        className={cx('rounded-full px-3.5 py-1.5 text-xs font-bold transition-colors', leadMode === k ? 'bg-coral/15 text-coral' : 'text-fg-3 hover:text-fg-2')}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  {leadMode === 'register' && (
                    <Field label="Name">
                      <input className="input min-h-[46px]" value={f.name} onChange={set('name')} placeholder="e.g. Mital Shah" autoComplete="name" />
                    </Field>
                  )}
                  <Field label="Email">
                    <input className="input min-h-[46px]" type="email" value={f.email} onChange={set('email')} placeholder="you@college.edu" autoComplete="email" />
                  </Field>
                  <Field label="Password" hint={leadMode === 'register' ? 'At least 6 characters.' : undefined}>
                    <input className="input min-h-[46px]" type="password" value={f.password} onChange={set('password')} autoComplete={leadMode === 'register' ? 'new-password' : 'current-password'} />
                  </Field>
                  {leadMode === 'register' && (
                    <div role="radiogroup" aria-label="Account type" className="grid gap-2 sm:grid-cols-2">
                      {ACCOUNT_TYPES.map((a) => {
                        const on = f.org === a.org
                        return (
                          <button
                            key={a.org}
                            type="button"
                            role="radio"
                            aria-checked={on}
                            onClick={() => setF((s) => ({ ...s, org: a.org }))}
                            className={cx(
                              'flex flex-col items-start gap-1.5 rounded-row p-3.5 text-left text-zinc-900 ring-2 transition',
                              a.bg,
                              on ? 'ring-zinc-900' : 'ring-transparent opacity-75 hover:opacity-100',
                            )}
                          >
                            <span className="flex w-full items-center gap-2">
                              <span className="grid h-8 w-8 place-items-center rounded-xl bg-white/70">
                                <a.icon {...ICON} size={16} />
                              </span>
                              <span className={cx('ml-auto h-4 w-4 rounded-full border-2 border-zinc-900', on && 'bg-zinc-900 ring-2 ring-inset ring-white')} />
                            </span>
                            <span className="text-[13px] font-extrabold leading-tight">{a.title}</span>
                            <span className="text-[11px] font-semibold leading-snug text-zinc-700">{a.unlocks}</span>
                          </button>
                        )
                      })}
                    </div>
                  )}
                  <button className="btn-primary min-h-[48px] w-full" disabled={!!busy || !leadValid}>
                    {busy === 'lead' ? <Spinner /> : <ArrowRight {...ICON} />}{' '}
                    {leadMode === 'register' ? (f.org === 'corporate' ? 'Register as Company Team Lead' : 'Register as College Team Lead') : 'Sign in'}
                  </button>
                </form>
              ) : (
                <form className="space-y-4" onSubmit={submitMember}>
                  <Field label="Project invite code">
                    <input className="input min-h-[46px] font-mono uppercase" value={f.code} onChange={set('code')} placeholder="SMART-2026" autoCapitalize="characters" spellCheck={false} />
                  </Field>
                  {inviteInfo?.error && <p className="-mt-2 text-xs font-semibold text-rose-600 dark:text-rose-400">That invite code wasn't found.</p>}
                  {!joinNew ? (
                    <Field label="Your personal PIN" hint="The 6-digit number your Team Lead sees next to your name.">
                      <input
                        className="input min-h-[46px] font-mono tracking-[0.3em]"
                        value={f.pin}
                        onChange={(e) => setF((s) => ({ ...s, pin: e.target.value.replace(/\D/g, '').slice(0, 6) }))}
                        placeholder="482913"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        maxLength={6}
                        aria-label="Your personal 6-digit PIN"
                      />
                    </Field>
                  ) : (
                    <>
                      <Field label="Your name">
                        <input className="input min-h-[46px]" value={f.newName} onChange={set('newName')} placeholder="e.g. Riya" />
                      </Field>
                      <Field label="Your role">
                        <input className="input min-h-[46px]" value={f.newRole} onChange={set('newRole')} placeholder="e.g. UI Designer" />
                      </Field>
                    </>
                  )}
                  <button className="btn-primary min-h-[48px] w-full" disabled={!!busy || !memberValid}>
                    {busy === 'member' ? <Spinner /> : <KeyRound {...ICON} />} {joinNew ? 'Join the team' : 'Sign in'}
                  </button>
                  <button type="button" className="w-full text-center text-xs font-semibold text-fg-3 hover:text-fg-2" onClick={() => setJoinNew((v) => !v)}>
                    {joinNew ? 'I already have a PIN' : "New to the team and don't have a PIN? Join with your name"}
                  </button>
                </form>
              )}
            </div>

            <div className="mt-6 flex flex-wrap items-center justify-center gap-2 rounded-row bg-panel p-3 ring-1 ring-line">
              <span className="inline-flex items-center gap-1.5 text-xs font-bold text-fg-2">
                <Zap {...ICON} size={13} className="text-coral" /> Instant judge demo
              </span>
              <button className="pill-btn border-transparent bg-lavender text-[#1E1B4B] hover:bg-lavender/80" disabled={!!busy} onClick={() => run('demo-lead', () => auth.demo('lead', 'college_lead'), () => 'Signed in as a College Team Lead')}>
                {busy === 'demo-lead' ? <Spinner /> : <GraduationCap {...ICON} size={13} />} College Lead
              </button>
              <button className="pill-btn border-transparent bg-flamingo text-rose-950 hover:bg-flamingo/80" disabled={!!busy} onClick={() => run('demo-company', () => auth.demo('lead', 'company_lead'), () => 'Signed in as a Company Team Lead')}>
                {busy === 'demo-company' ? <Spinner /> : <Building2 {...ICON} size={13} />} Company Lead
              </button>
              <button className="pill-btn border-transparent bg-butter text-amber-950 hover:bg-butter/80" disabled={!!busy} onClick={() => run('demo-member', () => auth.demo('member'), (s) => `Signed in as ${s.user.name}`)}>
                {busy === 'demo-member' ? <Spinner /> : <Users {...ICON} size={13} />} Member
              </button>
            </div>
            <p className="mt-4 text-center text-[11px] font-semibold text-fg-3">
              Works with Telegram · GitHub · Groq cloud AI · Ollama on your laptop
            </p>
          </section>
        </div>
      </div>
    </SkinProvider>
  )
}
