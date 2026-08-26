import { useEffect, useState } from 'react'
import type { AccountCreateInput } from '../types'
import { ApiError } from '../api'

interface Props {
  onSubmit: (input: AccountCreateInput) => Promise<void>
  onClose: () => void
}

export default function CreateAccountModal({ onSubmit, onClose }: Props) {
  const [displayName, setDisplayName] = useState('')
  const [timezone, setTimezone] = useState('Asia/Shanghai')
  const [windowStart, setWindowStart] = useState('20:00')
  const [windowEnd, setWindowEnd] = useState('22:00')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await onSubmit({
        displayName,
        timezone,
        sendWindowStart: windowStart,
        sendWindowEnd: windowEnd,
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '保存失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal" role="dialog" aria-modal="true" aria-label="新建账号">
        <div className="modal-head">
          <h2 className="modal-title">新建账号</h2>
          <button className="btn ghost small" type="button" onClick={onClose}>
            关闭
          </button>
        </div>
        <p className="modal-sub">只登记本地信息。登录凭据不会写入表单或数据库。</p>
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
            <span className="field-hint">每日任务日期按该时区计算。</span>
          </div>
          <div className="field">
            <label>每日发送时段</label>
            <div className="field-row">
              <input
                type="time"
                value={windowStart}
                onChange={(event) => setWindowStart(event.target.value)}
                aria-label="发送时段起点"
                required
              />
              <input
                type="time"
                value={windowEnd}
                onChange={(event) => setWindowEnd(event.target.value)}
                aria-label="发送时段终点"
                required
              />
            </div>
            <span className="field-hint">调度器会在该时段内随机取时发送。</span>
          </div>
          {error && <div className="form-error">{error}</div>}
          <div className="form-actions">
            <button className="btn primary" type="submit" disabled={submitting}>
              {submitting ? '创建中…' : '创建账号'}
            </button>
            <button className="btn" type="button" onClick={onClose}>
              取消
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
