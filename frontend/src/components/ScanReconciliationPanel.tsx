import { useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../api'
import { formatDateTime } from '../lib/format'
import { sparkLabelForSnapshot } from '../lib/spark'
import type {
  ContractTarget,
  MessageTemplate,
  ScanChatEntry,
  ScanHistoryItem,
  ScanReconciliationCategory,
  ScanReconciliationItem,
  ScanTaskState,
  TargetBatchResult,
} from '../types'
import SparkBadge from './SparkBadge'

interface Props {
  accountId: string
  task: ScanTaskState | null
  targets: ContractTarget[]
  templates: MessageTemplate[]
  onChanged: () => void
  onError: (message: string | null) => void
}

const CATEGORY_ORDER: ScanReconciliationCategory[] = [
  'changed',
  'no_spark',
  'new',
  'not_observed',
  'unchanged',
]

const CATEGORY_LABELS: Record<ScanReconciliationCategory, string> = {
  changed: '状态变化',
  no_spark: '无火花',
  new: '新会话',
  not_observed: '未观察到',
  unchanged: '无变化',
}

const STATUS_LABELS: Record<ScanHistoryItem['status'], string> = {
  running: '进行中',
  completed: '已完成',
  failed: '失败',
  interrupted: '已中断',
  cancelled: '已取消',
}

function emptyCounts() {
  return { changed: 0, noSpark: 0, new: 0, notObserved: 0, unchanged: 0 }
}

function reconciliationHasData(task: ScanTaskState | ScanHistoryItem): boolean {
  return task.reconciliation.schemaVersion === 1
}

function scanEntryForItem(
  item: ScanReconciliationItem,
  entries: ScanChatEntry[],
): ScanChatEntry | null {
  return (
    entries.find((entry) => entry.title === item.title) ??
    null
  )
}

function identityLabel(entry: ScanChatEntry): string {
  if (entry.conversationType === 'group') {
    if (entry.conversationStatus === 'bound') return '群会话已识别'
    if (entry.conversationStatus === 'ambiguous') return '群聊同名冲突'
    return '群会话未识别'
  }
  if (entry.identityStatus === 'bound') {
    return entry.platformHandle ? `已识别 · ${entry.platformHandle}` : '稳定身份已识别'
  }
  if (entry.identityStatus === 'ambiguous') return `同名冲突 · ${entry.identityCandidateCount} 个候选`
  return '本次未识别'
}

function conversationLabel(type: ScanReconciliationItem['conversationType']): string {
  return type === 'group' ? '群聊' : '好友'
}

function stateLabel(state: ScanReconciliationItem['current']): string {
  if (state.label) return state.label
  if (state.days != null) return `${state.days} 天`
  return '未记录'
}

function itemKey(item: ScanReconciliationItem, index: number): string {
  return `${item.category}:${item.targetId ?? 'new'}:${item.title}:${index}`
}

function scanEntryIsImportable(entry: ScanChatEntry | null): entry is ScanChatEntry {
  return entry !== null
}

export default function ScanReconciliationPanel({
  accountId,
  task,
  targets,
  templates,
  onChanged,
  onError,
}: Props) {
  const [history, setHistory] = useState<ScanHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [historyTaskId, setHistoryTaskId] = useState<string | null>(null)
  const [activeCategory, setActiveCategory] = useState<ScanReconciliationCategory>('changed')
  const [unchangedOpen, setUnchangedOpen] = useState(false)
  const [selectedNewTitles, setSelectedNewTitles] = useState<Set<string>>(new Set())
  const [batchTemplateId, setBatchTemplateId] = useState('')
  const [batchSubmitting, setBatchSubmitting] = useState(false)
  const [deletingNoSpark, setDeletingNoSpark] = useState(false)
  const [noSparkDismissed, setNoSparkDismissed] = useState(false)
  const [batchResult, setBatchResult] = useState<TargetBatchResult | null>(null)
  const [query, setQuery] = useState('')

  useEffect(() => {
    let cancelled = false
    if (!accountId) {
      setHistory([])
      return undefined
    }
    setHistoryLoading(true)
    setHistoryError(null)
    api
      .scanChatsHistory(accountId, 10)
      .then((next) => {
        if (!cancelled) setHistory(next)
      })
      .catch((err) => {
        if (cancelled) return
        const message = err instanceof ApiError ? err.message : '最近扫描记录读取失败'
        setHistoryError(message)
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [accountId, task?.taskId, task?.status])

  useEffect(() => {
    setHistoryTaskId(null)
    setSelectedNewTitles(new Set())
    setBatchResult(null)
    setNoSparkDismissed(false)
    setQuery('')
  }, [task?.taskId, accountId])

  useEffect(() => {
    // 历史记录是只读视图，不继承当前扫描中的勾选与筛选状态。
    setSelectedNewTitles(new Set())
    setQuery('')
  }, [historyTaskId])

  const historyTask = history.find((item) => item.taskId === historyTaskId) ?? (task === null ? history[0] ?? null : null)
  const viewedTask = historyTask ?? task
  const viewedReconciliation = viewedTask?.reconciliation ?? null
  const readOnly = historyTask !== null
  const canOperate = Boolean(
    !readOnly &&
      task?.status === 'completed' &&
      task.syncStatus === 'done' &&
      task.overall === 'ok' &&
      reconciliationHasData(task),
  )
  const counts = viewedReconciliation?.counts ?? emptyCounts()
  const categoryCount = (category: ScanReconciliationCategory): number => {
    if (category === 'no_spark') return counts.noSpark
    if (category === 'not_observed') return counts.notObserved
    return counts[category]
  }

  useEffect(() => {
    if (!viewedReconciliation) return
    const firstNonEmpty = CATEGORY_ORDER.find((category) => categoryCount(category) > 0)
    setActiveCategory(firstNonEmpty ?? 'unchanged')
    setUnchangedOpen(firstNonEmpty === undefined)
  }, [historyTaskId, historyTask?.taskId, task?.taskId, task?.reconciliation])

  const viewedEntries = viewedReconciliation?.entries ?? []
  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    return viewedEntries.filter(
      (item) =>
        item.category === activeCategory &&
        (!normalized || item.title.toLowerCase().includes(normalized)),
    )
  }, [activeCategory, query, viewedEntries])
  const targetById = useMemo(
    () => new Map(targets.map((target) => [target.id, target])),
    [targets],
  )
  const entryByTitle = useMemo(
    () => new Map((task?.entries ?? []).map((entry) => [entry.title, entry])),
    [task?.entries],
  )
  // 筛选仅影响当前可见列表，不能悄悄丢掉用户已经勾选的其他新会话。
  const currentNewItems = viewedEntries.filter((item) => item.category === 'new')
  const selectedNewItems = currentNewItems.filter((item) => selectedNewTitles.has(item.title))
  const noSparkItems = viewedEntries.filter((item) => item.category === 'no_spark')
  const currentNoSparkTargets = noSparkItems
    .map((item) => (item.targetId ? targetById.get(item.targetId) : undefined))
    .filter((target): target is ContractTarget => target !== undefined)
  const importedNames = new Set([
    ...(batchResult?.created ?? []),
    ...(batchResult?.refreshed ?? []),
    ...(batchResult?.duplicates ?? []),
  ])

  function selectNew(title: string) {
    setSelectedNewTitles((previous) => {
      const next = new Set(previous)
      if (next.has(title)) next.delete(title)
      else next.add(title)
      return next
    })
  }

  async function handleImport() {
    if (!canOperate || selectedNewItems.length === 0) return
    const entries = selectedNewItems
      .map((item) => entryByTitle.get(item.title) ?? null)
      .filter(scanEntryIsImportable)
    if (entries.length === 0) return
    setBatchSubmitting(true)
    onError(null)
    try {
      const result = await api.createTargetsBatch({
        accountId,
        templateId: batchTemplateId || undefined,
        items: entries.map((entry) => ({
          nickname: entry.title,
          platformUserId: entry.platformUserId,
          platformHandle: entry.platformHandle,
          platformConversationId: entry.platformConversationId,
          conversationType: entry.conversationType,
          identityStatus: entry.identityStatus,
          conversationStatus: entry.conversationStatus,
          streakDays: entry.streakDays ?? undefined,
          sparkDays: entry.streakDays,
          sparkKind: entry.streakKind,
          sparkLabel: sparkLabelForSnapshot(entry),
        })),
      })
      setBatchResult(result)
      setSelectedNewTitles(new Set())
      onChanged()
    } catch (err) {
      onError(err instanceof ApiError ? err.message : '导入失败，请重试')
    } finally {
      setBatchSubmitting(false)
    }
  }

  async function handleDeleteNoSpark() {
    if (!canOperate || currentNoSparkTargets.length === 0) return
    const names = currentNoSparkTargets.map((target) => target.nicknameSnapshot).join('、')
    if (
      !window.confirm(
        `确定删除本次扫描确认无火花的好友？\n\n${names}\n\n删除后将停止后续续火并隐藏好友，历史任务仍会保留，再次扫描导入时可以恢复。`,
      )
    ) {
      return
    }
    setDeletingNoSpark(true)
    onError(null)
    const results = await Promise.allSettled(
      currentNoSparkTargets.map((target) => api.deleteTarget(target.id)),
    )
    const failed = currentNoSparkTargets.filter((_, index) => results[index].status === 'rejected')
    setDeletingNoSpark(false)
    onChanged()
    if (failed.length > 0) {
      onError(`以下好友删除失败，请重试：${failed.map((target) => target.nicknameSnapshot).join('、')}`)
      return
    }
    setNoSparkDismissed(true)
  }

  function renderOfficialBadge(
    state: ScanReconciliationItem['current'],
    scannedAt?: string | null,
  ) {
    return (
      <SparkBadge
        officialDays={state.days}
        kind={state.kind}
        label={state.label}
        scannedAt={scannedAt}
      />
    )
  }

  function renderItem(item: ScanReconciliationItem, index: number) {
    const entry = scanEntryForItem(item, task?.entries ?? [])
    const target = item.targetId ? targetById.get(item.targetId) : undefined
    const imported = item.category === 'new' && importedNames.has(item.title)
    const selectable = item.category === 'new' && canOperate && scanEntryIsImportable(entry) && !imported

    if (item.category === 'changed') {
      return (
        <div className="reconciliation-row" key={itemKey(item, index)}>
          <div className="reconciliation-row-main">
            <strong>{item.title}</strong>
            <span>{conversationLabel(item.conversationType)}</span>
          </div>
          <div className="reconciliation-transition" aria-label={`${stateLabel(item.previous)} 到 ${stateLabel(item.current)}`}>
            {renderOfficialBadge(item.previous, item.previous.scannedAt)}
            <span className="reconciliation-arrow" aria-hidden="true">→</span>
            {renderOfficialBadge(item.current, viewedTask?.finishedAt)}
          </div>
        </div>
      )
    }

    if (item.category === 'no_spark') {
      return (
        <div className="reconciliation-row" key={itemKey(item, index)}>
          <div className="reconciliation-row-main">
            <strong>{item.title}</strong>
            <span>{conversationLabel(item.conversationType)} · 本地历史 {target?.streakDays ?? 0} 天</span>
          </div>
          <div className="reconciliation-row-side">
            {renderOfficialBadge(item.current, viewedTask?.finishedAt)}
            <span className="badge crimson">需复核</span>
          </div>
        </div>
      )
    }

    if (item.category === 'new') {
      return (
        <label className={`reconciliation-row reconciliation-select-row${selectedNewTitles.has(item.title) ? ' is-selected' : ''}`} key={itemKey(item, index)}>
          <input
            type="checkbox"
            checked={selectedNewTitles.has(item.title)}
            disabled={!selectable}
            onChange={() => selectNew(item.title)}
            aria-label={`选择导入 ${item.title}`}
          />
          <div className="reconciliation-row-main">
            <strong>{item.title}</strong>
            <span>{conversationLabel(item.conversationType)} · {entry ? identityLabel(entry) : '扫描条目未保存'}</span>
          </div>
          <div className="reconciliation-row-side">
            {renderOfficialBadge(item.current, viewedTask?.finishedAt)}
            {item.conversationType === 'group' && <span className="badge">群</span>}
            {imported ? <span className="badge">已导入</span> : entry && <span className="badge identity-badge">{identityLabel(entry)}</span>}
          </div>
        </label>
      )
    }

    if (item.category === 'not_observed') {
      return (
        <div className="reconciliation-row" key={itemKey(item, index)}>
          <div className="reconciliation-row-main">
            <strong>{item.title}</strong>
            <span>{conversationLabel(item.conversationType)}</span>
          </div>
          <p className="reconciliation-row-note">本次未看到，不代表无火花；未修改官方状态</p>
        </div>
      )
    }

    return (
      <div className="reconciliation-row reconciliation-row-muted" key={itemKey(item, index)}>
        <div className="reconciliation-row-main">
          <strong>{item.title}</strong>
          <span>{conversationLabel(item.conversationType)}</span>
        </div>
        <div className="reconciliation-row-side">{renderOfficialBadge(item.current, viewedTask?.finishedAt)}</div>
      </div>
    )
  }

  const noData = viewedTask !== null && !reconciliationHasData(viewedTask)
  const noSparkReviewVisible = activeCategory === 'no_spark' && canOperate && !noSparkDismissed

  return (
    <section className="reconciliation-panel" aria-labelledby="scan-reconciliation-title">
      <div className="reconciliation-head">
        <div>
          <span className="scan-section-kicker">SCAN RECONCILIATION</span>
          <h3 id="scan-reconciliation-title">蚀刻对账轨</h3>
          <p>
            {readOnly
              ? `历史记录 · ${formatDateTime(historyTask?.startedAt ?? historyTask?.finishedAt)} · 只读查看，不修改好友。`
              : '按扫描前快照与本次完整结果对照；官方状态仍只来自抖音页面扫描。'}
          </p>
        </div>
        {readOnly && task !== null && (
          <button className="btn ghost small" type="button" onClick={() => setHistoryTaskId(null)}>
            回到本次扫描
          </button>
        )}
      </div>

      {noData ? (
        <div className="reconciliation-empty">
          {viewedTask?.status === 'completed'
            ? '此扫描早于对账功能，暂无可展示的持久化变更。'
            : '只有完整成功扫描才会生成对账；本次未生成可操作结论。'}
        </div>
      ) : (
        <>
          <div className="reconciliation-rail" role="tablist" aria-label="扫描对账分类">
            {CATEGORY_ORDER.map((category) => (
              <button
                key={category}
                type="button"
                role="tab"
                aria-selected={activeCategory === category}
                className={`reconciliation-rail-item recon-${category}${activeCategory === category ? ' is-active' : ''}`}
                onClick={() => setActiveCategory(category)}
              >
                <span className="reconciliation-rail-label">{CATEGORY_LABELS[category]}</span>
                <strong className="reconciliation-rail-count">{categoryCount(category)}</strong>
              </button>
            ))}
          </div>

          <div className="reconciliation-tools">
            <label className="recon-search-label" htmlFor="reconciliation-search">筛选</label>
            <input
              id="reconciliation-search"
              className="reconciliation-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="按昵称筛选…"
            />
            {activeCategory === 'unchanged' && (
              <button
                className="btn ghost small"
                type="button"
                aria-expanded={unchangedOpen}
                onClick={() => setUnchangedOpen((open) => !open)}
              >
                {unchangedOpen ? '收起无变化' : '展开无变化'}
              </button>
            )}
          </div>

          {noSparkReviewVisible && currentNoSparkTargets.length > 0 && (
            <div className="reconciliation-review" role="alert">
              <div>
                <strong>本次扫描未检测到这些好友的官方火花。</strong>
                <p>
                  {currentNoSparkTargets
                    .map((target) => `${target.nicknameSnapshot}（本地历史 ${target.streakDays} 天）`)
                    .join('、')}
                </p>
                <small>删除只会隐藏好友并停止后续发送；历史任务会保留，再次扫描导入时可以恢复。</small>
              </div>
              <div className="reconciliation-actions">
                <button className="btn ghost small" type="button" onClick={() => setNoSparkDismissed(true)}>
                  暂时保留
                </button>
                <button
                  className="btn small no-spark-delete"
                  type="button"
                  disabled={deletingNoSpark}
                  onClick={() => void handleDeleteNoSpark()}
                >
                  {deletingNoSpark ? '正在删除…' : `删除这些好友（${currentNoSparkTargets.length}）`}
                </button>
              </div>
            </div>
          )}

          {activeCategory === 'unchanged' && !unchangedOpen ? (
            <div className="reconciliation-collapsed">无变化对象已收起，可展开查看。</div>
          ) : visibleItems.length > 0 ? (
            <div className="reconciliation-list">
              {visibleItems.map(renderItem)}
            </div>
          ) : (
            <div className="reconciliation-collapsed">当前分类没有匹配的记录。</div>
          )}

          {activeCategory === 'new' && (
            <div className="reconciliation-import-bar">
              <span>
                {readOnly
                  ? '历史扫描只读，不能导入新会话。'
                  : `已选择 ${selectedNewItems.length} 个新会话；新会话不会自动加入好友。`}
              </span>
              <div>
                <select
                  value={batchTemplateId}
                  onChange={(event) => setBatchTemplateId(event.target.value)}
                  disabled={!canOperate}
                  aria-label="新会话导入模板"
                >
                  <option value="">不绑定模板</option>
                  {templates.map((template) => (
                    <option key={template.id} value={template.id}>{template.name}</option>
                  ))}
                </select>
                <button
                  className="btn primary small"
                  type="button"
                  disabled={!canOperate || batchSubmitting || selectedNewItems.length === 0}
                  onClick={() => void handleImport()}
                >
                  {batchSubmitting ? '导入中…' : `导入新对象（${selectedNewItems.length}）`}
                </button>
              </div>
            </div>
          )}

          {batchResult && (
            <div className="reconciliation-result" role="status">
              导入结果：新增 {batchResult.created.length} · 已更新 {batchResult.refreshed.length} · 重复 {batchResult.duplicates.length} · 失败 {batchResult.failed.length}
            </div>
          )}
        </>
      )}

      <div className="reconciliation-history-head">
        <div>
          <span className="scan-section-kicker">RECENT SCANS</span>
          <h4>最近扫描记录</h4>
        </div>
        <span className="muted">最近 10 次</span>
      </div>
      {historyError && <p className="form-error">{historyError}</p>}
      {historyLoading ? (
        <p className="reconciliation-collapsed">正在读取最近扫描记录…</p>
      ) : history.length === 0 ? (
        <p className="reconciliation-collapsed">还没有扫描记录。</p>
      ) : (
        <div className="reconciliation-history-list">
          {history.map((record) => (
            <button
              className={`reconciliation-history-row${historyTask?.taskId === record.taskId ? ' is-active' : ''}`}
              aria-pressed={historyTask?.taskId === record.taskId}
              key={record.taskId}
              type="button"
              onClick={() => setHistoryTaskId(record.taskId)}
            >
              <span className="reconciliation-history-time">{formatDateTime(record.startedAt ?? record.finishedAt)}</span>
              <span>{record.source === 'automatic' ? '自动' : '手动'}</span>
              <span className={`reconciliation-history-status is-${record.status}`}>{STATUS_LABELS[record.status]}</span>
              <span className="reconciliation-history-counts">
                变 {record.reconciliation.counts.changed} · 熄 {record.reconciliation.counts.noSpark} · 新 {record.reconciliation.counts.new} · 未 {record.reconciliation.counts.notObserved} · 同 {record.reconciliation.counts.unchanged}
              </span>
            </button>
          ))}
        </div>
      )}
    </section>
  )
}
