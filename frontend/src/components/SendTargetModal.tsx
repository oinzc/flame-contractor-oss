import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { Account, ContractTarget, MessageTemplate, SendProgress, SendRead } from '../types'
import TaskDock from './TaskDock'

interface Props {
  account: Account
  target: ContractTarget
  templates: MessageTemplate[]
  onClose: () => void
  onChanged: () => void
}

const PROGRESS_POLL_INTERVAL = 800

const VARIABLE_DOC = [
  { token: '{{target_name}}', meaning: '好友昵称' },
  { token: '{{streak_days}}', meaning: '火花天数' },
  { token: '{{weekday}}', meaning: '星期几' },
  { token: '{{date}}', meaning: '今天日期' },
]

function previewContent(content: string): string {
  const today = new Date()
  const weekday = ['日', '一', '二', '三', '四', '五', '六'][today.getDay()]
  return content
    .replaceAll('{{target_name}}', '示例好友')
    .replaceAll('{{streak_days}}', '7')
    .replaceAll('{{weekday}}', `星期${weekday}`)
    .replaceAll('{{date}}', today.toLocaleDateString('zh-CN'))
}

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

function resultSummary(overall: SendRead['overall']): string {
  if (overall === 'ok') return '发送完成。'
  if (overall === 'partial') return '发送完成，结果未确认，请查看。'
  if (overall === 'auth_expired') return '登录已失效，请先重新扫码。'
  if (overall === 'network_unavailable') return '网络不可用，本轮未开始。'
  return '发送流程出错，请查看详情。'
}

function resultTone(overall: SendRead['overall']): string {
  if (overall === 'ok') return 'success'
  if (overall === 'partial') return 'partial'
  return 'error'
}

function progressPercent(progress: SendProgress | null): number {
  const total = progress?.total ?? 1
  if (total <= 0) return 0
  const completed = Math.min(Math.max(progress?.completed ?? 0, 0), total)
  return Math.round((completed / total) * 100)
}

