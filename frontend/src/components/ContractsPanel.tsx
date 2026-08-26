import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type { Account, ContractTarget, MessageTemplate } from '../types'

interface Props {
  accounts: Account[]
  targets: ContractTarget[]
  templates: MessageTemplate[]
  onChanged: () => void
}

export default function ContractsPanel({ accounts, targets, templates, onChanged }: Props) {
  const [selectedAccountId, setSelectedAccountId] = useState(accounts[0]?.id ?? '')
  const [nickname, setNickname] = useState('')
  const [platformUserId, setPlatformUserId] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [streakDays, setStreakDays] = useState('0')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!accounts.some((account) => account.id === selectedAccountId)) {
      setSelectedAccountId(accounts[0]?.id ?? '')
    }
  }, [accounts, selectedAccountId])

  if (accounts.length === 0) {
    return <div className="muted" style={{ fontSize: 12 }}>先在上方新建账号，再为它绑定契约对象。</div>
  }

  const selectedTargets = targets.filter((target) => target.accountId === selectedAccountId)

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

  function templateName(id: string | null): string {
    if (!id) return '—'
    return templates.find((template) => template.id === id)?.name ?? id.slice(0, 8)
  }

  return (
    <section className="panel">
      <h2 className="panel-title">契约与好友</h2>
      <p className="panel-sub">
        昵称必须与抖音会话列表中的显示完全一致；这是防错发的严格匹配，不是模糊搜索。
      </p>

      <div className="field" style={{ marginBottom: 12 }}>
        <label htmlFor="contract-account">所属账号</label>
        <select
          id="contract-account"
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

      {selectedTargets.length === 0 ? (
        <div className="empty" style={{ padding: '14px 10px' }}>
          <div className="empty-copy" style={{ margin: 0 }}>
            该账号还没有契约对象。在下方表单添加第一位好友后，Dry Run 才可执行。
          </div>
        </div>
      ) : (
        <div className="row-list" style={{ marginBottom: 14 }}>
          {selectedTargets.map((target) => (
            <div className="row-item" key={target.id}>
              <div className="row-main">
                <div className="row-title">
                  {target.nicknameSnapshot}
                  {target.platformUserId && (
                    <span className="stage-chip">ID 已绑定</span>
                  )}
                </div>
                <div className="row-sub">
                  模板 {templateName(target.templateId)} · 火花 {target.streakDays} 天
                </div>
              </div>
              <span className="badge">{target.enabled ? '启用' : '暂停'}</span>
            </div>
          ))}
        </div>
      )}

      <form className="form-grid" onSubmit={handleSubmit}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div className="field">
            <label htmlFor="target-nickname">好友昵称（必填）</label>
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
              placeholder="获得后优先用于身份匹配"
              maxLength={128}
            />
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 120px', gap: 12 }}>
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
            {submitting ? '绑定中…' : '绑定契约对象'}
          </button>
        </div>
      </form>
    </section>
  )
}
