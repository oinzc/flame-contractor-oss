import type { Tone } from '../lib/format'

interface Props {
  label: string
  tone?: Tone
}

export default function StatusBadge({ label, tone = 'default' }: Props) {
  const className = tone === 'default' ? 'badge' : `badge ${tone}`
  return (
    <span className={className}>
      <i className="dot" />
      {label}
    </span>
  )
}
