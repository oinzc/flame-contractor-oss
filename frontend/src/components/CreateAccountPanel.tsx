import { useState } from 'react'
import type { AccountCreateInput } from '../types'
import { ApiError } from '../api'

interface Props {
  onSubmit: (input: AccountCreateInput) => Promise<void>
  onCancel: () => void
}

export default function CreateAccountPanel({ onSubmit, onCancel }: Props) {
  const [displayName, setDisplayName] = useState('')
  const [timezone, setTimezone] = useState('Asia/Shanghai')
  const [windowStart, setWindowStart] = useState('20:00')
  const [windowEnd, setWindowEnd] = useState('22:00')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await onSubmit({ displayName, timezone, sendWindowStart: windowStart, sendWindowEnd: windowEnd })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '保存失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="panel">
      <h2 className="panel-title">立下契约 · 新建账号</h2>
      <p className="panel-sub">
        只登记本地元数据。登录凭据绝不会写入表单或数据库。
      </p>
      <form className="form-grid" onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="account-name">显示名称</label>
          <input
            id="account-name"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="例如：主账号"
            maxLength={60}
            required
            autoFocus
          />
        </div>
        <div className="field">
          <label htmlFor="account-timezone">时区</label>
          <input
            id="account-timezone"
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
            placeholder="Asia/Shanghai"
            maxLength={64}
            required
          />
          <span className="field-hint">每日契约日期将按该时区计算。</span>
        </div>
        <div className="field">
          <label htmlFor="account-window">每日焰窗</label>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <input
              id="account-window-start"
              type="time"
              value={windowStart}
              onChange={(event) => setWindowStart(event.target.value)}
              required
            />
            <span className="muted">至</span>
            <input
              id="account-window-end"
              type="time"
              value={windowEnd}
              onChange={(event) => setWindowEnd(event.target.value)}
              required
            />
          </div>
          <span className="field-hint">调度器将在该窗口内随机取时，降低规律性。</span>
        </div>
        {error && <div className="form-error">{error}</div>}
        <div className="form-actions">
          <button className="btn primary" type="submit" disabled={submitting}>
            {submitting ? '立约中…' : '立下契约'}
          </button>
          <button className="btn ghost" type="button" onClick={onCancel}>
            取消
          </button>
        </div>
      </form>
    </section>
  )
}
