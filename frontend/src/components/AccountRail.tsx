import { useState } from 'react'
import type { Account } from '../types'
import {
  ACCOUNT_STATUS_LABELS,
  ACCOUNT_STATUS_TONES,
} from '../lib/format'
import StatusBadge from './StatusBadge'

interface Props {
  accounts: Account[]
  onToggle: (account: Account) => void
  onCreateAccount: () => void
}

export default function AccountRail({ accounts, onToggle, onCreateAccount }: Props) {
  const [busyId, setBusyId] = useState<string | null>(null)

  if (accounts.length === 0) {
    return (
      <div className="empty">
        <span className="empty-glyph" aria-hidden="true">
          炉
        </span>
        <div className="empty-title">尚无契约者</div>
        <p className="empty-copy">
          账号是炉心，好友关系是契约。先立下第一座炉心，再为它绑定契约对象。
        </p>
        <button className="btn primary small" type="button" onClick={onCreateAccount}>
          新建账号
        </button>
      </div>
    )
  }

  async function handleToggle(account: Account) {
    setBusyId(account.id)
    try {
      await onToggle(account)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="account-grid">
      {accounts.map((account) => (
        <article className="account-card" key={account.id}>
          <div className="account-head">
            <span className="account-glyph" aria-hidden="true">
              {account.displayName.slice(0, 1)}
            </span>
            <div style={{ minWidth: 0 }}>
              <div className="account-name" title={account.displayName}>
                {account.displayName}
              </div>
              <div className="account-meta">{account.id.slice(0, 8)}</div>
            </div>
            <StatusBadge
              label={ACCOUNT_STATUS_LABELS[account.status]}
              tone={ACCOUNT_STATUS_TONES[account.status]}
            />
          </div>
          <div className="account-window">
            <span>
              焰窗 {account.sendWindowStart}–{account.sendWindowEnd}
            </span>
            <span>{account.timezone}</span>
          </div>
          <div className="account-actions">
            <button
              className="btn ghost small"
              type="button"
              disabled={busyId === account.id}
              onClick={() => handleToggle(account)}
            >
              {account.enabled ? '暂停' : '恢复'}
            </button>
            {account.status === 'pending_login' && (
              <span className="muted" style={{ fontSize: 12 }}>
                扫码登录 · 下一阶段
              </span>
            )}
          </div>
        </article>
      ))}
    </div>
  )
}
