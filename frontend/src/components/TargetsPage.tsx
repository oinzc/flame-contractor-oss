import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type {
  Account,
  ContractTarget,
  IdentityHydrationTaskState,
  MessageTemplate,
  ScanChatEntry,
  ScanTaskState,
  TargetBatchResult,
} from '../types'
import { formatDateTime } from '../lib/format'
import { sparkLabelForSnapshot } from '../lib/spark'
import SparkBadge from './SparkBadge'
import OfficialSparkCalibrationModal from './OfficialSparkCalibrationModal'
import { ScanProgressBar, scanProgressCount, scanProgressPercent } from './ScanProgress'

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

function identityStatusLabel(entry: ScanChatEntry): string {
  if (entry.conversationType === 'group') {
    if (entry.conversationStatus === 'bound') {
      return entry.platformConversationId
        ? `群聊已识别 · ${compactStableId(entry.platformConversationId)}`
        : '群聊会话已识别'
    }
    if (entry.conversationStatus === 'ambiguous') {
      return `群聊同名冲突 · ${entry.conversationCandidateCount} 个会话`
    }
    return '群聊会话未识别'
  }
  if (entry.identityStatus === 'bound') {
    return entry.platformHandle ? `已识别 · ${entry.platformHandle}` : '稳定身份已识别'
  }
  if (entry.identityStatus === 'ambiguous') return `同名冲突 · ${entry.identityCandidateCount} 个候选`
  return '本次未识别'
}

