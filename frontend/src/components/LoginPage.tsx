import { useEffect, useState } from 'react'
import { api, ApiError, setToken } from '../api'

interface Props {
  onAuthed: () => void
}

function FlameGlyph() {
  return (
    <svg viewBox="0 0 48 56" role="img" aria-label="焰之契约者">
      <path d="M25 3v15L13 31v14l11 8V37l10-11V13L25 3Z" />
      <path d="m37 22 8 9v14l-12 8V38l4-5V22Z" />
    </svg>
  )
}

export default function LoginPage({ onAuthed }: Props) {
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null)
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [passwordVisible, setPasswordVisible] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let active = true
    api
      .authSetupStatus()
      .then((result) => {
        if (active) setNeedsSetup(result.needsSetup)
      })
      .catch((err) => {
        if (active) setError(err instanceof ApiError ? err.message : '无法连接本机控制面')
      })
    return () => {
      active = false
    }
  }, [])

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    if (needsSetup === null) return
    if (!username.trim() || !password) {
      setError('请输入用户名和密码')
      return
    }
    if (needsSetup && password.length < 8) {
      setError('管理员密码至少需要 8 个字符')
      return
    }
    if (needsSetup && password !== passwordConfirm) {
      setError('两次输入的密码不一致')
      return
    }

    setSubmitting(true)
    try {
      const result = needsSetup
        ? await api.authInitialize({
            username: username.trim(),
            password,
            displayName: displayName.trim() || undefined,
          })
        : await api.authLogin({ username: username.trim(), password })
      setToken(result.token)
      onAuthed()
    } catch (err) {
      if (needsSetup && err instanceof ApiError && err.status === 409) {
        setNeedsSetup(false)
        setPassword('')
        setPasswordConfirm('')
      }
      setError(err instanceof ApiError ? err.message : '操作失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  const ready = needsSetup !== null

  return (
    <main className="login-page">
      <section className="login-intro" aria-labelledby="login-brand-title">
        <div className="login-brand">
          <span className="login-mark">
            <FlameGlyph />
          </span>
          <div>
            <p className="login-kicker">FLAME CONTRACTOR</p>
            <h1 id="login-brand-title">焰之契约者</h1>
          </div>
        </div>
        <div className="login-promise">
          <p className="login-eyebrow">本机续火控制台</p>
          <h2>守住每一段<br />重要的联系</h2>
          <p>管理多个抖音账号、续火计划与发送状态，让每次操作都有迹可循。</p>
        </div>
        <ul className="login-trust" aria-label="本地部署特性">
          <li><span>01</span>数据仅保存在本机</li>
          <li><span>02</span>自动调度可随时暂停</li>
          <li><span>03</span>发送结果全程留痕</li>
        </ul>
      </section>

      <section className="login-console" aria-labelledby="login-form-title">
        <div className="login-console-head">
          <span className={`status-lamp ${ready ? 'green' : 'amber'}`} aria-hidden="true" />
          <span>{ready ? 'CONTROL PLANE READY' : 'CONNECTING CONTROL PLANE'}</span>
        </div>
        <div className="login-card">
          <p className="login-card-index">AUTH / 01</p>
          <h2 id="login-form-title">
            {needsSetup === null ? '正在连接' : needsSetup ? '初始化管理员' : '欢迎回来'}
          </h2>
          <p className="login-sub">
            {needsSetup === null
              ? '正在确认此设备的账号状态…'
              : needsSetup
              ? '这是此设备首次启动。请创建唯一的首位管理员，之后新增用户将由管理员处理。'
              : '登录平台账号，继续管理你的续火计划。'}
          </p>

          <form className="login-form" onSubmit={handleSubmit}>
            <div className="field">
              <label htmlFor="login-username">用户名</label>
              <input
                id="login-username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                maxLength={60}
                placeholder={needsSetup ? '设置管理员用户名' : '输入用户名'}
                autoFocus
                required
              />
            </div>

            {needsSetup && (
              <div className="field">
                <label htmlFor="login-displayname">显示名称 <span>可选</span></label>
                <input
                  id="login-displayname"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  autoComplete="name"
                  maxLength={60}
                  placeholder="用于控制台内显示"
                />
              </div>
            )}

            <div className="field">
              <label htmlFor="login-password">密码</label>
              <div className="password-field">
                <input
                  id="login-password"
                  type={passwordVisible ? 'text' : 'password'}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete={needsSetup ? 'new-password' : 'current-password'}
                  minLength={needsSetup ? 8 : undefined}
                  maxLength={128}
                  placeholder={needsSetup ? '至少 8 个字符' : '输入密码'}
                  required
                />
                <button
                  type="button"
                  className="password-toggle"
                  aria-label={passwordVisible ? '隐藏密码' : '显示密码'}
                  aria-pressed={passwordVisible}
                  onClick={() => setPasswordVisible((visible) => !visible)}
                >
                  {passwordVisible ? '隐藏' : '显示'}
                </button>
              </div>
            </div>

            {needsSetup && (
              <div className="field">
                <label htmlFor="login-password-confirm">确认密码</label>
                <input
                  id="login-password-confirm"
                  type={passwordVisible ? 'text' : 'password'}
                  value={passwordConfirm}
                  onChange={(event) => setPasswordConfirm(event.target.value)}
                  autoComplete="new-password"
                  minLength={8}
                  maxLength={128}
                  placeholder="再次输入管理员密码"
                  required
                />
              </div>
            )}

            {error && <div className="form-error" role="alert">{error}</div>}

            <button className="btn primary login-submit" type="submit" disabled={!ready || submitting}>
              {submitting ? '处理中…' : needsSetup ? '创建管理员并进入' : '进入控制台'}
              <span aria-hidden="true">→</span>
            </button>
          </form>

          {!needsSetup && ready && (
            <p className="login-help">忘记密码？请联系平台管理员在“我的账号”中重置。</p>
          )}
        </div>
        <p className="login-local"><span aria-hidden="true" />本地部署 · 数据不离开此设备</p>
      </section>
    </main>
  )
}
