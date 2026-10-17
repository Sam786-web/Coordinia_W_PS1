import { ExternalLink } from 'lucide-react'
import PageHeader from './PageHeader'
import MobileAppView from './MobileAppView'
import { AppQrCard, useNetworkInfo } from './PhoneConnect'
import { ICON } from '../lib/utils'

export default function MobileView({ state, ws, onOpenConnect }) {
  const net = useNetworkInfo(true)
  return (
    <div className="space-y-6">
      <PageHeader
        title="Mobile Companion Preview"
        description="The same app your teammates use on their Android phones, live and fully interactive."
        actions={
          <a className="btn" href="/?mode=mobile" target="_blank" rel="noreferrer">
            <ExternalLink {...ICON} /> Open full screen
          </a>
        }
      />

      <div className="flex flex-col items-center gap-8 2xl:flex-row 2xl:items-start">
        <div className="h-[780px] w-[390px] shrink-0 overflow-hidden rounded-[44px] border-[8px] border-zinc-900 bg-bg shadow-[0_24px_60px_rgba(15,23,42,0.18)] ring-4 ring-lime">
          <MobileAppView state={state} ws={ws} embedded />
        </div>

        <div className="w-full max-w-md space-y-4">
          <AppQrCard net={net} qrSize={152} />
          <section className="card p-5">
            <h3 className="text-sm font-semibold text-fg">Setting up our two Android phones</h3>
            <ul className="mt-2 space-y-2 text-sm text-fg-2">
              <li>Each person picks their own name in “Viewing as”. The phone remembers it.</li>
              <li>Changes sync instantly in both directions, so the dashboard updates as they tap.</li>
              <li>
                For Telegram messages, link each phone to the bot from{' '}
                <button className="text-indigo-600 hover:underline dark:text-indigo-400" onClick={onOpenConnect}>
                  Connect Phones
                </button>
                .
              </li>
            </ul>
          </section>
        </div>
      </div>
    </div>
  )
}
