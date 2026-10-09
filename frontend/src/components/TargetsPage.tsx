import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type {
  Account,
  ContractTarget,
  IdentityHydrationTaskState,
  MessageTemplate,
  ScanTaskState,
} from '../types'
import { formatDateTime } from '../lib/format'
import SparkBadge from './SparkBadge'
import { ScanProgressBar, scanProgressCount, scanProgressPercent } from './ScanProgress'
import ScanReconciliationPanel from './ScanReconciliationPanel'
import FormDialog from './FormDialog'

interface Props {
  accounts: Account[]
  targets: ContractTarget[]
  templates: MessageTemplate[]
  onChanged: () => void
  onOpenSendTarget: (account: Account, target: ContractTarget) => void
  scanTask: ScanTaskState | null
  scanStarting: boolean
  onStartScan: (accountId: string) => Promise<void>
  identityTask: IdentityHydrationTaskState | null
  identityStarting: boolean
  onStartIdentityHydration: (accountId: string, targetIds: string[]) => Promise<void>
}

function compactStableId(value: string): string {
  if (value.length <= 18) return value
  return `${value.slice(0, 8)}…${value.slice(-6)}`
}

export default function TargetsPage({
  accounts,
  targets,
  templates,
  onChanged,
  onOpenSendTarget,
  scanTask,
  scanStarting,
  onStartScan,
  identityTask,
  identityStarting,
  onStartIdentityHydration,
}: Props) {
  const [selectedAccountId, setSelectedAccountId] = useState(accounts[0]?.id ?? '')
  const [activeView, setActiveView] = useState<'friends' | 'scan'>('friends')
  const [formOpen, setFormOpen] = useState(false)
  const [initialDraft, setInitialDraft] = useState('')
  const [showPaused, setShowPaused] = useState(false)
  const [nickname, setNickname] = useState('')
  const [platformUserId, setPlatformUserId] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [streakDays, setStreakDays] = useState('0')
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [scanError, setScanError] = useState<string | null>(null)

  useEffect(() => {
    if (!accounts.some((account) => account.id === selectedAccountId)) {
      setSelectedAccountId(accounts[0]?.id ?? '')
    }
  }, [accounts, selectedAccountId])

  const accountTargets = targets.filter((target) => target.accountId === selectedAccountId)
  const pausedCount = accountTargets.filter((target) => !target.enabled).length
  // 默认只显示启用中的好友；暂停项是保留的历史记录，按需展开查看
  const selectedTargets = showPaused ? accountTargets : accountTargets.filter((t) => t.enabled)
  const selectedAccount = accounts.find((account) => account.id === selectedAccountId) ?? null
  const identityTargetIds = accountTargets
    .filter(
      (target) =>
        target.conversationType !== 'group' &&
        !target.platformUserId &&
        !target.platformConversationId,
    )
    .map((target) => target.id)
  const selectedIdentityTask =
    identityTask?.accountId === selectedAccountId ? identityTask : null
  const identityRunning = identityTask?.status === 'running'
  const selectedScanTask =
    scanTask?.accountId === selectedAccountId ? scanTask : null

  const scanLoading = scanStarting || scanTask?.status === 'running'
  const displayedScanError =
    scanError ??
    ((selectedScanTask?.status === 'failed' ||
      selectedScanTask?.status === 'interrupted' ||
      selectedScanTask?.status === 'cancelled')
      ? selectedScanTask.detail ?? '扫描未完成，请重新发起'
      : selectedScanTask?.syncError ?? null)


  const draft = JSON.stringify({ nickname, platformUserId, templateId, streakDays })

  function openAddForm() {
    setNickname('')
    setPlatformUserId('')
    setTemplateId('')
    setStreakDays('0')
    setError(null)
    setInitialDraft(JSON.stringify({ nickname: '', platformUserId: '', templateId: '', streakDays: '0' }))
    setFormOpen(true)
  }

  async function handleDelete(target: ContractTarget) {
    if (
      !window.confirm(
        `确定删除好友「${target.nicknameSnapshot}」？\n\n已删除的好友会从列表、发送与扫描中隐藏，但保留历史任务记录。之后再次扫描或添加同名/同号好友会自动恢复。`,
      )
    ) {
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

  async function handleSetSendTime(target: ContractTarget) {
    const promptText = target.sendTimeHhmm
      ? `好友「${target.nicknameSnapshot}」当前固定发送时刻：${target.sendTimeHhmm}\n留空则跟随账号设置（账号精确时刻或发送窗口随机）：`
      : `好友「${target.nicknameSnapshot}」当前跟随账号设置。\n填写 HH:MM 精确发送时刻（如 21:00），留空则跟随账号：`
    const input = window.prompt(promptText, target.sendTimeHhmm ?? '')
    if (input === null) return
    const value = input.trim()
    if (value !== '' && !/^([01]\d|2[0-3]):[0-5]\d$/.test(value)) {
      setActionError('时间格式应为 HH:MM（如 21:00）')
      return
    }
    setActionError(null)
    try {
      await api.patchTarget(target.id, { sendTimeHhmm: value === '' ? null : value })
      onChanged()
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '设置失败，请重试')
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

  async function handleScan() {
    setScanError(null)
    setActionError(null)
    setActiveView('scan')
    try {
      await onStartScan(selectedAccountId)
    } catch (err) {
      setScanError(err instanceof ApiError ? err.message : '扫描失败，请重试')
    }
  }

  async function handleIdentityHydration() {
    if (identityTargetIds.length === 0) return
    const confirmed = window.confirm(
      `将逐个打开 ${identityTargetIds.length} 个未识别的普通好友会话，触发抖音官方身份接口。\n\n不会发送消息，但可能将这些会话标记为已读。是否继续？`,
    )
    if (!confirmed) return
    setActionError(null)
    try {
      await onStartIdentityHydration(selectedAccountId, identityTargetIds)
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '身份补采启动失败，请重试')
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
    if (submitting) return
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
      setFormOpen(false)
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
        <div className="page-actions">
          <button
            className="btn small"
            type="button"
            disabled={scanLoading || selectedAccount?.status !== 'healthy'}
            title={
              selectedAccount?.status !== 'healthy'
                ? '账号需先扫码登录才能扫描会话列表'
                : '读取抖音会话列表并自动刷新已有好友/群聊的官方火花状态；只读页面，不发送消息（约需 1–2 分钟）'
            }
            onClick={() => void handleScan()}
          >
            {scanLoading
              ? scanStarting
                ? '正在启动扫描…'
                : selectedScanTask?.status === 'running'
                  ? `扫描中… ${scanProgressPercent(selectedScanTask) === null ? '读取中' : `${scanProgressPercent(selectedScanTask)}%`}`
                  : '其他账号扫描中…'
              : '扫描会话'}
          </button>
          <button className="btn primary small" type="button" disabled={!selectedAccount} onClick={openAddForm}>+ 添加好友</button>
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

          <div className="workspace-tabs" role="tablist" aria-label="好友管理视图"
            onKeyDown={(event) => {
              if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
              event.preventDefault()
              const next = event.key === 'Home' ? 'friends' : event.key === 'End' ? 'scan' : activeView === 'friends' ? 'scan' : 'friends'
              setActiveView(next)
              event.currentTarget.querySelector<HTMLButtonElement>(`#${next}-tab`)?.focus()
            }}>
            <button id="friends-tab" className="workspace-tab" type="button" role="tab" aria-selected={activeView === 'friends'} aria-controls="friends-panel" tabIndex={activeView === 'friends' ? 0 : -1} onClick={() => setActiveView('friends')}>好友列表（{selectedTargets.length}）</button>
            <button id="scan-tab" className="workspace-tab" type="button" role="tab" aria-selected={activeView === 'scan'} aria-controls="scan-panel" tabIndex={activeView === 'scan' ? 0 : -1} onClick={() => setActiveView('scan')}>扫描结果{selectedScanTask?.status === 'running' ? ' · 进行中' : selectedScanTask?.finishedAt ? ` · ${formatDateTime(selectedScanTask.finishedAt)}` : ''}</button>
          </div>
          {actionError && <div className="form-error" role="alert">{actionError}</div>}
          {displayedScanError && <div className="form-error" role="alert">{displayedScanError}</div>}

          <section className="card workspace-panel" role="tabpanel" id="friends-panel" aria-labelledby="friends-tab" hidden={activeView !== 'friends'}>
            <div className="target-list-head">
              <div className="target-list-title">
                <h2>好友列表（{selectedTargets.length}）</h2>
              </div>
              <div className="target-list-actions">
                <button
                  className="btn small"
                  type="button"
                  disabled={
                    identityStarting ||
                    identityRunning ||
                    scanLoading ||
                    identityTargetIds.length === 0 ||
                    selectedAccount?.status !== 'healthy'
                  }
                  title={
                    identityTargetIds.length === 0
                      ? '普通好友身份均已绑定'
                      : selectedAccount?.status !== 'healthy'
                        ? '账号需处于健康登录状态才能补采身份'
                        : '逐个打开已有普通好友会话以触发官方身份接口；不会发送消息，但可能将会话标为已读'
                  }
                  onClick={() => void handleIdentityHydration()}
                >
                  {identityStarting
                    ? '正在启动补采…'
                    : identityRunning
                      ? selectedIdentityTask
                        ? `补采中… ${Math.round((selectedIdentityTask.completed / Math.max(selectedIdentityTask.total, 1)) * 100)}%`
                        : '其他账号补采中…'
                      : `补采好友身份（${identityTargetIds.length}）`}
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
            {selectedTargets.length === 0 ? (
              <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
                {!showPaused && pausedCount > 0
                  ? `好友都处于暂停状态（${pausedCount} 个），勾选上方「显示已暂停」可查看。`
                  : '这个账号还没有好友，点击「添加好友」或扫描会话进行导入。'}
              </p>
            ) : (
              <div className="target-table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>昵称</th>
                      <th>平台身份</th>
                      <th>模板</th>
                      <th>抖音官方状态</th>
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
                        <td>
                          {target.conversationType === 'group' ? (
                            <div className="identity-cell">
                              <strong title={target.platformConversationId ?? undefined}>群聊会话</strong>
                              {target.platformConversationId && (
                                <span
                                  className="identity-stable-id"
                                  title={`群会话唯一 ID：${target.platformConversationId}`}
                                >
                                  {compactStableId(target.platformConversationId)}
                                </span>
                              )}
                            </div>
                          ) : (
                            <div className="identity-cell">
                              <strong title={target.platformHandle ?? undefined}>
                                {target.platformHandle ?? (target.platformUserId ? '稳定身份已绑定' : '未识别')}
                              </strong>
                              {target.platformUserId && (
                                <span
                                  className="identity-stable-id"
                                  title={`稳定身份：${target.platformUserId}`}
                                >
                                  {compactStableId(target.platformUserId)}
                                </span>
                              )}
                            </div>
                          )}
                        </td>
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
                              officialDays={target.sparkDays}
                              kind={target.sparkKind}
                              label={target.sparkLabel}
                              scannedAt={target.sparkScannedAt}
                            />
                            {target.sparkScannedAt && (
                              <span className="spark-scan-note">
                                官方状态更新于 {formatDateTime(target.sparkScannedAt)}
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="mono">{formatDateTime(target.lastConfirmedAt)}</td>
                        <td>
                          <button
                            className="btn ghost small"
                            type="button"
                            onClick={() => handleSetSendTime(target)}
                            title={target.sendTimeHhmm ? `固定 ${target.sendTimeHhmm} 发送` : '跟随账号设置'}
                          >
                            {target.sendTimeHhmm ? target.sendTimeHhmm : '定时'}
                          </button>
                          <button
                            className="btn ghost small"
                            type="button"
                            style={{ marginLeft: 6 }}
                            onClick={() => {
                              const account = accounts.find((item) => item.id === selectedAccountId)
                              if (account) onOpenSendTarget(account, target)
                            }}
                          >
                            发送
                          </button>
                          <button
                            className="btn ghost small"
                            type="button"
                            style={{ marginLeft: 6 }}
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

          <section className="card workspace-panel" role="tabpanel" id="scan-panel" aria-labelledby="scan-tab" hidden={activeView !== 'scan'}>
            <div className="card-head target-scan-head">
              <h2>扫描结果与对账</h2>
            </div>
            <p className="scan-helper">
              扫描会刷新已有好友的官方状态，不发送消息。新会话需勾选后导入，无火花对象需确认后删除；切换页面不影响后台扫描。
            </p>
            {selectedScanTask && (
              <section
                className={`scan-inline-progress${selectedScanTask.status === 'failed' || selectedScanTask.status === 'interrupted' || selectedScanTask.status === 'cancelled' ? ' is-error' : ''}${selectedScanTask.status === 'completed' ? ' is-complete' : ''}`}
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
                          : selectedScanTask.status === 'interrupted'
                            ? '扫描已中断，保留部分结果'
                            : selectedScanTask.status === 'cancelled'
                              ? '扫描已取消，保留部分结果'
                              : '会话列表扫描未完成'}
                    </strong>
                    <p>
                      {selectedScanTask.status === 'completed' && selectedScanTask.syncStatus === 'pending'
                        ? '正在把已存在好友的官方状态写回本地…'
                        : selectedScanTask.pollError ?? selectedScanTask.syncError ?? selectedScanTask.stage}
                    </p>
                  </div>
                  <b>
                    {scanProgressPercent(selectedScanTask) === null
                      ? selectedScanTask.status === 'interrupted'
                        ? '已中断'
                        : selectedScanTask.status === 'cancelled'
                          ? '已取消'
                          : '读取中'
                      : `${scanProgressPercent(selectedScanTask)}%`}
                  </b>
                </div>
                <ScanProgressBar task={selectedScanTask} />
                <div className="scan-inline-meta">
                  <span>{scanProgressCount(selectedScanTask)}</span>
                  <span>
                    {selectedScanTask.currentItem ??
                      (selectedScanTask.status === 'running'
                        ? '等待下一段列表…'
                        : selectedScanTask.status === 'interrupted' || selectedScanTask.status === 'cancelled'
                          ? '已保存已发现的部分会话'
                          : '结果已就绪')}
                  </span>
                </div>
              </section>
            )}
            <ScanReconciliationPanel
              accountId={selectedAccountId}
              task={selectedScanTask}
              targets={accountTargets}
              templates={templates}
              onChanged={onChanged}
              onError={setActionError}
            />
          </section>

          {formOpen && (
            <FormDialog title="添加好友" description={`所属账号：${selectedAccount?.displayName ?? ''}。昵称须与抖音显示完全一致。`}
              formId="target-editor-form" submitLabel={submitting ? '添加中…' : '添加好友'}
              dirty={draft !== initialDraft} busy={submitting} onClose={() => { setFormOpen(false); setError(null) }}>
              <form id="target-editor-form" className="form-grid" onSubmit={handleSubmit}>
                <div className="field">
                  <label htmlFor="target-nickname">昵称（必填）</label>
                  <input
                    autoFocus
                    id="target-nickname"
                    value={nickname}
                    onChange={(event) => setNickname(event.target.value)}
                    placeholder="与抖音显示完全一致"
                    maxLength={80}
                    required
                  />
                </div>
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
                <details className="form-advanced">
                  <summary>高级设置</summary>
                  <div className="form-grid">
                    <div className="field">
                      <label htmlFor="target-uid">稳定身份 ID（选填）</label>
                      <input
                        id="target-uid"
                        value={platformUserId}
                        onChange={(event) => setPlatformUserId(event.target.value)}
                        placeholder="通常由扫描自动写入；仅供高级手动绑定"
                        maxLength={128}
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="target-streak">本地初始累计（仅新建）</label>
                      <input
                        id="target-streak"
                        type="number"
                        min={0}
                        value={streakDays}
                        onChange={(event) => setStreakDays(event.target.value)}
                      />
                    </div>
                  </div>
                </details>
                {error && <div className="form-error" role="alert">{error}</div>}
              </form>
            </FormDialog>
          )}
        </>
      )}
    </div>
  )
}
