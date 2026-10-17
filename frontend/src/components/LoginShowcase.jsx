import { Bot, CalendarClock, Check, GitCommitHorizontal, HeartPulse, ShieldCheck, Sparkles } from 'lucide-react'
import { Logo } from './ui'
import { ICON, cx } from '../lib/utils'

// Illustrated product preview for the sign-in page: the real loop in one picture
// (pulse -> Telegram blocker relay -> auto-unblock), drawn in the app's own pastel + lime style.

const BARS = [
  { name: 'Aanya', pct: 100, tint: 'bg-lavender' },
  { name: 'Kabir', pct: 55, tint: 'bg-butter' },
  { name: 'Meera', pct: 80, tint: 'bg-flamingo' },
  { name: 'Dev', pct: 35, tint: 'bg-mint' },
]

function PulseCard() {
  return (
    <div className="w-[270px] rounded-[26px] bg-white p-5 shadow-[0_24px_50px_-12px_rgba(17,24,39,0.25)]">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[11px] font-bold text-zinc-500">Sync Pulse</p>
          <p className="mt-0.5 flex items-center gap-1.5 text-2xl font-extrabold tracking-tight text-zinc-900">
            <HeartPulse {...ICON} size={18} className="text-capsule" /> 68% Sync
          </p>
        </div>
        <span className="rounded-full bg-mint px-2.5 py-1 text-[10px] font-extrabold text-emerald-950">On track</span>
      </div>
      <div className="mt-3 flex h-24 items-end justify-between px-1">
        {BARS.map((b, i) => (
          <div key={b.name} className="flex flex-col items-center gap-1.5">
            <div className="relative flex h-20 w-7 items-end overflow-hidden rounded-full bg-[#FFE4E6]">
              <div
                className="w-full origin-bottom animate-grow rounded-full bg-capsule"
                style={{ height: `${b.pct}%`, animationDelay: `${0.4 + i * 0.12}s` }}
              />
            </div>
            <span className={cx('grid h-6 w-6 place-items-center rounded-full text-[10px] font-extrabold text-zinc-800', b.tint)}>{b.name[0]}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function ChatCard() {
  return (
    <div className="w-[250px] rounded-[22px] bg-zinc-900 p-3.5 text-white shadow-[0_24px_50px_-12px_rgba(17,24,39,0.45)]">
      <div className="flex items-center gap-2 border-b border-white/10 pb-2.5">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-lavender text-[#1E1B4B]">
          <Bot {...ICON} size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-extrabold">Coordina bot</p>
          <p className="flex items-center gap-1 text-[10px] text-white/60">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> Telegram
          </p>
        </div>
      </div>
      <div className="mt-2.5 space-y-2 text-[11px] leading-snug">
        <p className="ml-auto w-fit max-w-[88%] rounded-2xl rounded-br-md bg-[#2B5278] px-3 py-1.5">
          API's connected, but I'm waiting on the test keys from Meera
          <span className="mt-0.5 block text-right text-[9px] text-white/50">Kabir · 10:42</span>
        </p>
        <p className="w-fit max-w-[92%] rounded-2xl rounded-bl-md bg-white/10 px-3 py-1.5">
          Got it: marked <b className="text-flamingo">Blocked</b>. I've asked Meera to send the keys.
          <span className="mt-0.5 block text-[9px] text-white/50">Coordinator · 10:42</span>
        </p>
      </div>
    </div>
  )
}

function Toast({ icon: Icon, tint, title, sub, className }) {
  return (
    <div className={cx('flex items-center gap-2.5 rounded-2xl bg-white py-2 pl-2 pr-3.5 shadow-[0_16px_34px_-10px_rgba(17,24,39,0.3)]', className)}>
      <span className={cx('grid h-8 w-8 shrink-0 place-items-center rounded-xl text-zinc-900', tint)}>
        <Icon {...ICON} size={15} />
      </span>
      <span>
        <span className="block text-xs font-extrabold text-zinc-900">{title}</span>
        <span className="block text-[10px] font-semibold text-zinc-500">{sub}</span>
      </span>
    </div>
  )
}

const POINTS = [
  [GitCommitHorizontal, 'GitHub repo → tasks'],
  [Bot, 'Telegram check-ins'],
  [CalendarClock, 'Deadlines that follow up'],
]

export default function LoginShowcase() {
  return (
    <aside className="relative m-3 flex flex-col overflow-hidden rounded-[32px] bg-lime p-7 text-zinc-900 lg:p-9">
      {/* soft decorative shapes */}
      <div className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-white/30 blur-2xl" />
      <div className="pointer-events-none absolute -bottom-20 -left-10 h-64 w-64 rounded-full bg-[#C5E84A]/60 blur-3xl" />
      <svg className="pointer-events-none absolute inset-0 h-full w-full opacity-[0.07]" aria-hidden="true">
        <defs>
          <pattern id="coordina-dots" width="22" height="22" patternUnits="userSpaceOnUse">
            <circle cx="2" cy="2" r="1.6" fill="#111827" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#coordina-dots)" />
      </svg>

      <div className="relative flex items-center gap-3">
        <Logo className="h-11 w-11" />
        <span className="text-lg font-extrabold tracking-tight">Coordina</span>
        <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-zinc-900 px-2.5 py-1 text-[10px] font-bold text-lime">
          <Sparkles {...ICON} size={11} /> AI coordinator
        </span>
      </div>

      <h1 className="relative mt-6 text-3xl font-extrabold leading-[1.1] tracking-tight lg:text-[2.35rem]">
        The AI that{' '}
        <span className="relative whitespace-nowrap">
          <span className="relative z-10">chases updates</span>
          <span className="absolute inset-x-0 bottom-1 z-0 h-3 rounded-full bg-white/70" />
        </span>{' '}
        so you don't have to.
      </h1>
      <p className="relative mt-3 max-w-md text-sm font-medium text-zinc-700">
        Paste a GitHub repo and hand out personal PINs. Coordina checks in with every teammate on Telegram, spots who's stuck, and asks the right person for help.
      </p>

      {/* product collage */}
      <div className="relative mx-auto mt-6 h-[330px] w-full max-w-[460px]" aria-hidden="true">
        <div className="absolute left-0 top-4 animate-rise" style={{ animationDelay: '0.1s' }}>
          <div className="animate-float-slow">
            <PulseCard />
          </div>
        </div>
        <div className="absolute bottom-0 right-0 animate-rise" style={{ animationDelay: '0.35s' }}>
          <div className="animate-float" style={{ animationDelay: '1.5s' }}>
            <ChatCard />
          </div>
        </div>
        <div className="absolute right-0 top-0 animate-rise" style={{ animationDelay: '0.6s' }}>
          <Toast icon={Check} tint="bg-mint" title="Auto-unblocked" sub="Kabir → In Progress" className="animate-float" />
        </div>
        <div className="absolute bottom-2 left-6 animate-rise" style={{ animationDelay: '0.8s' }}>
          <Toast icon={ShieldCheck} tint="bg-butter" title="PIN 482 913" sub="Verified on Telegram" className="animate-float-slow" />
        </div>
      </div>

      <div className="relative mt-auto flex flex-wrap gap-2 pt-6">
        {POINTS.map(([Icon, label]) => (
          <span key={label} className="inline-flex items-center gap-1.5 rounded-full bg-white/60 px-3 py-1.5 text-xs font-bold text-zinc-800 backdrop-blur">
            <Icon {...ICON} size={13} /> {label}
          </span>
        ))}
      </div>
    </aside>
  )
}
