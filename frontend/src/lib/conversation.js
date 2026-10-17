import { parseDetails } from './utils'

const VIA = { telegram: 'Telegram', simulator: 'Tester', github: 'GitHub commit', android: 'Mobile app', voice: 'Voice note' }

/**
 * Turn raw agent logs (newest first) into a readable, chronological conversation.
 * Item kinds: 'in' (teammate message), 'out' (coordinator message), 'action', 'warn', 'note'.
 */
export function buildConversation(logs) {
  const items = []
  for (const log of [...logs].reverse()) {
    const d = parseDetails(log.details_json)
    const base = { key: log.id, ts: log.timestamp }
    if (log.log_type === 'REPLY') {
      const m = /^(.+?) via (\w+): ([\s\S]*)$/.exec(log.summary)
      if (m && !/^availability ->/.test(m[3])) items.push({ ...base, kind: 'in', who: m[1], via: VIA[m[2]] || m[2], text: m[3] })
      else items.push({ ...base, kind: 'note', who: m?.[1], text: m ? `${m[1]} updated availability: ${m[3].replace(/^availability -> /, '')}` : log.summary })
    } else if (d.notification?.text) {
      const n = d.notification
      items.push({
        ...base,
        kind: 'out',
        to: n.to,
        text: n.text,
        status: n.delivered ? 'Sent on Telegram' : n.channel === 'telegram' ? 'Telegram delivery failed' : 'Not sent · Telegram not connected',
        failed: !n.delivered,
      })
    } else if (log.log_type === 'AI_REASONING' && d.result) {
      const m = /\] (.+?)'s "(.+)" -> (.+?) \(confidence/.exec(log.summary)
      items.push({
        ...base,
        kind: 'action',
        who: m?.[1],
        text: m ? `Updated "${m[2]}" to ${m[3]}` : log.summary.replace(/^\[[^\]]+\]\s*/, ''),
        result: d.result,
        llm: d.llm,
      })
      if (d.result.reply_to_sender && m) items.push({ ...base, key: `${log.id}-reply`, kind: 'out', to: m[1], text: d.result.reply_to_sender, status: 'Reply' })
    } else if (log.log_type === 'BLOCKER_ALERT' && /^contradiction/i.test(log.summary)) {
      items.push({ ...base, kind: 'warn', text: log.summary.replace(/^Contradiction:\s*/i, '') })
    } else if (log.log_type === 'AI_REASONING') {
      items.push({ ...base, kind: 'note', text: log.summary.replace(/^\[[^\]]+\]\s*/, '') })
    }
    // Plain blocker alerts are already covered by the action line above them
  }
  return items
}

/** Only the messages exchanged between one teammate and the coordinator. */
export function conversationFor(logs, name) {
  const n = name?.toLowerCase()
  return buildConversation(logs).filter(
    (it) => (it.kind === 'in' && it.who?.toLowerCase() === n) || (it.kind === 'out' && it.to?.toLowerCase() === n),
  )
}
