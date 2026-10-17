/** One-tap demo replies per workspace (sent as that teammate through the coordinator). */
const SUGGESTIONS = {
  college: [
    { label: 'Kabir: Waiting on keys', sender: 'Kabir', message: 'The API is connected, but I am waiting for test credentials from Meera' },
    { label: 'Meera: Sent keys', sender: 'Meera', message: 'Just sent the test credentials to Kabir' },
    { label: 'Meera: Done (contradiction)', sender: 'Meera', message: 'Integration is 100% done! Everything works end to end.' },
    { label: 'Kabir: Testing complete', sender: 'Kabir', message: 'Got the keys, testing complete. Payment API is done!' },
  ],
  enterprise: [
    { label: 'Rohan: Waiting on KMS keys', sender: 'Rohan', message: 'Connected the payment webhook, but waiting for AWS KMS keys from Priya' },
    { label: 'Priya: Sent keys', sender: 'Priya', message: 'Shared the production KMS keys with Rohan' },
    { label: 'Vikram: Done (contradiction)', sender: 'Vikram', message: 'Load and pen tests all passed, done!' },
    { label: 'Rohan: Live in prod', sender: 'Rohan', message: 'Stripe webhook is live in production, complete!' },
  ],
}

export function suggestionsFor(mode, people) {
  if (SUGGESTIONS[mode]) return SUGGESTIONS[mode]
  if (people.length < 2) return []
  const [a, b] = people.map((p) => p.name)
  return [
    { label: `${a}: Waiting on ${b}`, sender: a, message: `Backend is connected, but I'm waiting for the design files from ${b}` },
    { label: `${b}: Sent files`, sender: b, message: `Shared the design files with ${a}` },
    { label: `${a}: All done`, sender: a, message: 'All done, shipped it!' },
  ]
}
