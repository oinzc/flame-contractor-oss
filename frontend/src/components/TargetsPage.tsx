import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type { Account, ContractTarget, MessageTemplate } from '../types'
import { formatDateTime } from '../lib/format'

interface Props {
  accounts: Account[]
  targets: ContractTarget[]
  templates: MessageTemplate[]
  onChanged: () => void
}

export default function TargetsPage({ accounts, targets, templates, onChanged }: Props) {
  const [selectedAccountId, setSelectedAccountId] = useState(accounts[0]?.id ?? '')
  const [nickname, setNickname] = useState('')
  const [platformUserId, setPlatformUserId] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [streakDays, setStreakDays] = useState('0')
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!accounts.some((account) => account.id === selectedAccountId)) {
      setSelectedAccountId(accounts[0]?.id ?? '')
    }
  }, [accounts, selectedAccountId])

  const selectedTargets = targets.filter((target) => target.accountId === selectedAccountId)

  function templateName(id: string | null): string {
    if (!id) return '未绑定'
    return templates.find((template) => template.id === id)?.name ?? id.slice(0, 8)
  }

  async function handleDelete(target: ContractTarget) {
    if (!window.confirm(`确定删除好友「${target.nicknameSnapshot}」？`)) {
      return
    }
    setActionError(null)
    try {
      await api.deleteTarget(target.id)
      onChanged()
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '删除失败，请重试')
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await api.createTarget({
        accountId: selectedAccountId,
        nicknameSnapshot: nickname.trim(),
        platformUserId: platformUserId.trim() || undefined,
        templateId: templateId || undefined,
        streakDays: Math.max(0, Number.parseInt(streakDays || '0', 10) || 0),
      })
      setNickname('')
      setPlatformUserId('')
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '添加失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="main-col">
      <div className="page-head">
        <div>
          <h1>好友管理</h1>
          <p className="sub">昵称必须与抖音会话列表里的显示完全一致，这是防错发的严格匹配。</p>
        </div>
      </div>

      {accounts.length === 0 ? (
        <div className="empty">
          <div className="empty-title">先创建账号</div>
          <p className="empty-copy">好友属于某个账号，请先到「账号」页新建并登录账号。</p>
        </div>
      ) : (
        <>
          <div className="field" style={{ maxWidth: 320 }}>
            <label htmlFor="target-account">所属账号</label>
            <select
              id="target-account"
              value={selectedAccountId}
              onChange={(event) => setSelectedAccountId(event.target.value)}
            >
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.displayName}
                </option>
              ))}
            </select>
          </div>

          <section className="card">
            <div className="card-head">
              <h2>好友列表（{selectedTargets.length}）</h2>
            </div>
            {actionError && <div className="form-error" style={{ marginBottom: 8 }}>{actionError}</div>}
            {selectedTargets.length === 0 ? (
              <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
                这个账号还没有好友，用下面的表单添加第一位。
              </p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>昵称</th>
                    <th>抖音号</th>
                    <th>模板</th>
                    <th>火花天数</th>
                    <th>上次确认发送</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedTargets.map((target) => (
                    <tr key={target.id}>
                      <td>{target.nicknameSnapshot}</td>
                      <td className="mono">{target.platformUserId ?? '—'}</td>
                      <td>{templateName(target.templateId)}</td>
                      <td className="mono">{target.streakDays} 天</td>
                      <td className="mono">{formatDateTime(target.lastConfirmedAt)}</td>
                      <td>
                        <button
                          className="btn ghost small"
                          type="button"
                          onClick={() => handleDelete(target)}
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="card">
            <div className="card-head">
              <h2>添加好友</h2>
            </div>
            <form className="form-grid" onSubmit={handleSubmit}>
              <div className="field-row">
                <div className="field">
                  <label htmlFor="target-nickname">昵称（必填）</label>
                  <input
                    id="target-nickname"
                    value={nickname}
                    onChange={(event) => setNickname(event.target.value)}
                    placeholder="与抖音显示完全一致"
                    maxLength={80}
                    required
                  />
                </div>
                <div className="field">
                  <label htmlFor="target-uid">抖音号（选填）</label>
                  <input
                    id="target-uid"
                    value={platformUserId}
                    onChange={(event) => setPlatformUserId(event.target.value)}
                    placeholder="获得后优先用于匹配"
                    maxLength={128}
                  />
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label htmlFor="target-template">绑定模板（选填）</label>
                  <select
                    id="target-template"
                    value={templateId}
                    onChange={(event) => setTemplateId(event.target.value)}
                  >
                    <option value="">不绑定</option>
                    {templates.map((template) => (
                      <option key={template.id} value={template.id}>
                        {template.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="target-streak">火花天数</label>
                  <input
                    id="target-streak"
                    type="number"
                    min={0}
                    value={streakDays}
                    onChange={(event) => setStreakDays(event.target.value)}
                  />
                </div>
              </div>
              {error && <div className="form-error">{error}</div>}
              <div className="form-actions">
                <button className="btn primary small" type="submit" disabled={submitting}>
                  {submitting ? '添加中…' : '添加好友'}
                </button>
              </div>
            </form>
          </section>
        </>
      )}
    </div>
  )
}
