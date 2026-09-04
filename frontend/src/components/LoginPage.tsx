import { useState } from 'react'
import { api, ApiError, setToken } from '../api'

interface Props {
  onAuthed: () => void
}

export default function LoginPage({ onAuthed }: Props) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    if (!username.trim() || !password) {
      setError('请输入用户名和密码')
      return
    }
    setSubmitting(true)
    try {
      const result =
        mode === 'login'
          ? await api.authLogin({ username: username.trim(), password })
          : await api.authRegister({ username: username.trim(), password })
      setToken(result.token)
      onAuthed()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '操作失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  function switchMode(next: 'login' | 'register') {
    setMode(next)
    setError(null)
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
          {error && <div className="form-error">{error}</div>}
          <div className="form-actions">
            <button className="btn primary" type="submit" disabled={submitting}>
              {submitting ? '处理中…' : mode === 'login' ? '登录' : '注册并进入'}
            </button>
          </div>
        </form>

        <p className="login-foot">
          {mode === 'login' ? '首次使用：请先切换到「注册」，或使用默认账号 admin/admin。' : ''}
        </p>
      </div>
    </div>
  )
}
