import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type { Account, SendPreviewRead, SendRead } from '../types'

const RESULT_LABELS: Record<string, string> = {
  succeeded: '已发送',
  duplicate_succeeded: '今日已发送（跳过）',
  duplicate_unknown: '今日结果未确认（跳过）',
  unknown_requires_review: '结果未确认，请人工核对',
  failed_retryable: '发送失败，可重试',
  failed_terminal: '发送失败',
  not_found: '未找到好友',
  ambiguous: '身份存疑，未发送',
  skipped: '跳过',
}

const RESULT_TONES: Record<string, string> = {
  succeeded: 'ok',
  duplicate_succeeded: 'warn',
  duplicate_unknown: 'bad',
  unknown_requires_review: 'bad',
  failed_retryable: 'bad',
  failed_terminal: 'bad',
  not_found: 'bad',
  ambiguous: 'bad',
  skipped: 'warn',
}

interface Props {
  account: Account
  onClose: () => void
  onChanged: () => void
}

export default function SendModal({ account, onClose, onChanged }: Props) {
  const [preview, setPreview] = useState<SendPreviewRead | null>(null)
  const [result, setResult] = useState<SendRead | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)

  useEffect(() => {
    api
      .sendPreview(account.id)
      .then(setPreview)
      .catch((err) => setError(err instanceof ApiError ? err.message : '预览加载失败'))
  }, [account.id])

  async function handleSend() {
    setError(null)
    setSending(true)
    try {
      const sendResult = await api.sendNow(account.id)
      setResult(sendResult)
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '发送失败')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal" role="dialog" aria-modal="true" aria-label="发送确认" style={{ width: 520 }}>
        <div className="modal-head">
          <h2 className="modal-title">发送确认 · {account.displayName}</h2>
          <button className="btn ghost small" type="button" onClick={onClose}>
            关闭
          </button>
        </div>
        <p className="modal-sub">
          每个好友今天只会发送一次；发送前会严格核对好友身份，存疑则不发。
        </p>

        {error && <div className="form-error">{error}</div>}

        {!result && preview && (
          <>
            {preview.items.length === 0 ? (
              <div className="empty" style={{ padding: '24px 16px' }}>
                <div className="empty-title">没有可发送的好友</div>
                <p className="empty-copy">请先到「好友」页添加并启用好友。</p>
              </div>
            ) : (
              <div className="send-list">
                {preview.items.map((item) => (
                  <div className="send-item" key={item.targetId}>
                    <div className="send-item-head">
                      <span className="send-name">{item.targetName}</span>
                      {item.reason && <span className="send-reason">{item.reason}</span>}
                    </div>
                    {item.message ? (
                      <div className="send-msg">{item.message}</div>
                    ) : (
                      <div className="send-msg muted">无消息内容</div>
                    )}
                  </div>
                ))}
              </div>
            )}
            <div className="form-actions">
              <button
                className="btn primary"
                type="button"
                disabled={sending || preview.items.length === 0}
                onClick={() => void handleSend()}
              >
                {sending ? '发送中…' : '确认发送'}
              </button>
              <button className="btn" type="button" onClick={onClose}>
                取消
              </button>
            </div>
          </>
        )}

        {result && (
          <>
            <div className="send-overall">
              {result.overall === 'ok' && '发送完成，全部好友已处理。'}
              {result.overall === 'partial' && '发送完成，部分好友未成功，请查看明细。'}
              {result.overall === 'auth_expired' && '登录已失效，请先重新扫码。'}
              {result.overall === 'error' && '发送流程出错。'}
              {result.detail && <span className="send-overall-detail">{result.detail}</span>}
            </div>
            <div className="dry-result">
              <ul>
                {result.results.map((item, index) => (
                  <li key={`${item.target}-${index}`}>
                    <span className="dry-target">{item.target}</span>
                    <span className={`dry-status ${RESULT_TONES[item.status] ?? ''}`}>
                      {RESULT_LABELS[item.status] ?? item.status}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="form-actions">
              <button className="btn primary small" type="button" onClick={onClose}>
                完成
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
