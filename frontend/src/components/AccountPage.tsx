import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type { AuthSessionRead, User } from '../types'
import { formatDateTime } from '../lib/format'

interface Props {
  user: User
  onLogout: () => void
}

type Section =
  | { kind: 'profile' }
  | { kind: 'password' }
  | { kind: 'sessions' }
  | { kind: 'admin' }

export default function AccountPage({ user, onLogout }: Props) {
  const [section, setSection] = useState<Section['kind']>('profile')
  const [displayName, setDisplayName] = useState(user.displayName ?? '')
  const [email, setEmail] = useState(user.email ?? '')
  const [passwordCurrent, setPasswordCurrent] = useState('')
  const [passwordNew, setPasswordNew] = useState('')
  const [passwordNew2, setPasswordNew2] = useState('')
  const [sessions, setSessions] = useState<AuthSessionRead[]>([])
  const [profileError, setProfileError] = useState<string | null>(null)
  const [profileNotice, setProfileNotice] = useState<string | null>(null)
  const [pwError, setPwError] = useState<string | null>(null)
  const [pwNotice, setPwNotice] = useState<string | null>(null)
  const [submittingProfile, setSubmittingProfile] = useState(false)
  const [submittingPw, setSubmittingPw] = useState(false)
  const [revoking, setRevoking] = useState<string | null>(null)
  const [adminUsers, setAdminUsers] = useState<User[]>([])
  const [adminError, setAdminError] = useState<string | null>(null)

  const loadSessions = () =>
    api
      .authSessions()
      .then(setSessions)
      .catch(() => {
        /* 会话读取失败不打扰 */
      })
  const loadUsers = () =>
    api
      .authListUsers()
      .then(setAdminUsers)
      .catch(() => {
        /* 非管理员或失败静默 */
      })

  useEffect(() => {
    void loadSessions()
  }, [])
  useEffect(() => {
    if (user.isSuperuser) void loadUsers()
  }, [user.isSuperuser])

  async function handleSaveProfile(event: React.FormEvent) {
    event.preventDefault()
    setProfileError(null)
    setProfileNotice(null)
    setSubmittingProfile(true)
    try {
      await api.authUpdateMe({ displayName: displayName.trim(), email: email.trim() })
      setProfileNotice('资料已更新')
    } catch (err) {
      setProfileError(err instanceof ApiError ? err.message : '保存失败，请重试')
    } finally {
      setSubmittingProfile(false)
    }
  }

  async function handleChangePassword(event: React.FormEvent) {
    event.preventDefault()
    setPwError(null)
    setPwNotice(null)
    if (!passwordCurrent || !passwordNew) {
      setPwError('请填写当前密码和新密码')
      return
    }
    if (passwordNew !== passwordNew2) {
      setPwError('两次输入的新密码不一致')
      return
    }
    setSubmittingPw(true)
    try {
      await api.authChangePassword({ currentPassword: passwordCurrent, newPassword: passwordNew })
      setPwNotice('密码已更新，其他设备已退出')
      setPasswordCurrent('')
      setPasswordNew('')
      setPasswordNew2('')
      loadSessions()
    } catch (err) {
      setPwError(err instanceof ApiError ? err.message : '修改失败，请重试')
    } finally {
      setSubmittingPw(false)
    }
  }

  async function handleRevoke(session: AuthSessionRead) {
    if (session.current) return
    setRevoking(session.id)
    try {
      await api.authRevokeSession(session.id)
      loadSessions()
    } catch (err) {
      setProfileError(err instanceof ApiError ? err.message : '取消设备失败')
    } finally {
      setRevoking(null)
    }
  }

  async function handleAdminUpdate(target: User, patch: { password?: string; isSuperuser?: boolean }) {
    setAdminError(null)
    try {
      await api.authAdminUpdateUser(target.id, patch)
      loadUsers()
    } catch (err) {
      setAdminError(err instanceof ApiError ? err.message : '操作失败')
    }
  }

  const NAV: { key: Section['kind']; label: string }[] = [
    { key: 'profile', label: '个人资料' },
    { key: 'password', label: '修改密码' },
    { key: 'sessions', label: '已登录设备' },
    ...(user.isSuperuser ? [{ key: 'admin' as const, label: '平台用户' }] : []),
  ]

  return (
    <div className="main-col">
      <div className="page-head">
        <div>
          <h1>我的账号</h1>
          <p className="sub">管理你的平台账号资料、登录密码与已登录设备。</p>
        </div>
        <button className="btn small" type="button" onClick={onLogout}>
          退出登录
        </button>
      </div>

      <div className="account-profile-tabs" role="tablist">
        {NAV.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={section === item.key}
            className={`account-profile-tab${section === item.key ? ' active' : ''}`}
            onClick={() => setSection(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {section === 'profile' && (
        <section className="card-block">
          <h3 className="card-title">平台账号</h3>
          <div className="kv-rows">
            <div className="kv-row">
              <span className="k">用户名</span>
              <span className="v">{user.username}</span>
            </div>
            <div className="kv-row">
              <span className="k">角色</span>
              <span className="v">{user.isSuperuser ? '平台管理员' : '普通用户'}</span>
            </div>
          </div>
          <h3 className="card-title" style={{ marginTop: 18 }}>
            个人资料
          </h3>
          <form className="form-grid" onSubmit={handleSaveProfile}>
            <div className="field">
              <label htmlFor="profile-displayname">显示名称</label>
              <input
                id="profile-displayname"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                maxLength={60}
              />
            </div>
            <div className="field">
              <label htmlFor="profile-email">邮箱</label>
              <input
                id="profile-email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                maxLength={255}
              />
              <span className="field-hint">用于标识与找回；本机版本暂不发送邮件。</span>
            </div>
            {profileError && <div className="form-error">{profileError}</div>}
            {profileNotice && (
              <div className="form-error" style={{ color: 'var(--flame)' }}>
                {profileNotice}
              </div>
            )}
            <div className="form-actions">
              <button className="btn primary" type="submit" disabled={submittingProfile}>
                {submittingProfile ? '保存中…' : '保存资料'}
              </button>
            </div>
          </form>
        </section>
      )}

      {section === 'password' && (
        <section className="card-block">
          <h3 className="card-title">修改密码</h3>
          <form className="form-grid" onSubmit={handleChangePassword}>
            <div className="field">
              <label htmlFor="pw-current">当前密码</label>
              <input
                id="pw-current"
                type="password"
                value={passwordCurrent}
                onChange={(event) => setPasswordCurrent(event.target.value)}
                autoComplete="current-password"
                maxLength={128}
              />
            </div>
            <div className="field">
              <label htmlFor="pw-new">新密码</label>
              <input
                id="pw-new"
                type="password"
                value={passwordNew}
                onChange={(event) => setPasswordNew(event.target.value)}
                autoComplete="new-password"
                maxLength={128}
              />
            </div>
            <div className="field">
              <label htmlFor="pw-new2">确认新密码</label>
              <input
                id="pw-new2"
                type="password"
                value={passwordNew2}
                onChange={(event) => setPasswordNew2(event.target.value)}
                autoComplete="new-password"
                maxLength={128}
              />
            </div>
            <span className="field-hint">修改后除本设备外的所有登录会话将自动退出。</span>
            {pwError && <div className="form-error">{pwError}</div>}
            {pwNotice && (
              <div className="form-error" style={{ color: 'var(--flame)' }}>
                {pwNotice}
              </div>
            )}
            <div className="form-actions">
              <button className="btn primary" type="submit" disabled={submittingPw}>
                {submittingPw ? '更新中…' : '修改密码'}
              </button>
            </div>
          </form>
        </section>
      )}

      {section === 'sessions' && (
        <section className="card-block">
          <h3 className="card-title">已登录设备</h3>
          {sessions.length === 0 ? (
            <p className="empty-copy">暂无登录会话。</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>设备</th>
                    <th>IP</th>
                    <th>登录时间</th>
                    <th>过期时间</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((session) => (
                    <tr key={session.id}>
                      <td>
                        {session.current ? (
                          <span className="session-current">本机</span>
                        ) : (
                          session.userAgent?.slice(0, 60) || '未知设备'
                        )}
                      </td>
                      <td>{session.ipAddress ?? '—'}</td>
                      <td>{formatDateTime(session.createdAt)}</td>
                      <td>{formatDateTime(session.expiresAt)}</td>
                      <td>
                        {!session.current && (
                          <button
                            className="btn ghost small"
                            type="button"
                            disabled={revoking === session.id}
                            onClick={() => handleRevoke(session)}
                          >
                            {revoking === session.id ? '退出中…' : '退出'}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {section === 'admin' && user.isSuperuser && (
        <section className="card-block">
          <h3 className="card-title">平台用户管理</h3>
          <p className="empty-copy">
            作为平台管理员，你可以查看全部平台账号，并重置某用户密码或调整其管理员标记。
            重置密码会立即使该用户的所有登录会话失效。
          </p>
          {adminError && <div className="form-error">{adminError}</div>}
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>用户名</th>
                  <th>显示名称</th>
                  <th>角色</th>
                  <th>创建时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {adminUsers.map((u) => (
                  <AdminUserRow key={u.id} user={u} onUpdate={handleAdminUpdate} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}

function AdminUserRow({
  user,
  onUpdate,
}: {
  user: User
  onUpdate: (user: User, patch: { password?: string; isSuperuser?: boolean }) => Promise<void>
}) {
  const [resetPw, setResetPw] = useState('')
  const [acting, setActing] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function handleReset() {
    if (!resetPw) return
    setErr(null)
    setActing(true)
    try {
      await onUpdate(user, { password: resetPw })
      setResetPw('')
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '重置失败')
    } finally {
      setActing(false)
    }
  }

  return (
    <tr>
      <td>{user.username}</td>
      <td>{user.displayName ?? '—'}</td>
      <td>{user.isSuperuser ? '管理员' : '普通用户'}</td>
      <td>{formatDateTime(user.createdAt)}</td>
      <td>
        <div className="admin-user-row">
          <input
            type="password"
            placeholder="新密码"
            value={resetPw}
            onChange={(event) => setResetPw(event.target.value)}
            maxLength={128}
            aria-label={`为 ${user.username} 重置密码`}
            style={{ width: 120 }}
          />
          <button
            className="btn small"
            type="button"
            disabled={acting || !resetPw}
            onClick={handleReset}
          >
            {acting ? '重置中…' : '重置密码'}
          </button>
          {user.username !== 'admin' && (
            <button
              className="btn ghost small"
              type="button"
              disabled={acting}
              onClick={() => onUpdate(user, { isSuperuser: !user.isSuperuser })}
            >
              {user.isSuperuser ? '取消管理员' : '设为管理员'}
            </button>
          )}
          {err && <span className="form-error">{err}</span>}
        </div>
      </td>
    </tr>
  )
}
