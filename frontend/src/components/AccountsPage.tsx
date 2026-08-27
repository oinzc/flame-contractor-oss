import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { Account, DryRunRead, LoginStatus } from '../types'
import { ACCOUNT_STATUS_LABELS, ACCOUNT_STATUS_TONES, formatDateTime } from '../lib/format'
import StatusBadge from './StatusBadge'
import SendModal from './SendModal'

const DRY_RUN_LABELS: Record<string, string> = {
  found: '已定位 · 未发送',
  not_found: '未找到',
  ambiguous: '身份存疑',
}

interface Props {
  accounts: Account[]
  onChanged: () => void
  onCreateAccount: () => void
}

export default function AccountsPage({ accounts, onChanged, onCreateAccount }: Props) {
  const [busyId, setBusyId] = useState<string | null>(null)
  const [loginStates, setLoginStates] = useState<Record<string, LoginStatus>>({})
  const [dryResults, setDryResults] = useState<Record<string, DryRunRead>>({})
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({})
  const [sendAccount, setSendAccount] = useState<Account | null>(null)
  const timersRef = useRef<Record<string, number>>({})

  useEffect(() => {
    const timers = timersRef.current
    return () => {
      Object.values(timers).forEach((timer) => window.clearTimeout(timer))
    }
  }, [])

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

  async function handleDryRun(account: Account) {
    clearError(account.id)
    setBusyId(account.id)
    try {
      const result = await api.dryRun(account.id)
      setDryResults((previous) => ({ ...previous, [account.id]: result }))
      onChanged()
    } catch (err) {
      setError(account.id, err instanceof ApiError ? err.message : '定位校验失败')
    } finally {
      setBusyId(null)
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
            const dry = dryResults[account.id]
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
                      {account.sendWindowStart}–{account.sendWindowEnd}
                    </span>
                  </div>
                  <div className="kv-row">
                    <span className="k">时区</span>
                    <span className="v">{account.timezone}</span>
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
                {login && login.state === 'succeeded' && (
                  <div className="flow-hint ember">登录成功，账号已变为正常。</div>
                )}
                {login && (login.state === 'failed' || login.state === 'expired') && (
                  <div className="flow-hint crimson">
                    {login.state === 'expired' ? '等待扫码超时，请重新发起。' : '登录流程失败，请重试。'}
                  </div>
                )}

                {dry && (
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
                      disabled={login?.state === 'waiting_scan'}
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
                    <button
                      className="btn small"
                      type="button"
                      onClick={() => setSendAccount(account)}
                    >
                      立即发送
                    </button>
                  )}
                  <button
                    className="btn ghost small"
                    type="button"
                    disabled={busyId === account.id}
                    onClick={() => handleToggle(account)}
                  >
                    {account.enabled ? '暂停' : '恢复'}
                  </button>
                </div>
              </article>
            )
          })}
        </div>
      )}

      {sendAccount && (
        <SendModal
          account={sendAccount}
          onClose={() => setSendAccount(null)}
          onChanged={() => void onChanged()}
        />
      )}
    </div>
  )
}
