import type { Account } from '../types'
import {
  ACCOUNT_STATUS_LABELS,
  ACCOUNT_STATUS_TONES,
  ATTENTION_ACCOUNT_HINTS,
  ATTENTION_ACCOUNT_STATUSES,
} from '../lib/format'
import StatusBadge from './StatusBadge'

interface Props {
  accounts: Account[]
}

export default function AttentionPanel({ accounts }: Props) {
  const attention = accounts.filter((account) =>
    ATTENTION_ACCOUNT_STATUSES.includes(account.status),
  )

  if (attention.length === 0) {
    return <div className="muted" style={{ fontSize: 12 }}>无待处理事项，各炉心安好。</div>
  }

  return (
    <div className="row-list">
      {attention.map((account) => (
        <div className="row-item" key={account.id}>
          <div className="row-main">
            <div className="row-title">{account.displayName}</div>
            <div className="row-sub">{ATTENTION_ACCOUNT_HINTS[account.status]}</div>
          </div>
          <StatusBadge
            label={ACCOUNT_STATUS_LABELS[account.status]}
            tone={ACCOUNT_STATUS_TONES[account.status]}
          />
        </div>
      ))}
    </div>
  )
}
