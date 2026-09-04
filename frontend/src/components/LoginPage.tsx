import { useState } from 'react'
import { api, ApiError, setToken } from '../api'

interface Props {
  onAuthed: () => void
}

type Mode = 'login' | 'register'

export default function LoginPage({ onAuthed }: Props) {
  const [mode, setMode] = useState<Mode>('login')
  const [showRecover, setShowRecover] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setNotice(null)
    if (!username.trim() || !password) {
      setError('请输入用户名和密码')
      return
    }
    setSubmitting(true)
    try {
      const result =
        mode === 'login'
          ? await api.authLogin({ username: username.trim(), password })
          : await api.authRegister({
              username: username.trim(),
              password,
              displayName: displayName.trim() || undefined,
              email: email.trim() || undefined,
            })
      setToken(result.token)
      onAuthed()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '操作失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRecover(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setNotice(null)
    if (!username.trim()) {
      setError('请输入要找回的用户名')
      return
    }
    setSubmitting(true)
    try {
      const result = await api.authRecoverPassword({ username: username.trim() })
      setNotice(result.message)
      setShowRecover(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '找回失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  function switchMode(next: Mode) {
    setMode(next)
    setError(null)
    setNotice(null)
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-mark" aria-hidden="true">
          🔥
        </div>
        <h1 className="login-title">焰之契约者</h1>
        <p className="login-sub">登录你的续火平台账号，管理所有抖音账号</p>

        <div className="login-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'login'}
            className={`login-tab${mode === 'login' ? ' active' : ''}`}
            onClick={() => switchMode('login')}
          >
            登录
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'register'}
            className={`login-tab${mode === 'register' ? ' active' : ''}`}
            onClick={() => switchMode('register')}
          >
            注册
          </button>
        </div>

        {showRecover ? (
          <form className="form-grid" onSubmit={handleRecover}>
            <div className="field">
              <label htmlFor="recover-username">用户名</label>
              <input
                id="recover-username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                maxLength={60}
                autoFocus
              />
              <span className="field-hint">
                本机版本暂不支持自助重置密码，提交后会提示联系平台管理员。
              </span>
            </div>
            {error && <div className="form-error">{error}</div>}
            {notice && <div className="form-error" style={{ color: 'var(--flame)' }}>{notice}</div>}
            <div className="form-actions">
              <button className="btn primary" type="submit" disabled={submitting}>
                {submitting ? '处理中…' : '找回密码'}
              </button>
              <button
                className="btn"
                type="button"
                onClick={() => setShowRecover(false)}
              >
                返回登录
              </button>
            </div>
          </form>
        ) : (
          <form className="form-grid" onSubmit={handleSubmit}>
            <div className="field">
              <label htmlFor="login-username">用户名</label>
              <input
                id="login-username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                maxLength={60}
                autoFocus
              />
            </div>
            <div className="field">
              <label htmlFor="login-password">密码</label>
              <input
                id="login-password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                maxLength={128}
              />
            </div>
            {mode === 'register' && (
              <>
                <div className="field">
                  <label htmlFor="login-displayname">显示名称（可选）</label>
                  <input
                    id="login-displayname"
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                    maxLength={60}
                  />
                  <span className="field-hint">留空则默认与用户名相同。</span>
                </div>
                <div className="field">
                  <label htmlFor="login-email">邮箱（可选）</label>
                  <input
                    id="login-email"
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    maxLength={255}
                  />
                </div>
              </>
            )}
            {error && <div className="form-error">{error}</div>}
            <div className="form-actions">
              <button className="btn primary" type="submit" disabled={submitting}>
                {submitting ? '处理中…' : mode === 'login' ? '登录' : '注册并进入'}
              </button>
            </div>
          </form>
        )}

        <p className="login-foot">
          {mode === 'login' && !showRecover ? (
            <button
              className="btn ghost small"
              type="button"
              onClick={() => {
                setShowRecover(true)
                setError(null)
                setNotice(null)
              }}
            >
              忘记密码？
            </button>
          ) : (
            ''
          )}
          {' · '}首次使用：请先切换到「注册」，或使用默认账号 admin/admin。
        </p>
      </div>
    </div>
  )
}
