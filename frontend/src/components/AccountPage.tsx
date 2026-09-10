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
  const [adminNotice, setAdminNotice] = useState<string | null>(null)
  const [newUsername, setNewUsername] = useState('')
  const [newDisplayName, setNewDisplayName] = useState('')
  const [newEmail, setNewEmail] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newPasswordConfirm, setNewPasswordConfirm] = useState('')
  const [newIsSuperuser, setNewIsSuperuser] = useState(false)
  const [creatingUser, setCreatingUser] = useState(false)

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
      await loadUsers()
      return true
    } catch (err) {
      setAdminError(err instanceof ApiError ? err.message : '操作失败')
      return false
    }
  }

  async function handleCreateUser(event: React.FormEvent) {
    event.preventDefault()
    setAdminError(null)
    setAdminNotice(null)
    if (!newUsername.trim() || !newPassword) {
      setAdminError('请填写用户名和初始密码')
      return
    }
    if (newPassword.length < 8) {
      setAdminError('初始密码至少需要 8 个字符')
      return
    }
    if (newPassword !== newPasswordConfirm) {
      setAdminError('两次输入的初始密码不一致')
      return
    }

    setCreatingUser(true)
    try {
      const created = await api.authAdminCreateUser({
        username: newUsername.trim(),
        password: newPassword,
        displayName: newDisplayName.trim() || undefined,
        email: newEmail.trim() || undefined,
        isSuperuser: newIsSuperuser,
      })
      setNewUsername('')
      setNewDisplayName('')
      setNewEmail('')
      setNewPassword('')
      setNewPasswordConfirm('')
      setNewIsSuperuser(false)
      setAdminNotice(`已创建平台用户“${created.username}”`)
      await loadUsers()
    } catch (err) {
      setAdminError(err instanceof ApiError ? err.message : '创建用户失败，请重试')
    } finally {
      setCreatingUser(false)
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
          <div className="admin-section-head">
            <div>
              <p className="section-kicker">ADMIN / USERS</p>
              <h3 className="card-title">创建平台用户</h3>
            </div>
            <span className="badge ember">{adminUsers.length} USERS</span>
          </div>
          <p className="empty-copy">
            登录页不开放公开注册。新成员由管理员在这里创建，初始密码请通过可信渠道单独告知。
          </p>
          <form className="admin-create-form" onSubmit={handleCreateUser}>
            <div className="field">
              <label htmlFor="admin-new-username">用户名</label>
              <input
                id="admin-new-username"
                value={newUsername}
                onChange={(event) => setNewUsername(event.target.value)}
                autoComplete="off"
                maxLength={60}
                placeholder="用于登录"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="admin-new-display">显示名称 <span>可选</span></label>
              <input
                id="admin-new-display"
                value={newDisplayName}
                onChange={(event) => setNewDisplayName(event.target.value)}
                autoComplete="off"
                maxLength={60}
                placeholder="用于控制台内显示"
              />
            </div>
            <div className="field">
              <label htmlFor="admin-new-email">邮箱 <span>可选</span></label>
              <input
                id="admin-new-email"
                type="email"
                value={newEmail}
                onChange={(event) => setNewEmail(event.target.value)}
                autoComplete="off"
                maxLength={255}
                placeholder="仅作账号资料"
              />
            </div>
            <div className="field">
              <label htmlFor="admin-new-password">初始密码</label>
              <input
                id="admin-new-password"
                type="password"
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                autoComplete="new-password"
                minLength={8}
                maxLength={128}
                placeholder="至少 8 个字符"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="admin-new-password-confirm">确认初始密码</label>
              <input
                id="admin-new-password-confirm"
                type="password"
                value={newPasswordConfirm}
                onChange={(event) => setNewPasswordConfirm(event.target.value)}
                autoComplete="new-password"
                minLength={8}
                maxLength={128}
                placeholder="再次输入初始密码"
                required
              />
            </div>
            <div className="admin-create-actions">
              <label className="admin-role-check">
                <input
                  type="checkbox"
                  checked={newIsSuperuser}
                  onChange={(event) => setNewIsSuperuser(event.target.checked)}
                />
                同时授予管理员权限
              </label>
              <button className="btn primary" type="submit" disabled={creatingUser}>
                {creatingUser ? '创建中…' : '创建用户'}
              </button>
            </div>
          </form>
          {adminError && <div className="form-error">{adminError}</div>}
          {adminNotice && <div className="form-notice">{adminNotice}</div>}

          <div className="admin-list-head">
            <h3 className="card-title">现有平台用户</h3>
            <p>重置密码会立即使该用户的所有登录会话失效。</p>
          </div>
          <div className="table-wrap admin-users-wrap">
            <table className="table admin-users-table">
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
                  <AdminUserRow
                    key={u.id}
                    user={u}
                    currentUserId={user.id}
                    onUpdate={handleAdminUpdate}
                  />
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
  currentUserId,
  onUpdate,
}: {
  user: User
  currentUserId: string
  onUpdate: (user: User, patch: { password?: string; isSuperuser?: boolean }) => Promise<boolean>
}) {
  const [resetPw, setResetPw] = useState('')
  const [acting, setActing] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const isCurrent = user.id === currentUserId

  async function handleReset() {
    if (!resetPw) return
    setErr(null)
    setActing(true)
    try {
      const updated = await onUpdate(user, { password: resetPw })
      if (updated) setResetPw('')
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '重置失败')
    } finally {
      setActing(false)
    }
  }

  return (
    <tr>
      <td data-label="用户名">{user.username}</td>
      <td data-label="显示名称">{user.displayName ?? '—'}</td>
      <td data-label="角色">{user.isSuperuser ? '管理员' : '普通用户'}{isCurrent ? ' · 当前账号' : ''}</td>
      <td data-label="创建时间">{formatDateTime(user.createdAt)}</td>
      <td data-label="操作">
        {isCurrent ? (
          <span className="field-hint">请在“修改密码”中操作</span>
        ) : (
          <form
            className="admin-user-row"
            onSubmit={(event) => {
              event.preventDefault()
              void handleReset()
            }}
          >
            <input
              type="password"
              placeholder="至少 8 个字符"
              value={resetPw}
              onChange={(event) => setResetPw(event.target.value)}
              autoComplete="new-password"
              minLength={8}
              maxLength={128}
              aria-label={`为 ${user.username} 重置密码`}
            />
            <button
              className="btn small"
              type="submit"
              disabled={acting || resetPw.length < 8}
            >
              {acting ? '重置中…' : '重置密码'}
            </button>
            <button
              className="btn ghost small"
              type="button"
              disabled={acting}
              onClick={() => void onUpdate(user, { isSuperuser: !user.isSuperuser })}
            >
              {user.isSuperuser ? '取消管理员' : '设为管理员'}
            </button>
            {err && <span className="form-error">{err}</span>}
          </form>
        )}
      </td>
    </tr>
  )
}