export default function SendTargetModal({
  account,
  target,
  templates,
  onClose,
  onChanged,
}: Props) {
  const [mode, setMode] = useState<'template' | 'text'>('template')
  const [templateId, setTemplateId] = useState('')
  const [content, setContent] = useState('')
  const [sending, setSending] = useState(false)
  const [minimized, setMinimized] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<SendRead | null>(null)
  const [progress, setProgress] = useState<SendProgress | null>(null)
  const [sendStartedAt, setSendStartedAt] = useState<string | null>(null)
  const progressTimerRef = useRef<number | null>(null)
  const sendingRef = useRef(false)
  const minimizedRef = useRef(minimized)
  const targetRef = useRef(target)

  useEffect(() => {
    minimizedRef.current = minimized
  }, [minimized])

  useEffect(() => {
    // 目标可能被后续刷新替换（如换绑模板后），保持追踪当前目标名。
    targetRef.current = target
  }, [target])

  useEffect(() => {
    if (!sending) return undefined
    const timer = window.setInterval(() => {
      // 仅触发重渲染；无需记录 now。
    }, 1000)
    return () => window.clearInterval(timer)
  }, [sending])

  useEffect(() => {
    return () => {
      if (progressTimerRef.current !== null) {
        window.clearTimeout(progressTimerRef.current)
        progressTimerRef.current = null
      }
      ;(async () => {
        // 清理：若在途经仍为 running，前端关闭不打断发送，交由全局任务坞继续。
      })()
    }
  }, [])

  function clearProgressTimer() {
    if (progressTimerRef.current !== null) {
      window.clearTimeout(progressTimerRef.current)
      progressTimerRef.current = null
    }
  }

  async function pollProgress() {
    try {
      const snapshot = await api.sendProgress(account.id)
      if (snapshot.status !== 'idle') setProgress(snapshot)
    } catch {
      // 轮询短暂失败不打断发送；最终结果以 sendTarget 返回值/轮询终态为准。
    }
    if (sendingRef.current) {
      progressTimerRef.current = window.setTimeout(() => void pollProgress(), PROGRESS_POLL_INTERVAL)
    }
  }

  async function handleSend() {
    if (sending) return
    if (mode === 'template' && !templateId) {
      setError('请选择一个模板')
      return
    }
    if (mode === 'text' && !content.trim()) {
      setError('请输入要发送的内容')
      return
    }
    setSending(true)
    setError(null)
    setResult(null)
    const startedAt = new Date().toISOString()
    setSendStartedAt(startedAt)
    setProgress({ accountId: account.id, status: 'running', total: 1, completed: 0, currentTarget: targetRef.current.nicknameSnapshot, stage: '准备发送', lastResult: null, overall: null, detail: null, startedAt, finishedAt: null })
    sendingRef.current = true
    void pollProgress()

    try {
      const body = mode === 'template' ? { templateId } : { content }
      const sendResult = await api.sendTarget(account.id, targetRef.current.id, body)
      const terminalStatus = sendResult.overall === 'ok' || sendResult.overall === 'partial' ? 'completed' : 'failed'
      const finishedAt = new Date().toISOString()
      setProgress((previous) => ({
        ...(previous ?? { accountId: account.id, status: 'completed', total: 1, completed: 1, currentTarget: targetRef.current.nicknameSnapshot, stage: '发送完成', lastResult: sendResult.results[0]?.status ?? null, overall: sendResult.overall, detail: sendResult.detail, startedAt, finishedAt }),
        status: terminalStatus,
        completed: terminalStatus === 'completed' ? 1 : previous?.completed ?? 0,
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
      }))
      setResult(sendResult)
      onChanged()
      if (minimizedRef.current && terminalStatus === 'completed') {
        // 最小化时成功后稍作停留，再自动关闭任务坞。
        window.setTimeout(() => onClose(), 900)
      }
    } catch (err) {
      const message = err instanceof ApiError ? err.message : '发送失败'
      setError(message)
      setProgress((previous) => ({
        ...(previous ?? { accountId: account.id, status: 'failed', total: 1, completed: 0, currentTarget: targetRef.current.nicknameSnapshot, stage: '发送请求失败', lastResult: null, overall: 'error', detail: message, startedAt, finishedAt: new Date().toISOString() }),
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

  const enabledTemplates = templates.filter((template) => template.enabled)
  const preview = mode === 'text' && content ? previewContent(content) : ''
  const displayedProgress = progress ?? { accountId: account.id, status: 'idle', total: 1, completed: 0, currentTarget: target.nicknameSnapshot, stage: '准备发送', lastResult: null, overall: null, detail: null, startedAt: sendStartedAt, finishedAt: null }

  if (minimized) {
    const completed = !sending && progress?.status === 'completed'
    const failed = progress?.status === 'failed' || Boolean(error)
    return (
      <TaskDock
        kind="send"
        account={account}
        mark="↗"
        kicker={completed ? 'FORCE COMPLETE' : 'BACKGROUND FORCE'}
        statusLabel={completed ? '100%' : failed ? '需查看' : '进行中'}
        percent={progressPercent(progress)}
        progressLabel={`已处理 ${progress?.completed ?? 0} / ${progress?.total ?? 1}`}
        stage={error ?? progress?.stage ?? '准备发送'}
        currentItem={progress?.currentTarget ?? target.nicknameSnapshot}
        note={
          sending
            ? '强制发送会忽略今日成功记录；面板已收起，后端仍继续执行。'
            : result
              ? resultSummary(result.overall)
              : '任务已停止，可打开详情查看。'
        }
        failed={failed}
        complete={completed}
        onRestore={() => setMinimized(false)}
        restoreLabel="查看详情"
        onClose={!sending ? onClose : undefined}
        closeLabel="关闭任务"
      />
    )
  }

  return (
    <div className="modal-overlay">
      <div className="modal send-modal" role="dialog" aria-modal="true" aria-label="单好友强制发送">
        <div className="send-modal-head">
          <div className="send-modal-identity">
            <span className="send-modal-mark" aria-hidden="true">
              焰
            </span>
            <div>
              <span className="send-modal-kicker">TARGET FORCE / {account.displayName}</span>
              <h2 className="modal-title">发送给 · {target.nicknameSnapshot}</h2>
            </div>
          </div>
          <button className="btn ghost small" type="button" onClick={() => (sending ? setMinimized(true) : onClose())}>
            {sending ? '收起' : '关闭'}
          </button>
        </div>

        <div className="send-modal-body">
          {error && <div className="form-error">{error}</div>}

          {!result && (
            <>
              {sending && (
                <section className="send-live-panel" aria-live="polite">
                  <div className="send-live-intro">
                    <div>
                      <span className="send-section-kicker">LIVE EXECUTION</span>
                      <h3>正在发送</h3>
                      <p>面板可以收起，后端发送请求会继续运行并记录结果。</p>
                    </div>
                    <strong className="send-live-percent">{progressPercent(displayedProgress)}%</strong>
                  </div>
                  <div
                    className="send-progress-track"
                    role="progressbar"
                    aria-label="发送进度"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={progressPercent(displayedProgress)}
                  >
                    <span className="send-progress-fill" style={{ transform: `scaleX(${progressPercent(displayedProgress) / 100})` }} />
                  </div>
                  <div className="send-progress-meta send-live-meta">
                    <span>
                      已处理 <b>{displayedProgress.completed}</b> / {displayedProgress.total} 位好友
                    </span>
                  </div>
                  <div className="send-current-row">
                    <span className="send-live-dot" aria-hidden="true" />
                    <div>
                      <span>{displayedProgress.stage}</span>
                      <strong>{displayedProgress.currentTarget ?? '目标好友'}</strong>
                    </div>
                  </div>
                  <div className="send-live-guard">
                    <span aria-hidden="true">i</span>
                    <p>发送终态以“气泡真实出现且无失败标记”为准；存疑对象不会自动重发。</p>
                  </div>
                </section>
              )}

              {!sending && (
                <>
                  <div className="mode-tabs">
                    <button
                      className={`btn small${mode === 'template' ? ' primary' : ' ghost'}`}
                      type="button"
                      onClick={() => setMode('template')}
                    >
                      选模板
                    </button>
                    <button
                      className={`btn small${mode === 'text' ? ' primary' : ' ghost'}`}
                      type="button"
                      onClick={() => setMode('text')}
                    >
                      直接输入文本
                    </button>
                  </div>

                  {mode === 'template' ? (
                    <div className="field">
                      <label htmlFor="send-target-template">选择模板</label>
                      <select
                        id="send-target-template"
                        value={templateId}
                        onChange={(event) => setTemplateId(event.target.value)}
                      >
                        <option value="">— 选择启用模板 —</option>
                        {enabledTemplates.map((template) => (
                          <option key={template.id} value={template.id}>
                            {template.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : (
                    <div className="field">
                      <label htmlFor="send-target-content">输入内容</label>
                      <textarea
                        id="send-target-content"
                        value={content}
                        onChange={(event) => setContent(event.target.value)}
                        rows={4}
                        placeholder="例如：{{target_name}}，今天也要加油！"
                      />
                      <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>可用变量：</div>
                      <div className="variable-tags">
                        {VARIABLE_DOC.map((variable) => (
                          <code key={variable.token} className="var-tag" title={variable.meaning}>
                            {variable.token}
                          </code>
                        ))}
                      </div>
                      {preview && (
                        <div className="send-msg" style={{ marginTop: 10 }}>
                          预览：{preview}
                        </div>
                      )}
                    </div>
                  )}

                  <p className="send-modal-copy force">
                    本次为手动强制发送，会绕过当天成功记录。身份存疑、登录失效或结果未确认仍不会发送。
                  </p>

                  <div className="send-modal-footer">
                    <span className="send-footnote">发送终态以「气泡真实出现且无失败标记」为准。</span>
                    <div className="form-actions">
                      <button className="btn primary" type="button" onClick={() => void handleSend()}>
                        确认发送
                      </button>
                      <button className="btn" type="button" onClick={onClose}>
                        取消
                      </button>
                    </div>
                  </div>
                </>
              )}
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
                <strong>{progressPercent(displayedProgress)}%</strong>
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
        </div>
      </div>
    </div>
  )
}