function scanEntryMatchesTarget(entry: ScanChatEntry, target: ContractTarget): boolean {
  if (entry.platformUserId && target.platformUserId === entry.platformUserId) return true
  if (
    entry.platformConversationId &&
    target.platformConversationId === entry.platformConversationId
  ) {
    return true
  }
  if (target.nicknameSnapshot !== entry.title) return false
  return (
    target.conversationType === null ||
    entry.conversationType === null ||
    target.conversationType === entry.conversationType
  )
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
  const [deletingNoSpark, setDeletingNoSpark] = useState(false)
  const [dismissedNoSparkTaskId, setDismissedNoSparkTaskId] = useState<string | null>(null)
  const [sparkCalibrationTarget, setSparkCalibrationTarget] = useState<ContractTarget | null>(null)

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
    if (
      scanTask.status === 'completed' ||
      scanTask.status === 'interrupted' ||
      scanTask.status === 'cancelled'
    ) {
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
  const isExistingScanEntry = (entry: ScanChatEntry) =>
    accountTargets.some((target) => scanEntryMatchesTarget(entry, target))
  const selectedImportEntries = [...selectedEntries.values()].filter(
    (entry) => !isExistingScanEntry(entry),
  )

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

  const visibleScanEntries =
    selectedScanTask?.status === 'running' ? selectedScanTask.entries : scanEntries
  const scanLoading = scanStarting || scanTask?.status === 'running'
  const displayedScanError =
    scanError ??
    ((selectedScanTask?.status === 'failed' ||
      selectedScanTask?.status === 'interrupted' ||
      selectedScanTask?.status === 'cancelled')
      ? selectedScanTask.detail ?? '扫描未完成，请重新发起'
      : selectedScanTask?.syncError ?? null)

  // 抖音官方火花天数就是续火对象的判定信号；火花将熄的（消息提示）也算
  const scanStreakCount =
    visibleScanEntries?.filter((entry) => isStreakTarget(entry)).length ?? 0
  const scanNoStreakCount = (visibleScanEntries?.length ?? 0) - scanStreakCount
  const scanExistCount =
    visibleScanEntries?.filter((entry) => isExistingScanEntry(entry)).length ?? 0
  const scanIdentityCount =
    visibleScanEntries?.filter((entry) => entry.identityStatus === 'bound').length ?? 0
  const scanIdentityPendingCount =
    visibleScanEntries?.filter(
      (entry) => entry.conversationType !== 'group' && entry.identityStatus !== 'bound',
    ).length ?? 0
  const scanConversationCount =
    visibleScanEntries?.filter(
      (entry) => entry.conversationType === 'group' && entry.conversationStatus === 'bound',
    ).length ?? 0
  const scanFiltered = (visibleScanEntries ?? []).filter((entry) => {
    if (!showNoStreak && !isStreakTarget(entry)) return false
    const q = scanQuery.trim().toLowerCase()
    if (q && !entry.title.toLowerCase().includes(q)) return false
    return true
  })
  const existingNoSparkTargets =
    selectedScanTask?.status === 'completed' && selectedScanTask.syncStatus === 'done'
      ? accountTargets.filter(
          (target) =>
            target.conversationType !== 'group' &&
            (visibleScanEntries ?? []).some(
              (entry) =>
                entry.conversationType !== 'group' &&
                !isStreakTarget(entry) &&
                scanEntryMatchesTarget(entry, target),
            ),
        )
      : []
  const showNoSparkReminder =
    existingNoSparkTargets.length > 0 &&
    dismissedNoSparkTaskId !== selectedScanTask?.taskId

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

  async function handleDeleteNoSparkTargets() {
    if (existingNoSparkTargets.length === 0) return
    const names = existingNoSparkTargets.map((target) => target.nicknameSnapshot).join('、')
    if (
      !window.confirm(
        `确定删除本次扫描确认无火花的好友？\n\n${names}\n\n删除后将停止后续续火，并从好友列表隐藏；历史任务仍会保留，再次扫描导入时可以恢复。`,
      )
    ) {
      return
    }

    setDeletingNoSpark(true)
    setActionError(null)
    const failed: string[] = []
    for (const target of existingNoSparkTargets) {
      try {
        await api.deleteTarget(target.id)
      } catch {
        failed.push(target.nicknameSnapshot)
      }
    }
    setDeletingNoSpark(false)
    onChanged()
    if (failed.length > 0) {
      setActionError(`以下好友删除失败，请重试：${failed.join('、')}`)
      return
    }
    setDismissedNoSparkTaskId(selectedScanTask?.taskId ?? null)
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

  async function handleImportSelected() {
    if (selectedImportEntries.length === 0) return
    setBatchSubmitting(true)
    setActionError(null)
    try {
      const result = await api.createTargetsBatch({
        accountId: selectedAccountId,
        items: selectedImportEntries.map((entry) => ({
          nickname: entry.title,
          platformUserId: entry.platformUserId,
          platformHandle: entry.platformHandle,
          platformConversationId: entry.platformConversationId,
          conversationType: entry.conversationType,
          identityStatus: entry.identityStatus,
          conversationStatus: entry.conversationStatus,
          streakDays: entry.streakDays ?? undefined,
          sparkDays: entry.streakDays,
          // null 也要传递：重新扫描到普通状态时应清除旧的特殊徽标。
          sparkKind: entry.streakKind,
          sparkLabel: sparkLabelForSnapshot(entry),
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
                            <button
                              className="btn ghost small"
                              type="button"
                              onClick={() => setSparkCalibrationTarget(target)}
                              title="只修正抖音官方状态快照，不改本地累计"
                            >
                              校准官方
                            </button>
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

          <section className="card">
            <div className="card-head target-scan-head">
              <h2>会话扫描与批量添加</h2>
              <button
                className="btn small"
                type="button"
                disabled={scanLoading || selectedAccount?.status !== 'healthy'}
                title={
                  selectedAccount?.status !== 'healthy'
                    ? '账号需先扫码登录才能扫描会话列表'
                    : '后台滚动加载整个会话列表；完成后自动刷新已有好友/群聊的官方状态，下方仅需勾选新对象（约需 1–2 分钟）'
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
            </div>
            <p className="scan-helper">
              扫描只读取会话列表，不点击会话、不发送消息；完成后会自动刷新列表中已有好友/群聊的官方状态。已有对象会标记为已自动同步，下方仅需勾选未在列表的新对象进行导入。身份补采会逐个打开已有普通好友会话，可能标为已读，但同样不会发送。用户保存抖音稳定身份，群聊保存会话唯一 ID；未识别或同名冲突时绝不猜测绑定。
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
                      {selectedScanTask.syncStatus === 'pending'
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
            {(selectedScanTask?.status === 'interrupted' || selectedScanTask?.status === 'cancelled') &&
              scanEntries !== null && (
                <p className="scan-recovery-note">
                  {selectedScanTask.status === 'cancelled'
                    ? '这是取消前保存的部分结果，可继续手动导入；重新扫描可获取完整会话列表。'
                    : '这是后端重启前保存的部分结果，仅供查看或手动导入；重新扫描可获取完整会话列表。'}
                </p>
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
                    {batchResult.identityBound.length > 0 && (
                      <div>🔐 已绑定稳定身份 {batchResult.identityBound.length} 个：{batchResult.identityBound.join('、')}</div>
                    )}
                    {batchResult.identityUnresolved.length > 0 && (
                      <div>🔎 未观察到身份数据 {batchResult.identityUnresolved.length} 个：{batchResult.identityUnresolved.join('、')}</div>
                    )}
                    {batchResult.identityAmbiguous.length > 0 && (
                      <div>🛑 同名冲突，未绑定 {batchResult.identityAmbiguous.length} 个：{batchResult.identityAmbiguous.join('、')}</div>
                    )}
                    {batchResult.conversationBound.length > 0 && (
                      <div>👥 已绑定群会话 {batchResult.conversationBound.length} 个：{batchResult.conversationBound.join('、')}</div>
                    )}
                    {batchResult.conversationUnresolved.length > 0 && (
                      <div>🔎 未观察到群会话 ID {batchResult.conversationUnresolved.length} 个：{batchResult.conversationUnresolved.join('、')}</div>
                    )}
                    {batchResult.conversationAmbiguous.length > 0 && (
                      <div>🛑 群聊同名冲突，未绑定 {batchResult.conversationAmbiguous.length} 个：{batchResult.conversationAmbiguous.join('、')}</div>
                    )}
                  </div>
                )}
                {showNoSparkReminder && (
                  <section className="no-spark-review" role="alert" aria-live="polite">
                    <div className="no-spark-review-copy">
                      <span className="badge crimson">熄火复核 · {existingNoSparkTargets.length}</span>
                      <strong>这些好友已在续火列表中，但本次扫描没有检测到官方火花。</strong>
                      <p>
                        {existingNoSparkTargets
                          .map(
                            (target) =>
                              `${target.nicknameSnapshot}（本地历史 ${target.streakDays} 天）`,
                          )
                          .join('、')}
                      </p>
                      <small>没有火花通常无需继续续火。删除只会隐藏好友并停止后续发送，历史任务仍会保留。</small>
                    </div>
                    <div className="no-spark-review-actions">
                      <button
                        className="btn ghost small"
                        type="button"
                        disabled={deletingNoSpark}
                        onClick={() => setDismissedNoSparkTaskId(selectedScanTask?.taskId ?? null)}
                      >
                        暂时保留
                      </button>
                      <button
                        className="btn small no-spark-delete"
                        type="button"
                        disabled={deletingNoSpark}
                        onClick={() => void handleDeleteNoSparkTargets()}
                      >
                        {deletingNoSpark
                          ? '正在删除…'
                          : `删除这些好友（${existingNoSparkTargets.length}）`}
                      </button>
                    </div>
                  </section>
                )}
                {visibleScanEntries !== null && (
                  <div className="scan-pick-results" style={{ display: 'grid', gap: 8 }}>
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
                          const exists = isExistingScanEntry(entry)
                          const checked = !exists && selectedEntries.has(entry.title)
                          const group =
                            entry.conversationType === 'group' ||
                            looksLikeGroup(entry.title, entry.preview)
                          const identityStatus =
                            entry.conversationType === 'group'
                              ? entry.conversationStatus
                              : entry.identityStatus
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
                                disabled={exists}
                                checked={checked}
                                onChange={() =>
                                  setSelectedEntries((previous) => {
                                    if (exists) return previous
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
                                    officialDays={entry.streakDays}
                                    kind={entry.streakKind}
                                    label={entry.streakLabel}
                                  />
                                )}
                                {entry.streakDays == null && !entry.streakLabel && hasSparkHint(entry) && (
                                  <span className="badge gold">🔥?</span>
                                )}
                                {group && <span className="badge">群</span>}
                                <span
                                  className={`badge identity-badge identity-${identityStatus}`}
                                  title={
                                    entry.conversationType === 'group'
                                      ? entry.conversationDetail ?? undefined
                                      : entry.identityDetail ?? undefined
                                  }
                                >
                                  {identityStatusLabel(entry)}
                                </span>
                                {exists && (
                                  <span className="badge">已自动同步</span>
                                )}
                              </span>
                            </label>
                          )
                        })
                      )}
                    </div>
                    <div className="pick-foot">
                      <span className="pick-stat">
                        扫描 {visibleScanEntries.length} · 续火对象 {scanStreakCount} · 用户身份已识别 {scanIdentityCount} · 群会话已识别 {scanConversationCount} · 待识别 {scanIdentityPendingCount} · 已在列表 {scanExistCount}
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
                          disabled={batchSubmitting || selectedImportEntries.length === 0}
                          onClick={() => void handleImportSelected()}
                        >
                          {batchSubmitting
                            ? '导入中…'
                            : `导入新对象（${selectedImportEntries.length}）`}
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
                  <label htmlFor="target-uid">稳定身份 ID（选填）</label>
                  <input
                    id="target-uid"
                    value={platformUserId}
                    onChange={(event) => setPlatformUserId(event.target.value)}
                    placeholder="通常由扫描自动写入；仅供高级手动绑定"
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
              {error && <div className="form-error">{error}</div>}
              <div className="form-actions">
                <button className="btn primary small" type="submit" disabled={submitting}>
                  {submitting ? '添加中…' : '添加好友'}
                </button>
              </div>
            </form>
          </section>
          {sparkCalibrationTarget && (
            <OfficialSparkCalibrationModal
              target={sparkCalibrationTarget}
              onClose={() => setSparkCalibrationTarget(null)}
              onSubmit={async (patch) => {
                await api.patchTarget(sparkCalibrationTarget.id, patch)
                setSparkCalibrationTarget(null)
                await onChanged()
              }}
            />
          )}
        </>
      )}
    </div>
  )
}
