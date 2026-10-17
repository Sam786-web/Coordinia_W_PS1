import { useState } from 'react'
import Modal from '../Modal'
import { Field, Spinner } from '../ui'
import { api } from '../../lib/api'
import { useToast } from '../Toast'

export default function SlackModal({ open, onClose, onSaved }) {
  const toast = useToast()
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)

  const save = async () => {
    setBusy(true)
    try {
      const out = await api.post('/api/integrations', { slack_enabled: true, slack_webhook_url: url.trim() })
      toast.success('Slack connected', 'Reminders, relays and escalations are mirrored to your channel.')
      onSaved?.(out)
      setUrl('')
      onClose()
    } catch (e) {
      toast.error('Could not connect Slack', e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Connect Slack"
      description="Company add-on: mirror the coordinator's messages into a Slack channel."
      width="max-w-md"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" onClick={save} disabled={busy || !url.trim()}>
            {busy && <Spinner />} Connect
          </button>
        </>
      }
    >
      <Field label="Incoming webhook URL" hint="In Slack: Apps → Incoming Webhooks → Add to a channel, then copy the URL.">
        <input className="input font-mono text-xs" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.slack.com/services/…" autoFocus spellCheck={false} />
      </Field>
    </Modal>
  )
}
