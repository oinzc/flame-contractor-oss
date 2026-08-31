import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type {
  Account,
  ContractTarget,
  MessageTemplate,
  ScanChatEntry,
  ScanTaskState,
  TargetBatchResult,
} from '../types'
import { formatDateTime } from '../lib/format'
import SparkBadge from './SparkBadge'
import { ScanProgressBar, scanProgressCount, scanProgressPercent } from './ScanProgress'

interface Props {
  accounts: Account[]
  targets: ContractTarget[]
  templates: MessageTemplate[]
  onChanged: () => void
  scanTask: ScanTaskState | null
  scanStarting: boolean
  onStartScan: (accountId: string) => Promise<void>
}

export default function TargetsPage({
  accounts,
  targets,
  templates,
  onChanged,
  scanTask,
  scanStarting,
  onStartScan,
}: Props) {
  const [selectedAccountId, setSelectedAccountId] = useState(accounts[0]?.id ?? '')
  const [showPaused, setShowPaused] = useState(false)
  const [nickname, setNickname] = useState('')
  const [platformUserId, setPlatformUserId] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [streakDays, setStreakDays] = useState('0')
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [batchTemplateId, setBatchTemplateId] = useState('')
  const [batchResult, setBatchResult] = useState<TargetBatchResult | null>(null)
  const [batchSubmitting, setBatchSubmitting] = useState(false)
  const [scanError, setScanError] = useState<string | null>(null)
  const [scanEntries, setScanEntries] = useState<ScanChatEntry[] | null>(null)
  const [selectedEntries, setSelectedEntries] = useState<Map<string, ScanChatEntry>>(new Map())
  const [scanQuery, setScanQuery] = useState('')
  const [showNoStreak, setShowNoStreak] = useState(false)

  useEffect(() => {
    if (!accounts.some((account) => account.id === selectedAccountId)) {
      setSelectedAccountId(accounts[0]?.id ?? '')
    }
  }, [accounts, selectedAccountId])

  useEffect(() => {
    if (!scanTask || scanTask.accountId !== selectedAccountId) {
      setScanEntries(null)
      setSelectedEntries(new Map())
      setScanQuery('')
      setBatchResult(null)
      return
    }
    if (scanTask.status === 'running') {
      setScanEntries(null)
      setSelectedEntries(new Map())
      setScanQuery('')
      setBatchResult(null)
      return
    }
    if (scanTask.status === 'completed') {
      setScanEntries(scanTask.entries)
      setBatchResult(scanTask.batchResult)
    } else {
      setScanEntries(null)
    }
  }, [scanTask?.taskId, scanTask?.status, scanTask?.batchResult, selectedAccountId])

  const accountTargets = targets.filter((target) => target.accountId === selectedAccountId)
  const pausedCount = accountTargets.filter((target) => !target.enabled).length
  // 默认只显示启用中的好友；暂停项是保留的历史记录，按需展开查看
  const selectedTargets = showPaused ? accountTargets : accountTargets.filter((t) => t.enabled)
  const existingNicknames = new Set(accountTargets.map((target) => target.nicknameSnapshot))
  const selectedAccount = accounts.find((account) => account.id === selectedAccountId) ?? null
  const selectedScanTask =
    scanTask?.accountId === selectedAccountId ? scanTask : null
  const scanLoading = scanStarting || scanTask?.status === 'running'
  const displayedScanError =
    scanError ??
    (selectedScanTask?.status === 'failed'
      ? selectedScanTask.detail ?? '扫描失败，请重新发起'
      : selectedScanTask?.syncError ?? null)

  // 抖音官方火花天数就是续火对象的判定信号；火花将熄的（消息提示）也算
  const scanStreakCount =
    scanEntries?.filter((entry) => isStreakTarget(entry)).length ?? 0
  const scanNoStreakCount = (scanEntries?.length ?? 0) - scanStreakCount
  const scanExistCount =
    scanEntries?.filter((entry) => existingNicknames.has(entry.title)).length ?? 0
  const scanFiltered = (scanEntries ?? []).filter((entry) => {
    if (!showNoStreak && !isStreakTarget(entry)) return false
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

  // 火花将熄/重燃中的会话：火花徽标是文字标签（「重燃中 1/3」「5 天后消失」）；
  // 已断火的只剩系统消息文案（「N 天后火花将消失」）。三者都视为续火对象
  function hasSparkHint(entry: {
    streakLabel?: string | null
    preview: string
  }): boolean {
    return Boolean(entry.streakLabel) || /火花/.test(entry.preview)
  }

  function isStreakTarget(entry: {
    streakDays: number | null
    streakLabel?: string | null
    preview: string
  }): boolean {
    return entry.streakDays != null || hasSparkHint(entry)
  }

  async function handleScan() {
    setScanError(null)
    setActionError(null)
    setBatchResult(null)
    setScanEntries(null)
    setSelectedEntries(new Map())
    setScanQuery('')
    try {
      await onStartScan(selectedAccountId)
    } catch (err) {
      setScanError(err instanceof ApiError ? err.message : '扫描失败，请重试')
    }
  }

  async function handleImportSelected() {
    if (selectedEntries.size === 0) return
    setBatchSubmitting(true)
    setActionError(null)
    try {
      const result = await api.createTargetsBatch({
        accountId: selectedAccountId,
        items: [...selectedEntries.values()].map((entry) => ({
          nickname: entry.title,
          platformUserId: entry.platformUserId,
          streakDays: entry.streakDays ?? undefined,
          // null 也要传递：重新扫描到普通状态时应清除旧的特殊徽标。
          sparkKind: entry.streakKind,
          sparkLabel: entry.streakLabel,
        })),
      })
      setBatchResult(result)
      setScanEntries(null)
      setSelectedEntries(new Map())
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
            <div className="target-list-head">
              <div className="target-list-title">
                <h2>好友列表（{selectedTargets.length}）</h2>
              </div>
              <div className="target-list-actions">
                <button
                  className="btn small"
                  type="button"
                  disabled={scanLoading || selectedAccount?.status !== 'healthy'}
                  title={
                    selectedAccount?.status !== 'healthy'
                      ? '账号需先扫码登录才能扫描会话列表'
                      : '后台滚动加载整个会话列表；切换页面不会中断，完成后自动刷新已在列表好友的官方展示状态（约需 1–2 分钟）'
                  }
                  onClick={() => void handleScan()}
                >
                  {scanLoading
                    ? scanStarting
                      ? '正在启动扫描…'
                      : selectedScanTask?.status === 'running'
                        ? `扫描中… ${scanProgressPercent(selectedScanTask) === null ? '读取中' : `${scanProgressPercent(selectedScanTask)}%`}`
                        : '其他账号扫描中…'
                    : '扫描会话列表'}
                </button>
                {pausedCount > 0 && (
                  <label className={`target-paused-toggle${showPaused ? ' is-active' : ''}`}>
                    <input
                      type="checkbox"
                      checked={showPaused}
                      onChange={(event) => setShowPaused(event.target.checked)}
                    />
                    <span className="target-paused-label">显示已暂停</span>
                    <span className="target-paused-count">（{pausedCount}）</span>
                  </label>
                )}
              </div>
            </div>
            {actionError && <div className="form-error" style={{ marginBottom: 8 }}>{actionError}</div>}
            {displayedScanError && <div className="form-error" style={{ marginBottom: 8 }}>{displayedScanError}</div>}
            {selectedTargets.length === 0 ? (
              <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
                {!showPaused && pausedCount > 0
                  ? `好友都处于暂停状态（${pausedCount} 个），勾选上方「显示已暂停」可查看。`
                  : '这个账号还没有好友，用下面的表单添加第一位。'}
              </p>
            ) : (
              <div className="target-table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>昵称</th>
                      <th>抖音号</th>
                      <th>模板</th>
                      <th>火花状态</th>
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
                        <td>
                          <div className="target-spark-cell">
                            <SparkBadge
                              days={target.streakDays}
                              kind={target.sparkKind}
                              label={target.sparkLabel}
                              scannedAt={target.sparkScannedAt}
                            />
                            <button
                              className="btn ghost small"
                              type="button"
                              onClick={() => handleAdjustStreak(target)}
                            >
                              校准
                            </button>
                            {target.sparkLabel && target.sparkScannedAt && (
                              <span className="spark-scan-note">
                                抖音扫描于 {formatDateTime(target.sparkScannedAt)}
                              </span>
                            )}
                          </div>
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
              </div>
            )}
          </section>

          <section className="card">
            <div className="card-head">
              <h2>会话扫描与批量添加</h2>
            </div>
            <p className="scan-helper">
              扫描完成后，已在列表的好友会自动刷新官方火花状态；新好友仍需在下方勾选后导入。
            </p>
            {selectedScanTask && (
              <section
                className={`scan-inline-progress${selectedScanTask.status === 'failed' ? ' is-error' : ''}${selectedScanTask.status === 'completed' ? ' is-complete' : ''}`}
                aria-live="polite"
              >
                <div className="scan-inline-head">
                  <div>
                    <span className="scan-section-kicker">SESSION SCAN</span>
                    <strong>
                      {selectedScanTask.status === 'running'
                        ? '正在读取会话列表'
                        : selectedScanTask.status === 'completed'
                          ? '会话列表扫描完成'
                          : '会话列表扫描未完成'}
                    </strong>
                    <p>
                      {selectedScanTask.syncStatus === 'pending'
                        ? '正在把已存在好友的官方状态写回本地…'
                        : selectedScanTask.pollError ?? selectedScanTask.syncError ?? selectedScanTask.stage}
                    </p>
                  </div>
                  <b>
                    {scanProgressPercent(selectedScanTask) === null
                      ? '读取中'
                      : `${scanProgressPercent(selectedScanTask)}%`}
                  </b>
                </div>
                <ScanProgressBar task={selectedScanTask} />
                <div className="scan-inline-meta">
                  <span>{scanProgressCount(selectedScanTask)}</span>
                  <span>
                    {selectedScanTask.currentItem ??
                      (selectedScanTask.status === 'running' ? '等待下一段列表…' : '结果已就绪')}
                  </span>
                </div>
              </section>
            )}
            <div className="form-grid">
                {batchResult && (
                  <div className="muted" style={{ fontSize: 12.5, lineHeight: 1.7 }}>
                    {batchResult.created.length > 0 && (
                      <div>✅ 新增 {batchResult.created.length} 个：{batchResult.created.join('、')}</div>
                    )}
                    {batchResult.refreshed.length > 0 && (
                      <div>🔄 已更新官方状态 {batchResult.refreshed.length} 个：{batchResult.refreshed.join('、')}</div>
                    )}
                    {batchResult.duplicates.length > 0 && (
                      <div>↩️ 跳过重复 {batchResult.duplicates.length} 个：{batchResult.duplicates.join('、')}</div>
                    )}
                    {batchResult.failed.length > 0 && (
                      <div>⚠️ 失败 {batchResult.failed.length} 个：{batchResult.failed.join('、')}</div>
                    )}
                  </div>
                )}
                {scanEntries !== null && (
                  <div style={{ display: 'grid', gap: 8 }}>
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
                          checked={showNoStreak}
                          onChange={(event) => setShowNoStreak(event.target.checked)}
                        />
                        显示无火花会话（{scanNoStreakCount}）
                      </label>
                    </div>
                    <div className="pick-list">
                      {scanFiltered.length === 0 ? (
                        <div className="muted" style={{ padding: '14px 12px', fontSize: 12 }}>
                          {scanQuery.trim()
                            ? '没有匹配的会话，换个关键词试试。'
                            : showNoStreak
                            ? '没有可显示的会话。'
                            : '没有带火花的会话——勾选右上角开关可查看全部会话。'}
                        </div>
                      ) : (
                        scanFiltered.map((entry) => {
                          const exists = existingNicknames.has(entry.title)
                          const checked = selectedEntries.has(entry.title)
                          const group = looksLikeGroup(entry.title, entry.preview)
                          return (
                            <label
                              key={entry.title}
                              className={[
                                'pick-row',
                                checked ? 'selected' : '',
                                exists ? 'occupied' : '',
                              ]
                                .filter(Boolean)
                                .join(' ')}
                              title={entry.preview || undefined}
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() =>
                                  setSelectedEntries((previous) => {
                                    const next = new Map(previous)
                                    if (next.has(entry.title)) next.delete(entry.title)
                                    else next.set(entry.title, entry)
                                    return next
                                  })
                                }
                              />
                              <span className="pick-name">{entry.title}</span>
                              <span className="pick-meta">
                                {isStreakTarget(entry) && (
                                  <SparkBadge
                                    days={entry.streakDays}
                                    kind={entry.streakKind}
                                    label={entry.streakLabel}
                                  />
                                )}
                                {entry.streakDays == null && !entry.streakLabel && hasSparkHint(entry) && (
                                  <span className="badge gold">🔥?</span>
                                )}
                                {group && <span className="badge">群</span>}
                                {exists && (
                                  <span className="badge">
                                    {checked ? '将刷新官方状态' : '已在列表'}
                                  </span>
                                )}
                              </span>
                            </label>
                          )
                        })
                      )}
                    </div>
                    <div className="pick-foot">
                      <span className="pick-stat">
                        扫描 {scanEntries.length} · 续火对象 {scanStreakCount} · 无火花 {scanNoStreakCount}（已隐藏）· 已在列表 {scanExistCount}
                      </span>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <select
                          value={batchTemplateId}
                          onChange={(event) => setBatchTemplateId(event.target.value)}
                          style={{ width: 170, padding: '5px 8px', fontSize: 12 }}
                          title="导入的好友统一绑定的模板（之后可单独换绑）"
                        >
                          <option value="">不绑定模板</option>
                          {templates.map((template) => (
                            <option key={template.id} value={template.id}>
                              {template.name}
                            </option>
                          ))}
                        </select>
                        <button
                          className="btn primary small"
                          type="button"
                          disabled={batchSubmitting || selectedEntries.size === 0}
                          onClick={() => void handleImportSelected()}
                        >
                          {batchSubmitting
                            ? '导入中…'
                            : `导入 / 刷新所选（${selectedEntries.size}）`}
                        </button>
                      </span>
                    </div>
                  </div>
                )}
              </div>
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
