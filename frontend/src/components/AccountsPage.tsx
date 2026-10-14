import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { Account, DryRunRead, LoginStatus } from '../types'
import { ACCOUNT_STATUS_LABELS, ACCOUNT_STATUS_TONES, formatDateTime } from '../lib/format'
import StatusBadge from './StatusBadge'
import EditAccountModal from './EditAccountModal'
import QrCodeImage from './QrCodeImage'

const DRY_RUN_LABELS: Record<string, string> = {
  found: '已定位 · 未发送',
  not_found: '未找到',
  ambiguous: '身份存疑',
}

// 后端返回的机器可读失败原因 → 可操作的提示（未知原因仍回退到通用文案）
const LOGIN_FAILURE_HINTS: Record<string, string> = {
  no_desktop: '当前部署没有桌面环境，无法弹出浏览器窗口；请改用「截图扫码」登录。',
  qr_unavailable: '登录二维码未能在页面上生成；请稍后重试，或检查服务器到抖音的网络。',
}

interface Props {
  accounts: Account[]
  onChanged: () => void
  onCreateAccount: () => void
  onOpenSend: (account: Account, force: boolean) => void
}

export default function AccountsPage({ accounts, onChanged, onCreateAccount, onOpenSend }: Props) {
  const [busyId, setBusyId] = useState<string | null>(null)
  const [dryRunAccountId, setDryRunAccountId] = useState<string | null>(null)
  const [loginStates, setLoginStates] = useState<Record<string, LoginStatus>>({})
  const [qrStates, setQrStates] = useState<Record<string, LoginStatus>>({})
  const [qrTicks, setQrTicks] = useState<Record<string, number>>({})
  const [verifyOptions, setVerifyOptions] = useState<Record<string, string[]>>({})
  const [verifyMsg, setVerifyMsg] = useState<Record<string, string>>({})
  const [verifyBusy, setVerifyBusy] = useState(false)
  const [codeDraft, setCodeDraft] = useState<Record<string, string>>({})
  const [dryResults, setDryResults] = useState<Record<string, DryRunRead>>({})
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({})
  const [editingAccount, setEditingAccount] = useState<Account | null>(null)
  const timersRef = useRef<Record<string, number>>({})

  useEffect(() => {
    const timers = timersRef.current
    return () => {
      Object.values(timers).forEach((timer) => window.clearTimeout(timer))
    }
  }, [])

  // 拉取各账号的截图扫码状态：让 API/他处发起的进行中流程在页面上可见
  useEffect(() => {
    let cancelled = false
    for (const account of accounts) {
      api
        .qrLoginStatus(account.id)
        .then((status) => {
          if (cancelled) return
          if (status.state !== 'idle') {
            setQrStates((previous) => ({ ...previous, [account.id]: status }))
          }
          if (
            status.state === 'preparing' ||
            status.state === 'waiting_scan' ||
            status.state === 'verification_required'
          ) {
            // 接管轮询：持续刷新二维码截图并跟踪登录结果
            void pollQrLogin(account.id)
          }
        })
        .catch(() => {
          /* 状态读取失败不打扰页面 */
        })
    }
    return () => {
      cancelled = true
    }
  }, [accounts])

  function setLoginState(accountId: string, status: LoginStatus) {
    setLoginStates((previous) => ({ ...previous, [accountId]: status }))
  }

  function clearError(accountId: string) {
    setActionErrors((previous) => {
      const next = { ...previous }
      delete next[accountId]
      return next
    })
  }

  function setError(accountId: string, message: string) {
    setActionErrors((previous) => ({ ...previous, [accountId]: message }))
  }

  async function pollLogin(accountId: string) {
    try {
      const status = await api.loginStatus(accountId)
      setLoginState(accountId, status)
      if (status.state === 'succeeded' || status.state === 'failed' || status.state === 'expired') {
        if (status.state === 'succeeded') {
          onChanged()
        }
        return
      }
    } catch (err) {
      setError(accountId, err instanceof ApiError ? err.message : '状态查询失败')
      return
    }
    timersRef.current[accountId] = window.setTimeout(() => {
      void pollLogin(accountId)
    }, 2000)
  }

  async function handleStartLogin(account: Account) {
    clearError(account.id)
    try {
      const status = await api.startLogin(account.id)
      setLoginState(account.id, status)
      void pollLogin(account.id)
    } catch (err) {
      setError(account.id, err instanceof ApiError ? err.message : '发起登录失败')
    }
  }

  async function handleCancelLogin(account: Account) {
    const timer = timersRef.current[account.id]
    if (timer !== undefined) {
      window.clearTimeout(timer)
      delete timersRef.current[account.id]
    }
    try {
      const status = await api.cancelLogin(account.id)
      setLoginState(account.id, status)
    } catch (err) {
      setError(account.id, err instanceof ApiError ? err.message : '取消失败')
    }
  }

  function setQrState(accountId: string, status: LoginStatus) {
    setQrStates((previous) => ({ ...previous, [accountId]: status }))
  }

  async function pollQrLogin(accountId: string) {
    try {
      const status = await api.qrLoginStatus(accountId)
      setQrState(accountId, status)
      if (status.state === 'succeeded' || status.state === 'failed' || status.state === 'expired') {
        if (status.state === 'succeeded') {
          onChanged()
        }
        return
      }
      // 截图端点 no-store，用 tick 破坏缓存让 <img> 拉到最新帧
      setQrTicks((previous) => ({ ...previous, [accountId]: Date.now() }))
      if (status.state === 'verification_required') {
        // 「身份验证」等待中：同步读取弹窗当前可见的验证方式，供远程操作
        try {
          const options = await api.loginVerificationOptions(accountId)
          if (options.state === 'verification_required') {
            setVerifyOptions((previous) => ({ ...previous, [accountId]: options.options }))
          }
        } catch {
          /* 流程已结束（验证完成/超时），下一轮状态轮询会收口 */
        }
      }
    } catch (err) {
      setError(accountId, err instanceof ApiError ? err.message : '截图扫码状态查询失败')
      return
    }
    timersRef.current[`qr-${accountId}`] = window.setTimeout(() => {
      void pollQrLogin(accountId)
    }, 2500)
  }

  async function handleStartQrLogin(account: Account) {
    clearError(account.id)
    try {
      const status = await api.startQrLogin(account.id)
      setQrState(account.id, status)
      void pollQrLogin(account.id)
    } catch (err) {
      setError(account.id, err instanceof ApiError ? err.message : '发起截图扫码失败')
    }
  }

  async function handleCancelQrLogin(account: Account) {
    const timer = timersRef.current[`qr-${account.id}`]
    if (timer !== undefined) {
      window.clearTimeout(timer)
      delete timersRef.current[`qr-${account.id}`]
    }
    try {
      const status = await api.cancelLogin(account.id)
      setQrState(account.id, status)
    } catch (err) {
      setError(account.id, err instanceof ApiError ? err.message : '取消失败')
    }
  }

  async function handleVerifySelect(account: Account, option: string) {
    setVerifyBusy(true)
    setVerifyMsg((previous) => ({ ...previous, [account.id]: '' }))
    try {
      const result = await api.selectVerificationOption(account.id, option)
      setVerifyMsg((previous) => ({
        ...previous,
        [account.id]: result.detail ?? '已选择验证方式',
      }))
    } catch (err) {
      setVerifyMsg((previous) => ({
        ...previous,
        [account.id]: err instanceof ApiError ? err.message : '选择验证方式失败',
      }))
    } finally {
      setVerifyBusy(false)
    }
  }

  async function handleVerifySubmit(account: Account) {
    const code = (codeDraft[account.id] ?? '').trim()
    if (!code) return
    setVerifyBusy(true)
    setVerifyMsg((previous) => ({ ...previous, [account.id]: '' }))
    try {
      const result = await api.submitVerificationCode(account.id, code)
      setVerifyMsg((previous) => ({
        ...previous,
        [account.id]: result.detail ?? '验证码已提交',
      }))
      setCodeDraft((previous) => ({ ...previous, [account.id]: '' }))
    } catch (err) {
      setVerifyMsg((previous) => ({
        ...previous,
        [account.id]: err instanceof ApiError ? err.message : '提交验证码失败',
      }))
    } finally {
      setVerifyBusy(false)
    }
  }

  async function handleDryRun(account: Account) {
    clearError(account.id)
    setBusyId(account.id)
    setDryRunAccountId(account.id)
    try {
      const result = await api.dryRun(account.id)
      setDryResults((previous) => ({ ...previous, [account.id]: result }))
      onChanged()
    } catch (err) {
      setError(account.id, err instanceof ApiError ? err.message : '定位校验失败')
    } finally {
      setBusyId(null)
      setDryRunAccountId(null)
    }
  }

  async function handleToggle(account: Account) {
    setBusyId(account.id)
    try {
      await api.patchAccount(account.id, { enabled: !account.enabled })
      onChanged()
    } catch (err) {
      setError(account.id, err instanceof ApiError ? err.message : '操作失败')
    } finally {
      setBusyId(null)
    }
  }

  async function handleRename(account: Account) {
    const input = window.prompt('修改账号显示名称：', account.displayName)
    if (input === null || input.trim() === '' || input.trim() === account.displayName) {
      return
    }
    clearError(account.id)
    try {
      await api.patchAccount(account.id, { displayName: input.trim() })
      onChanged()
    } catch (err) {
      setError(account.id, err instanceof ApiError ? err.message : '改名失败')
    }
  }

  async function handleSaveEdit(account: Account, patch: {
    sendWindowStart: string
    sendWindowEnd: string
    sendTimeHhmm: string | null
  }) {
    clearError(account.id)
    try {
      await api.patchAccount(account.id, patch)
      setEditingAccount(null)
      onChanged()
    } catch (err) {
      setError(account.id, err instanceof ApiError ? err.message : '保存失败')
      throw err
    }
  }

  async function handleDeleteAccount(account: Account) {
    const confirmed = window.confirm(
      `确定删除账号「${account.displayName}」？\n有好友或发送记录的账号无法删除，登录数据会一并清理。`,
    )
    if (!confirmed) {
      return
    }
    clearError(account.id)
    try {
      await api.deleteAccount(account.id)
      onChanged()
    } catch (err) {
      setError(account.id, err instanceof ApiError ? err.message : '删除失败')
    }
  }

  return (
    <div className="main-col">
      <div className="page-head">
        <div>
          <h1>账号管理</h1>
          <p className="sub">每个账号使用独立的浏览器登录，互不影响。</p>
        </div>
        <button className="btn primary small" type="button" onClick={onCreateAccount}>
          新建账号
        </button>
      </div>

      {accounts.length === 0 ? (
        <div className="empty">
          <div className="empty-title">还没有账号</div>
          <p className="empty-copy">
            先创建一个账号，再扫码登录。账号是本地标识，不保存任何登录凭据。
          </p>
          <button className="btn primary small" type="button" onClick={onCreateAccount}>
            新建账号
          </button>
        </div>
      ) : (
        <div className="account-grid">
          {accounts.map((account) => {
            const login = loginStates[account.id]
            const qr = qrStates[account.id]
            const dry = dryResults[account.id]
            const dryRunning = dryRunAccountId === account.id
            const error = actionErrors[account.id]
            const needsLogin =
              account.status === 'pending_login' || account.status === 'auth_expired'

            return (
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

                <div className="kv-rows">
                  <div className="kv-row">
                    <span className="k">发送时段</span>
                    <span className="v">
                      {account.sendTimeHhmm
                        ? `固定 ${account.sendTimeHhmm}`
                        : `${account.sendWindowStart}–${account.sendWindowEnd} 随机`}
                    </span>
                    <button
                      className="btn ghost small"
                      type="button"
                      style={{ marginLeft: 6 }}
                      onClick={() => setEditingAccount(account)}
                    >
                      编辑
                    </button>
                  </div>
                  <div className="kv-row">
                    <span className="k">时区</span>
                    <span className="v">{account.timezone}</span>
                  </div>
                  <div className="kv-row">
                    <span className="k">抖音身份</span>
                    <span className="v">{account.douyinNickname ?? '未识别（重新扫码登录后自动获取）'}</span>
                  </div>
                  <div className="kv-row">
                    <span className="k">上次验证</span>
                    <span className="v">{formatDateTime(account.lastVerifiedAt)}</span>
                  </div>
                </div>

                {login && login.state === 'waiting_scan' && (
                  <div className="flow-hint gold">
                    浏览器已打开，请用抖音 App 扫码。完成后本卡片自动更新。
                  </div>
                )}
                {login && login.state === 'verification_required' && (
                  <div className="flow-hint gold">{login.detail}</div>
                )}
                {login && login.state === 'succeeded' && (
                  <div className="flow-hint ember">
                    登录成功，账号已变为正常。
                    {login.selfNickname ? ` 当前抖音身份：${login.selfNickname}` : ''}
                  </div>
                )}
                {login && (login.state === 'failed' || login.state === 'expired') && (
                  <div className="flow-hint crimson">
                    {login.state === 'expired'
                      ? '等待扫码超时，请重新发起。'
                      : (LOGIN_FAILURE_HINTS[login.detail ?? ''] ?? '登录流程失败，请重试。')}
                  </div>
                )}

                {qr?.state === 'preparing' && (
                  <div className="flow-hint gold">正在渲染登录页，二维码生成后会自动显示…</div>
                )}
                {(qr?.state === 'waiting_scan' || qr?.state === 'verification_required') && (
                  <div className="qr-panel">
                    <QrCodeImage accountId={account.id} tick={qrTicks[account.id] ?? 0} />
                    {qr.state === 'verification_required' && (
                      <div className="flow-hint gold">{qr.detail}</div>
                    )}
                    {qr.state === 'verification_required' && (
                      <div className="verify-panel">
                        <div className="verify-options">
                          {(verifyOptions[account.id] ?? []).map((option) => (
                            <button
                              key={option}
                              className="btn ghost small"
                              type="button"
                              disabled={verifyBusy}
                              onClick={() => void handleVerifySelect(account, option)}
                            >
                              {option}
                            </button>
                          ))}
                          {(verifyOptions[account.id] ?? []).length === 0 && (
                            <span className="muted">正在读取验证方式…</span>
                          )}
                        </div>
                        <div className="verify-code-row">
                          <input
                            value={codeDraft[account.id] ?? ''}
                            inputMode="numeric"
                            placeholder="输入收到的验证码"
                            onChange={(event) =>
                              setCodeDraft((previous) => ({
                                ...previous,
                                [account.id]: event.target.value,
                              }))
                            }
                          />
                          <button
                            className="btn primary small"
                            type="button"
                            disabled={verifyBusy || !(codeDraft[account.id] ?? '').trim()}
                            onClick={() => void handleVerifySubmit(account)}
                          >
                            提交验证码
                          </button>
                        </div>
                        {verifyMsg[account.id] && (
                          <div className="qr-hint">{verifyMsg[account.id]}</div>
                        )}
                      </div>
                    )}
                    <div className="qr-hint">
                      截图扫码模式：打开抖音 App → 左上角「≡」→ 扫一扫 → 对准上方二维码。
                      二维码自动刷新，如手机端提示过期请稍候片刻再扫。
                    </div>
                    <button
                      className="btn small"
                      type="button"
                      onClick={() => handleCancelQrLogin(account)}
                    >
                      取消截图扫码
                    </button>
                  </div>
                )}
                {qr?.state === 'succeeded' && (
                  <div className="flow-hint ember">截图扫码登录成功，账号已变为正常。</div>
                )}
                {qr && (qr.state === 'failed' || qr.state === 'expired') && (
                  <div className="flow-hint crimson">
                    {qr.state === 'expired'
                      ? '等待扫码超时，请重新发起。'
                      : (LOGIN_FAILURE_HINTS[qr.detail ?? ''] ?? '截图扫码流程失败，请重试。')}
                  </div>
                )}

                {dryRunning && (
                  <div className="dry-progress" role="status" aria-live="polite">
                    <div className="dry-progress-head">
                      <div>
                        <span className="dry-progress-kicker">DRY RUN</span>
                        <strong>正在执行定位校验</strong>
                      </div>
                      <span className="dry-progress-state">进行中</span>
                    </div>
                    <div
                      className="dry-progress-track is-indeterminate"
                      role="progressbar"
                      aria-label="定位校验进度"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuetext="正在检查登录状态和好友定位"
                    >
                      <span className="dry-progress-fill" />
                    </div>
                    <p>正在检查登录状态并逐个核对好友；此过程只读，不会发送消息。</p>
                  </div>
                )}

                {dry && !dryRunning && (
                  <div className="dry-result">
                    <div className="dry-head">
                      {dry.overall === 'ok' && '定位校验完成 · 未发送任何消息'}
                      {dry.overall === 'auth_expired' && '登录已失效，请重新扫码'}
                      {dry.overall === 'error' && '定位校验出错'}
                    </div>
                    <ul>
                      {dry.targets.map((item) => (
                        <li key={item.target} className="dry-li">
                          <div className="dry-li-row">
                            <span className="dry-target">{item.target}</span>
                            <span className={`dry-status ${item.status}`}>
                              {DRY_RUN_LABELS[item.status]}
                              {item.status === 'ambiguous' && item.title ? `（${item.title}）` : ''}
                            </span>
                          </div>
                          {item.detail && <div className="dry-detail">{item.detail}</div>}
                        </li>
                      ))}
                    </ul>
                    {dry.visibleTitles.length > 0 && (
                      <div className="flow-hint" style={{ marginTop: 8 }}>
                        本次可见会话：{dry.visibleTitles.join('、')}
                      </div>
                    )}
                    {dry.diagnostics && (
                      <details className="diag-box">
                        <summary>诊断信息（如仍未找到，请展开复制发给开发者）</summary>
                        <pre>{JSON.stringify(dry.diagnostics, null, 2)}</pre>
                      </details>
                    )}
                  </div>
                )}

                {error && <div className="form-error">{error}</div>}

                <div className="account-actions">
                  {needsLogin && (
                    <button
                      className="btn primary small"
                      type="button"
                      disabled={login?.state === 'waiting_scan' || qr?.state === 'waiting_scan'}
                      onClick={() => handleStartLogin(account)}
                    >
                      扫码登录
                    </button>
                  )}
                  {login?.state === 'waiting_scan' && (
                    <button
                      className="btn small"
                      type="button"
                      onClick={() => handleCancelLogin(account)}
                    >
                      取消
                    </button>
                  )}
                  {qr?.state !== 'waiting_scan' && (
                    <button
                      className="btn small"
                      type="button"
                      onClick={() => handleStartQrLogin(account)}
                    >
                      {needsLogin ? '截图扫码' : '重新扫码登录'}
                    </button>
                  )}
                  {account.status === 'healthy' && (
                    <button
                      className="btn small"
                      type="button"
                      disabled={busyId === account.id}
                      onClick={() => handleDryRun(account)}
                    >
                      定位校验
                    </button>
                  )}
                  {account.status === 'healthy' && account.enabled && (
                    <>
                      <button
                        className="btn small"
                        type="button"
                        onClick={() => onOpenSend(account, false)}
                      >
                        立即发送
                      </button>
                      <button
                        className="btn small force-send-button"
                        type="button"
                        title="忽略今日已发送记录，处理全部启用好友"
                        aria-label={`强制发送全部启用好友，${account.displayName}`}
                        onClick={() => onOpenSend(account, true)}
                      >
                        强制发送全部
                      </button>
                    </>
                  )}
                  <button
                    className="btn ghost small"
                    type="button"
                    disabled={busyId === account.id}
                    onClick={() => handleToggle(account)}
                  >
                    {account.enabled ? '暂停' : '恢复'}
                  </button>
                  <button
                    className="btn ghost small"
                    type="button"
                    disabled={busyId === account.id}
                    onClick={() => handleRename(account)}
                  >
                    改名
                  </button>
                  <button
                    className="btn ghost small"
                    type="button"
                    disabled={busyId === account.id}
                    onClick={() => handleDeleteAccount(account)}
                  >
                    删除
                  </button>
                </div>
              </article>
            )
          })}
        </div>
      )}
      {editingAccount && (
        <EditAccountModal
          account={editingAccount}
          onSubmit={(patch) => handleSaveEdit(editingAccount, patch)}
          onClose={() => setEditingAccount(null)}
        />
      )}
    </div>
  )
}
