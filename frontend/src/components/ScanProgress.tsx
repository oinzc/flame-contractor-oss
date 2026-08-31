import type { Account, ScanTaskState } from '../types'
import TaskDock from './TaskDock'

interface ProgressBarProps {
  task: ScanTaskState
  compact?: boolean
}

export function scanProgressPercent(task: ScanTaskState): number | null {
  if (task.status === 'completed') return 100
  if (task.total === null || task.total <= 0) return null
  const completed = Math.min(Math.max(task.completed, 0), task.total)
  return Math.round((completed / task.total) * 100)
}

export function scanProgressCount(task: ScanTaskState): string {
  if (task.status === 'running' && task.total === null) {
    return `已发现 ${task.completed} 个会话`
  }
  if (task.total === null) return `已处理 ${task.completed} 个会话`
  return `${Math.min(task.completed, task.total)} / ${task.total} 个会话`
}

export function ScanProgressBar({ task, compact = false }: ProgressBarProps) {
  const percent = scanProgressPercent(task)
  const isIndeterminate = percent === null && task.status === 'running'

  return (
    <div
      className={`scan-progress-track${compact ? ' compact' : ''}${isIndeterminate ? ' is-indeterminate' : ''}`}
      role="progressbar"
      aria-label="会话扫描进度"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent ?? undefined}
      aria-valuetext={percent === null ? '正在读取会话列表' : `${percent}%`}
    >
      <span
        className="scan-progress-fill"
        style={
          percent === null
            ? task.status === 'running'
              ? undefined
              : { transform: 'scaleX(0)' }
            : { transform: `scaleX(${percent / 100})` }
        }
      />
    </div>
  )
}

interface ScanDockProps {
  task: ScanTaskState
  accounts: Account[]
  onOpen: () => void
  onDismiss: () => void
  offset?: boolean
}

function elapsed(startedAt: string | null, finishedAt: string | null): string | null {
  if (!startedAt) return null
  const start = Date.parse(startedAt)
  if (Number.isNaN(start)) return null
  const end = finishedAt ? Date.parse(finishedAt) : Date.now()
  if (Number.isNaN(end)) return null
  const seconds = Math.max(0, Math.floor((end - start) / 1000))
  const minutes = Math.floor(seconds / 60)
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

export default function ScanProgressDock({ task, accounts, onOpen, onDismiss, offset = false }: ScanDockProps) {
  const account = accounts.find((item) => item.id === task.accountId)
  const percent = scanProgressPercent(task)
  const running = task.status === 'running'
  const interrupted = task.status === 'interrupted'
  const syncing = task.status === 'completed' && task.syncStatus === 'pending'
  const failed = task.status === 'failed' || interrupted || Boolean(task.syncError)
  const complete = task.status === 'completed' && !syncing
  const elapsedLabel = elapsed(task.startedAt, task.finishedAt)
  const stage = task.pollError ?? task.syncError ?? (syncing ? '正在保存官方状态快照' : task.stage)
  const currentItem =
    task.currentItem ??
    (running ? '继续读取下一段会话列表…' : complete ? '扫描结果已就绪' : '请重新发起扫描')
  const stateLabel = running
    ? percent === null
      ? '读取中'
      : `${percent}%`
    : interrupted
      ? '已中断'
      : failed
        ? '需处理'
        : complete
          ? '100%'
          : '收尾中'
  const note = task.pollError
    ? '进度连接暂时中断，扫描仍在后台运行，正在自动重连。'
    : task.syncError
      ? task.syncError
      : syncing
        ? '扫描已到 100%，正在把已存在好友的官方状态写回本地。'
        : running
          ? '可切换页面；扫描由后端继续执行，不会因页面收起而中断。'
          : interrupted
            ? '后端曾重启，扫描已安全收束；已发现结果已保留，请重新发起完整扫描。'
            : complete
              ? `扫描完成${elapsedLabel ? ` · 用时 ${elapsedLabel}` : ''}。`
              : '扫描未完成，请重新发起。'

  return (
    <TaskDock
      kind="scan"
      account={account}
      mark="↘"
      kicker={complete ? 'SCAN COMPLETE' : interrupted ? 'SCAN INTERRUPTED' : 'BACKGROUND SCAN'}
      statusLabel={stateLabel}
      percent={percent}
      progressLabel={scanProgressCount(task)}
      stage={stage}
      currentItem={currentItem}
      note={note}
      failed={failed}
      complete={complete}
      indeterminate={running}
      offset={offset}
      onRestore={onOpen}
      restoreLabel="查看好友页"
      onClose={!running && !syncing ? onDismiss : undefined}
    />
  )
}
