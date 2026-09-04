import { useEffect, useState } from 'react'
import type { Account } from '../types'
import { ApiError } from '../api'

interface Props {
  account: Account
  onSubmit: (patch: {
    sendWindowStart: string
    sendWindowEnd: string
    sendTimeHhmm: string | null
  }) => Promise<void>
  onClose: () => void
}

export default function EditAccountModal({ account, onSubmit, onClose }: Props) {
  const [windowStart, setWindowStart] = useState(account.sendWindowStart)
  const [windowEnd, setWindowEnd] = useState(account.sendWindowEnd)
  const [sendTime, setSendTime] = useState(account.sendTimeHhmm ?? '')
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
    if (windowStart === windowEnd) {
      setError('发送时段的起点与终点不能相同')
      return
    }
    setSubmitting(true)
    try {
      await onSubmit({
        sendWindowStart: windowStart,
        sendWindowEnd: windowEnd,
        sendTimeHhmm: sendTime.trim() === '' ? null : sendTime,
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '保存失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal" role="dialog" aria-modal="true" aria-label="编辑账号发送时间">
        <div className="modal-head">
          <h2 className="modal-title">编辑账号 · {account.displayName}</h2>
          <button className="btn ghost small" type="button" onClick={onClose}>
            关闭
          </button>
        </div>
        <p className="modal-sub">
          未设置固定时刻时，调度器会在发送时段内随机取时。设置固定时刻后按该时刻发送。
        </p>
        <form className="form-grid" onSubmit={handleSubmit}>
          <div className="field">
            <label>发送时段</label>
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
            <span className="field-hint">调度器会在该时段内随机取时发送（未设置固定时刻时）。</span>
          </div>
          <div className="field">
            <label htmlFor="account-send-time">固定发送时刻（可选）</label>
            <input
              id="account-send-time"
              type="time"
              value={sendTime}
              onChange={(event) => setSendTime(event.target.value)}
              aria-label="固定发送时刻"
            />
            <span className="field-hint">
              填了则每天该时刻发送；留空则在发送时段内随机取时。好友单独设置的时间优先于本账号。
            </span>
          </div>
          {error && <div className="form-error">{error}</div>}
          <div className="form-actions">
            <button className="btn primary" type="submit" disabled={submitting}>
              {submitting ? '保存中…' : '保存'}
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
