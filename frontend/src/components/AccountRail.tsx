import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { Account, DryRunRead, LoginStatus } from '../types'
import { ACCOUNT_STATUS_LABELS, ACCOUNT_STATUS_TONES } from '../lib/format'
import StatusBadge from './StatusBadge'

const DRY_RUN_LABELS: Record<string, string> = {
  found: '已定位 · 未发送',
  not_found: '未找到',
  ambiguous: '身份存疑',
}

interface Props {
  accounts: Account[]
  onToggle: (account: Account) => void
  onCreateAccount: () => void
  onChanged: () => void
}

export default function AccountRail({ accounts, onToggle, onCreateAccount, onChanged }: Props) {
  const [busyId, setBusyId] = useState<string | null>(null)
  const [loginStates, setLoginStates] = useState<Record<string, LoginStatus>>({})
  const [dryResults, setDryResults] = useState<Record<string, DryRunRead>>({})
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({})
  const timersRef = useRef<Record<string, number>>({})

  useEffect(() => {
    const timers = timersRef.current
    return () => {
      Object.values(timers).forEach((timer) => window.clearTimeout(timer))
    }
  }, [])

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
      setError(account.id, err instanceof ApiError ? err.message : 'Dry Run 失败')
    } finally {
      setBusyId(null)
    }
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
      {accounts.map((account) => {
        const login = loginStates[account.id]
        const dry = dryResults[account.id]
        const error = actionErrors[account.id]
        const needsLogin = account.status === 'pending_login' || account.status === 'auth_expired'

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

            <div className="account-window">
              <span>
                焰窗 {account.sendWindowStart}–{account.sendWindowEnd}
              </span>
              <span>{account.timezone}</span>
            </div>

            {login && login.state === 'waiting_scan' && (
              <div className="flow-hint gold">
                浏览器已打开，请用抖音 App 扫码。完成后本卡片自动更新。
              </div>
            )}
            {login && login.state === 'succeeded' && (
              <div className="flow-hint ember">登录成功，炉心已点燃。</div>
            )}
            {login && (login.state === 'failed' || login.state === 'expired') && (
              <div className="flow-hint crimson">
                {login.state === 'expired' ? '等待扫码超时，请重新发起。' : '登录流程失败，请重试。'}
              </div>
            )}

            {dry && (
              <div className="dry-result">
                <div className="dry-head">
                  {dry.overall === 'ok' && 'Dry Run 完成 · 未发送任何消息'}
                  {dry.overall === 'auth_expired' && '登录已失效，请重新扫码'}
                  {dry.overall === 'error' && 'Dry Run 出错，请查看运行记录'}
                </div>
                <ul>
                  {dry.targets.map((item) => (
                    <li key={item.target}>
                      <span className="dry-target">{item.target}</span>
                      <span className={`dry-status ${item.status}`}>
                        {DRY_RUN_LABELS[item.status]}
                        {item.status === 'ambiguous' && item.title ? `（${item.title}）` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
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
                <button className="btn ghost small" type="button" onClick={() => handleCancelLogin(account)}>
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
                  Dry Run
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
  )
}
