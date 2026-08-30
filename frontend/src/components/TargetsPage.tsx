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
  const [showPaused, setShowPaused] = useState(false)
  const [nickname, setNickname] = useState('')
  const [platformUserId, setPlatformUserId] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [streakDays, setStreakDays] = useState('0')
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [showBatch, setShowBatch] = useState(false)
  const [batchText, setBatchText] = useState('')
  const [batchTemplateId, setBatchTemplateId] = useState('')
  const [batchResult, setBatchResult] = useState<{
    created: string[]
    duplicates: string[]
    failed: string[]
  } | null>(null)
  const [batchSubmitting, setBatchSubmitting] = useState(false)
  const [scanLoading, setScanLoading] = useState(false)
  const [scanError, setScanError] = useState<string | null>(null)
  const [scanEntries, setScanEntries] = useState<{ title: string; preview: string }[] | null>(null)
  const [selectedTitles, setSelectedTitles] = useState<Set<string>>(new Set())
  const [scanQuery, setScanQuery] = useState('')
  const [showGroups, setShowGroups] = useState(false)

  useEffect(() => {
    if (!accounts.some((account) => account.id === selectedAccountId)) {
      setSelectedAccountId(accounts[0]?.id ?? '')
    }
  }, [accounts, selectedAccountId])

  const accountTargets = targets.filter((target) => target.accountId === selectedAccountId)
  const pausedCount = accountTargets.filter((target) => !target.enabled).length
  // 默认只显示启用中的好友；暂停项是保留的历史记录，按需展开查看
  const selectedTargets = showPaused ? accountTargets : accountTargets.filter((t) => t.enabled)
  const existingNicknames = new Set(accountTargets.map((target) => target.nicknameSnapshot))
  const selectedAccount = accounts.find((account) => account.id === selectedAccountId) ?? null

  const scanGroupCount =
    scanEntries?.filter((entry) => looksLikeGroup(entry.title, entry.preview)).length ?? 0
  const scanExistCount =
    scanEntries?.filter((entry) => existingNicknames.has(entry.title)).length ?? 0
  const scanFiltered = (scanEntries ?? []).filter((entry) => {
    if (!showGroups && looksLikeGroup(entry.title, entry.preview)) return false
    const q = scanQuery.trim().toLowerCase()
    if (q && !entry.title.toLowerCase().includes(q)) return false
    return true
  })

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

  async function handleAdjustStreak(target: ContractTarget) {
    const input = window.prompt(
      `校准好友「${target.nicknameSnapshot}」的火花天数（本地记录，非抖音实时值）：`,
      String(target.streakDays),
    )
    if (input === null) return
    const value = Math.max(0, Number.parseInt(input, 10) || 0)
    setActionError(null)
    try {
      await api.patchTarget(target.id, { streakDays: value })
      onChanged()
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '校准失败，请重试')
    }
  }

  async function handleToggleEnabled(target: ContractTarget) {
    const verb = target.enabled ? '暂停' : '启用'
    setActionError(null)
    try {
      await api.patchTarget(target.id, { enabled: !target.enabled })
      onChanged()
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : `${verb}失败，请重试`)
    }
  }

  // 群聊识别（真机侦察结论）：标题带「已满/人数」括号；最后一条消息带「成员名：」前缀
  // （成员名可能被截断成冒号开头）；或为「xx加入了群聊」系统消息
  function looksLikeGroup(title: string, preview: string): boolean {
    return (
      /（已满）|\(\d+\)|（\d+人?）/.test(title) ||
      /加入了群聊/.test(preview) ||
      /^[^：]{0,20}：\S/.test(preview)
    )
  }

  async function handleScan() {
    setScanLoading(true)
    setScanError(null)
    setScanEntries(null)
    setSelectedTitles(new Set())
    setScanQuery('')
    try {
      const result = await api.scanChats(selectedAccountId)
      if (result.overall !== 'ok') {
        setScanError(result.detail ?? '扫描失败，请重试')
      } else {
        setScanEntries(result.entries)
      }
    } catch (err) {
      setScanError(err instanceof ApiError ? err.message : '扫描失败，请重试')
    } finally {
      setScanLoading(false)
    }
  }

  async function handleImportSelected() {
    if (selectedTitles.size === 0) return
    setBatchSubmitting(true)
    setActionError(null)
    try {
      const result = await api.createTargetsBatch({
        accountId: selectedAccountId,
        nicknames: [...selectedTitles],
      })
      setBatchResult(result)
      setScanEntries(null)
      setSelectedTitles(new Set())
      onChanged()
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '导入失败，请重试')
    } finally {
      setBatchSubmitting(false)
    }
  }

  function handleRebindTemplate(target: ContractTarget, templateId: string) {
    setActionError(null)
    api
      .patchTarget(target.id, { templateId: templateId || null })
      .then(onChanged)
      .catch((err) =>
        setActionError(err instanceof ApiError ? err.message : '换绑失败，请重试'),
      )
  }

  async function handleBatchSubmit(event: React.FormEvent) {
    event.preventDefault()
    setBatchSubmitting(true)
    setActionError(null)
    try {
      const result = await api.createTargetsBatch({
        accountId: selectedAccountId,
        templateId: batchTemplateId || undefined,
        nicknames: batchText.split('\n'),
      })
      setBatchResult(result)
      setBatchText('')
      onChanged()
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '批量添加失败，请重试')
    } finally {
      setBatchSubmitting(false)
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
              {pausedCount > 0 && (
                <label
                  style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, cursor: 'pointer' }}
                >
                  <input
                    type="checkbox"
                    checked={showPaused}
                    onChange={(event) => setShowPaused(event.target.checked)}
                  />
                  显示已暂停（{pausedCount}）
                </label>
              )}
            </div>
            {actionError && <div className="form-error" style={{ marginBottom: 8 }}>{actionError}</div>}
            {selectedTargets.length === 0 ? (
              <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
                {!showPaused && pausedCount > 0
                  ? `好友都处于暂停状态（${pausedCount} 个），勾选上方「显示已暂停」可查看。`
                  : '这个账号还没有好友，用下面的表单添加第一位。'}
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
                      <td>
                        {target.nicknameSnapshot}
                        {!target.enabled && (
                          <span className="muted" style={{ marginLeft: 6, fontSize: 11.5 }}>
                            （已暂停）
                          </span>
                        )}
                      </td>
                      <td className="mono">{target.platformUserId ?? '—'}</td>
                      <td>
                        <select
                          value={target.templateId ?? ''}
                          onChange={(event) => handleRebindTemplate(target, event.target.value)}
                          style={{ maxWidth: 140, fontSize: 12.5 }}
                        >
                          <option value="">不绑定</option>
                          {templates.map((template) => (
                            <option key={template.id} value={template.id}>
                              {template.name}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="mono">
                        {target.streakDays} 天
                        <button
                          className="btn ghost small"
                          type="button"
                          style={{ marginLeft: 6 }}
                          onClick={() => handleAdjustStreak(target)}
                        >
                          校准
                        </button>
                      </td>
                      <td className="mono">{formatDateTime(target.lastConfirmedAt)}</td>
                      <td>
                        <button
                          className="btn ghost small"
                          type="button"
                          onClick={() => handleToggleEnabled(target)}
                        >
                          {target.enabled ? '暂停' : '启用'}
                        </button>
                        <button
                          className="btn ghost small"
                          type="button"
                          style={{ marginLeft: 6 }}
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
              <h2>批量添加好友</h2>
              <button
                className="btn ghost small"
                type="button"
                onClick={() => setShowBatch((previous) => !previous)}
              >
                {showBatch ? '收起' : '展开'}
              </button>
            </div>
            {showBatch && (
              <form className="form-grid" onSubmit={handleBatchSubmit}>
                <div className="field">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <button
                      className="btn small"
                      type="button"
                      disabled={scanLoading || selectedAccount?.status !== 'healthy'}
                      title={
                        selectedAccount?.status !== 'healthy'
                          ? '账号需先扫码登录才能扫描会话列表'
                          : '打开浏览器滚动加载整个会话列表（只读，约需 1–2 分钟）'
                      }
                      onClick={() => void handleScan()}
                    >
                      {scanLoading ? '扫描中…（约 1–2 分钟）' : '🔍 从抖音会话列表扫描'}
                    </button>
                    {selectedAccount?.status !== 'healthy' && (
                      <span className="muted" style={{ fontSize: 11.5 }}>
                        账号需先扫码登录才能扫描
                      </span>
                    )}
                  </div>
                  {scanError && <div className="form-error" style={{ marginTop: 8 }}>{scanError}</div>}
                  {scanEntries !== null && (
                    <div style={{ marginTop: 10, display: 'grid', gap: 8 }}>
                      <div className="pick-toolbar">
                        <input
                          className="pick-search"
                          type="text"
                          value={scanQuery}
                          onChange={(event) => setScanQuery(event.target.value)}
                          placeholder="搜索会话名…"
                        />
                        <label className="pick-switch">
                          <input
                            type="checkbox"
                            checked={showGroups}
                            onChange={(event) => setShowGroups(event.target.checked)}
                          />
                          显示疑似群聊（{scanGroupCount}）
                        </label>
                      </div>
                      <div className="pick-list">
                        {scanFiltered.length === 0 ? (
                          <div className="muted" style={{ padding: '14px 12px', fontSize: 12 }}>
                            {scanQuery.trim()
                              ? '没有匹配的会话，换个关键词试试。'
                              : '没有可显示的会话。'}
                          </div>
                        ) : (
                          scanFiltered.map((entry) => {
                            const exists = existingNicknames.has(entry.title)
                            const group = looksLikeGroup(entry.title, entry.preview)
                            const checked = selectedTitles.has(entry.title)
                            return (
                              <label
                                key={entry.title}
                                className={[
                                  'pick-row',
                                  checked && !exists ? 'selected' : '',
                                  exists ? 'occupied' : '',
                                ]
                                  .filter(Boolean)
                                  .join(' ')}
                                title={entry.preview || undefined}
                              >
                                <input
                                  type="checkbox"
                                  disabled={exists}
                                  checked={exists || checked}
                                  onChange={() =>
                                    setSelectedTitles((previous) => {
                                      const next = new Set(previous)
                                      if (next.has(entry.title)) next.delete(entry.title)
                                      else next.add(entry.title)
                                      return next
                                    })
                                  }
                                />
                                <span className="pick-name">{entry.title}</span>
                                <span className="pick-meta">
                                  {group && <span className="badge gold">疑似群聊</span>}
                                  {exists && <span className="badge">已在列表</span>}
                                </span>
                              </label>
                            )
                          })
                        )}
                      </div>
                      <div className="pick-foot">
                        <span className="pick-stat">
                          扫描 {scanEntries.length} · 群聊 {scanGroupCount}（已隐藏）· 已在列表 {scanExistCount}
                        </span>
                        <button
                          className="btn primary small"
                          type="button"
                          disabled={batchSubmitting || selectedTitles.size === 0}
                          onClick={() => void handleImportSelected()}
                        >
                          {batchSubmitting
                            ? '导入中…'
                            : `导入所选（${selectedTitles.size}）`}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
                <div className="field">
                  <label htmlFor="target-batch-text">或粘贴昵称列表（每行一个，与抖音显示完全一致）</label>
                  <textarea
                    id="target-batch-text"
                    value={batchText}
                    onChange={(event) => setBatchText(event.target.value)}
                    placeholder={'好友A\n好友B\n好友C'}
                    rows={5}
                  />
                  <p className="muted" style={{ fontSize: 11.5, margin: '6px 0 0' }}>
                    重复昵称自动跳过，不会中断整批；最多一次 200 行。
                  </p>
                </div>
                <div className="field" style={{ maxWidth: 320 }}>
                  <label htmlFor="target-batch-template">统一绑定模板（选填）</label>
                  <select
                    id="target-batch-template"
                    value={batchTemplateId}
                    onChange={(event) => setBatchTemplateId(event.target.value)}
                  >
                    <option value="">不绑定</option>
                    {templates.map((template) => (
                      <option key={template.id} value={template.id}>
                        {template.name}
                      </option>
                    ))}
                  </select>
                </div>
                {batchResult && (
                  <div className="muted" style={{ fontSize: 12.5, lineHeight: 1.7 }}>
                    {batchResult.created.length > 0 && (
                      <div>✅ 新增 {batchResult.created.length} 个：{batchResult.created.join('、')}</div>
                    )}
                    {batchResult.duplicates.length > 0 && (
                      <div>↩️ 跳过重复 {batchResult.duplicates.length} 个：{batchResult.duplicates.join('、')}</div>
                    )}
                    {batchResult.failed.length > 0 && (
                      <div>⚠️ 失败 {batchResult.failed.length} 个：{batchResult.failed.join('、')}</div>
                    )}
                  </div>
                )}
                <div className="form-actions">
                  <button className="btn primary small" type="submit" disabled={batchSubmitting}>
                    {batchSubmitting ? '添加中…' : '批量添加'}
                  </button>
                </div>
              </form>
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
