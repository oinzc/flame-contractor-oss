import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { Account, SendPreviewRead, SendProgress, SendRead } from '../types'
import TaskDock from './TaskDock'

const PROGRESS_POLL_INTERVAL = 800

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
  minimized: boolean
  offset?: boolean
  onClose: () => void
  onMinimize: () => void
  onRestore: () => void
  onChanged: () => void
}

function createProgress(accountId: string, total: number, startedAt: string | null): SendProgress {
  return {
    accountId,
    status: 'running',
    total,
    completed: 0,
    currentTarget: null,
    stage: '准备发送',
    lastResult: null,
    overall: null,
    detail: null,
    startedAt,
    finishedAt: null,
  }
}

function progressPercent(progress: SendProgress | null, fallbackTotal = 0): number {
  const total = progress?.total ?? fallbackTotal
  if (total <= 0) return 0
  const completed = Math.min(Math.max(progress?.completed ?? 0, 0), total)
  return Math.round((completed / total) * 100)
}

function formatElapsed(startedAt: string | null, now: number): string {
  if (!startedAt) return '00:00'
  const started = Date.parse(startedAt)
  if (Number.isNaN(started)) return '00:00'
  const seconds = Math.max(0, Math.floor((now - started) / 1000))
  const minutes = Math.floor(seconds / 60)
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

function resultTone(overall: SendRead['overall']): string {
  if (overall === 'ok') return 'success'
  if (overall === 'partial') return 'partial'
  return 'error'
}

function resultSummary(overall: SendRead['overall']): string {
  if (overall === 'ok') return '发送完成，全部好友已处理。'
  if (overall === 'partial') return '发送完成，部分好友未成功，请查看明细。'
  if (overall === 'auth_expired') return '登录已失效，请先重新扫码。'
  if (overall === 'network_unavailable') return '网络不可用，本轮未开始。'
  return '发送流程出错，请查看详情。'
}

interface ProgressBarProps {
  progress: SendProgress | null
  fallbackTotal?: number
  compact?: boolean
}

function ProgressBar({ progress, fallbackTotal = 0, compact = false }: ProgressBarProps) {
  const percent = progressPercent(progress, fallbackTotal)
  return (
    <div
      className={`send-progress-track${compact ? ' compact' : ''}`}
      role="progressbar"
      aria-label="发送进度"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
    >
      <span className="send-progress-fill" style={{ transform: `scaleX(${percent / 100})` }} />
    </div>
  )
}

interface SendDockProps {
  account: Account
  progress: SendProgress | null
  fallbackTotal: number
  sending: boolean
  result: SendRead | null
  error: string | null
  offset?: boolean
  onRestore: () => void
  onClose: () => void
}

function SendDock({
  account,
  progress,
  fallbackTotal,
  sending,
  result,
  error,
  offset = false,
  onRestore,
  onClose,
}: SendDockProps) {
  const snapshot = progress ?? createProgress(account.id, fallbackTotal, null)
  const percent = progressPercent(snapshot, fallbackTotal)
  const failed = snapshot.status === 'failed' || Boolean(error)
  const completed = !sending && snapshot.status === 'completed'
  const currentTarget = snapshot.currentTarget ?? (sending ? '准备下一位好友…' : '流程结束')
  const statusLabel = completed ? '100%' : failed ? '需查看' : '进行中'

  return (
    <TaskDock
      kind="send"
      account={account}
      mark="↗"
      kicker={completed ? 'SEND COMPLETE' : 'BACKGROUND SEND'}
      statusLabel={statusLabel}
      percent={percent}
      progressLabel={`已处理 ${snapshot.completed} / ${snapshot.total || fallbackTotal} 位好友`}
      stage={error ?? snapshot.stage}
      currentItem={currentTarget}
      note={
        sending
          ? '大面板可以安全收起，发送仍由后端继续执行。'
          : result
            ? resultSummary(result.overall)
            : '任务已停止，可打开详情重试。'
      }
      failed={failed}
      complete={completed}
      offset={offset}
      onRestore={onRestore}
      restoreLabel="查看详情"
      onClose={!sending ? onClose : undefined}
      closeLabel="关闭任务"
    />
  )
}

export default function SendModal({
  account,
  minimized,
  offset = false,
  onClose,
  onMinimize,
  onRestore,
  onChanged,
}: Props) {
  const [preview, setPreview] = useState<SendPreviewRead | null>(null)
  const [result, setResult] = useState<SendRead | null>(null)
  const [progress, setProgress] = useState<SendProgress | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [sendStartedAt, setSendStartedAt] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const progressTimerRef = useRef<number | null>(null)
  const completionTimerRef = useRef<number | null>(null)
  const sendingRef = useRef(false)
  const minimizedRef = useRef(minimized)

  useEffect(() => {
    minimizedRef.current = minimized
  }, [minimized])

  useEffect(() => {
    let cancelled = false
    setPreview(null)
    setResult(null)
    setProgress(null)
    setError(null)
    api
      .sendPreview(account.id)
      .then((nextPreview) => {
        if (!cancelled) setPreview(nextPreview)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : '预览加载失败')
      })

    return () => {
      cancelled = true
      clearProgressTimer()
      clearCompletionTimer()
    }
  }, [account.id])

  useEffect(() => {
    if (!sending) return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [sending])

  function clearProgressTimer() {
    if (progressTimerRef.current !== null) {
      window.clearTimeout(progressTimerRef.current)
      progressTimerRef.current = null
    }
  }

  function clearCompletionTimer() {
    if (completionTimerRef.current !== null) {
      window.clearTimeout(completionTimerRef.current)
      completionTimerRef.current = null
    }
  }

  async function pollProgress() {
    try {
      const snapshot = await api.sendProgress(account.id)
      if (snapshot.status !== 'idle') setProgress(snapshot)
    } catch {
      // 发送请求本身负责报告最终错误；轮询短暂失败不打断发送。
    }
    if (sendingRef.current) {
      progressTimerRef.current = window.setTimeout(() => void pollProgress(), PROGRESS_POLL_INTERVAL)
    }
  }

  async function handleSend() {
    if (!preview || preview.items.length === 0 || sending) return
    clearCompletionTimer()
    setError(null)
    const total = preview.items.length
    const startedAt = new Date().toISOString()
    setSendStartedAt(startedAt)
    setProgress(createProgress(account.id, total, startedAt))
    setNow(Date.now())
    sendingRef.current = true
    setSending(true)
    void pollProgress()

    try {
      const sendResult = await api.sendNow(account.id)
      const terminalStatus = sendResult.overall === 'ok' || sendResult.overall === 'partial' ? 'completed' : 'failed'
      const finishedAt = new Date().toISOString()
      setProgress((previous) => {
        const snapshot = previous ?? createProgress(account.id, total, startedAt)
        return {
          ...snapshot,
          status: terminalStatus,
          completed: terminalStatus === 'completed' ? total : snapshot.completed,
          currentTarget: null,
          stage:
            sendResult.overall === 'auth_expired'
              ? '登录失效'
              : sendResult.overall === 'network_unavailable'
                ? '网络不可用'
                : terminalStatus === 'completed'
                  ? '发送完成'
                  : '流程结束',
          overall: sendResult.overall,
          detail: sendResult.detail,
          finishedAt,
        }
      })
      setResult(sendResult)
      onChanged()
      if (minimizedRef.current && terminalStatus === 'completed') {
        completionTimerRef.current = window.setTimeout(() => onClose(), 900)
      }
    } catch (err) {
      const message = err instanceof ApiError ? err.message : '发送失败'
      setError(message)
      setProgress((previous) => ({
        ...(previous ?? createProgress(account.id, total, startedAt)),
        status: 'failed',
        stage: '发送请求失败',
        detail: message,
        finishedAt: new Date().toISOString(),
      }))
    } finally {
      sendingRef.current = false
      clearProgressTimer()
      setSending(false)
    }
  }

  function handleHeaderAction() {
    if (sending) {
      onMinimize()
    } else {
      onClose()
    }
  }

  function handleRestore() {
    clearCompletionTimer()
    onRestore()
  }

  const fallbackTotal = preview?.items.length ?? progress?.total ?? 0
  const displayedProgress = progress ?? createProgress(account.id, fallbackTotal, sendStartedAt)

  if (minimized) {
    return (
      <SendDock
        account={account}
        progress={progress}
        fallbackTotal={fallbackTotal}
        sending={sending}
        result={result}
        error={error}
        offset={offset}
        onRestore={handleRestore}
        onClose={onClose}
      />
    )
  }

  return (
    <div className="modal-overlay send-overlay">
      <div className="modal send-modal" role="dialog" aria-modal="true" aria-label={`发送确认，${account.displayName}`}>
        <div className="send-modal-head">
          <div className="send-modal-identity">
            <span className="send-modal-mark" aria-hidden="true">
              焰
            </span>
            <div>
              <span className="send-modal-kicker">MANUAL SEND / {account.id.slice(0, 8)}</span>
              <h2 className="modal-title">发送确认 · {account.displayName}</h2>
            </div>
          </div>
          <button className="btn ghost small" type="button" onClick={handleHeaderAction}>
            {sending ? '收起' : '关闭'}
          </button>
        </div>

        <div className="send-modal-body">
          {error && <div className="form-error">{error}</div>}

          {sending && (
            <section className="send-live-panel" aria-live="polite">
              <div className="send-live-intro">
                <div>
                  <span className="send-section-kicker">LIVE EXECUTION</span>
                  <h3>续焰任务正在执行</h3>
                  <p>窗口可以收起，后端发送请求会继续运行。</p>
                </div>
                <strong className="send-live-percent">{progressPercent(displayedProgress, fallbackTotal)}%</strong>
              </div>

              <ProgressBar progress={displayedProgress} fallbackTotal={fallbackTotal} />
              <div className="send-progress-meta send-live-meta">
                <span>
                  已处理 <b>{displayedProgress.completed}</b> / {displayedProgress.total} 位好友
                </span>
                <span>{formatElapsed(displayedProgress.startedAt ?? sendStartedAt, now)} 已用时</span>
              </div>

              <div className="send-current-row">
                <span className="send-live-dot" aria-hidden="true" />
                <div>
                  <span>{displayedProgress.stage}</span>
                  <strong>{displayedProgress.currentTarget ?? '准备队列…'}</strong>
                </div>
              </div>

              <div className="send-live-guard">
                <span aria-hidden="true">i</span>
                <p>发送终态以“气泡真实出现且无失败标记”为准；存疑对象不会自动重发。</p>
              </div>
            </section>
          )}

          {!sending && !result && preview && (
            <>
              <p className="send-modal-copy">
                每个好友今天只会发送一次；发送前会严格核对好友身份，存疑则不发。
              </p>

              {preview.items.length === 0 ? (
                <div className="empty send-empty">
                  <div className="empty-title">没有可发送的好友</div>
                  <p className="empty-copy">请先到「好友」页添加并启用好友。</p>
                </div>
              ) : (
                <>
                  <div className="send-queue-summary">
                    <div className="send-queue-total">
                      <strong>{preview.items.length}</strong>
                      <span>位好友</span>
                      <small>待处理</small>
                    </div>
                    <div className="send-queue-rule">
                      <span className="send-section-kicker">SAFE SEND</span>
                      <strong>每位好友每日仅发送一次</strong>
                      <p>先校验好友身份，再按队列逐一处理。</p>
                    </div>
                  </div>

                  <div className="send-list-heading">
                    <div>
                      <span className="send-section-kicker">QUEUE</span>
                      <strong>本次发送队列</strong>
                    </div>
                    <span className="send-list-date">{preview.businessDate}</span>
                  </div>

                  <div className="send-list-scroll" role="list" aria-label="发送队列预览">
                    {preview.items.map((item, index) => (
                      <div className="send-item" key={item.targetId} role="listitem">
                        <span className="send-item-index">{String(index + 1).padStart(2, '0')}</span>
                        <div className="send-item-content">
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
                      </div>
                    ))}
                  </div>
                </>
              )}

              <div className="send-modal-footer">
                <span className="send-footnote">可随时收起执行面板，发送不会被取消。</span>
                <div className="form-actions">
                  <button
                    className="btn primary"
                    type="button"
                    disabled={preview.items.length === 0}
                    onClick={() => void handleSend()}
                  >
                    确认发送 · {preview.items.length}
                  </button>
                  <button className="btn" type="button" onClick={onClose}>
                    取消
                  </button>
                </div>
              </div>
            </>
          )}

          {!sending && result && (
            <>
              <section className={`send-result-hero ${resultTone(result.overall)}`} aria-live="polite">
                <div>
                  <span className="send-section-kicker">TERMINAL RESULT</span>
                  <h3>{resultSummary(result.overall)}</h3>
                  {result.detail && <p>{result.detail}</p>}
                </div>
                <strong>{progressPercent(displayedProgress, fallbackTotal)}%</strong>
              </section>

              <div className="send-result-scroll dry-result">
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

              <div className="send-modal-footer">
                <span className="send-footnote">结果已写入本地发送记录，可在任务页继续复核。</span>
                <div className="form-actions">
                  <button className="btn primary small" type="button" onClick={onClose}>
                    完成
                  </button>
                </div>
              </div>
            </>
          )}

          {!sending && !result && !preview && !error && (
            <div className="send-loading" aria-live="polite">
              <span className="send-live-dot" aria-hidden="true" />
              <span>正在生成发送队列…</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
