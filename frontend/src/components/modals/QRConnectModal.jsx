import Modal from '../Modal'
import { AppQrCard, TelegramQrCard, useNetworkInfo } from '../PhoneConnect'

export default function QRConnectModal({ open, onClose, participants }) {
  const net = useNetworkInfo(open)
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Connect phones"
      description="Set up the mobile app and the Telegram bot on each teammate's Android phone."
      width="max-w-3xl"
    >
      <div className="grid gap-4 md:grid-cols-2">
        <AppQrCard net={net} />
        <TelegramQrCard net={net} participants={participants} />
      </div>
    </Modal>
  )
}
