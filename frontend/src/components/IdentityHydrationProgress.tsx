import type { Account, IdentityHydrationTaskState } from '../types'
import TaskDock from './TaskDock'

interface Props {
  task: IdentityHydrationTaskState
  accounts: Account[]
  onOpen: () => void
  onDismiss: () => void
  onCancel?: () => void
  onRetry?: () => void
  cancelLabel?: string
  cancelDisabled?: boolean
  offset?: boolean | number
}

export function identityHydrationPercent(task: IdentityHydrationTaskState): number {
  if (task.status === 'completed') return 100
  if (task.total <= 0) return 0
  return Math.round((Math.min(Math.max(task.completed, 0), task.total) / task.total) * 100)
}

export default function IdentityHydrationProgress({
  task,
  accounts,
  onOpen,
  onDismiss,
  onCancel,
  onRetry,
  cancelLabel,
  cancelDisabled = false,
  offset = false,
}: Props) {
  const account = accounts.find((item) => item.id === task.accountId)
  const percent = identityHydrationPercent(task)
  const running = task.status === 'running'
  const complete = task.status === 'completed'
  const failed = task.status === 'failed' || task.status === 'interrupted' || task.status === 'cancelled'
  const bound = task.results.filter((item) => item.status === 'bound').length
  const unresolved = task.results.filter((item) => item.status === 'not_observed').length
  const blocked = task.results.filter((item) => item.status === 'ambiguous' || item.status === 'conflict').length
  const retryable = !running && !complete && Boolean(onRetry)
  const stage = task.pollError ?? task.detail ?? task.stage
  const currentItem = task.currentItem ?? (complete ? '身份补采结果已写入好友列表' : '等待下一位好友')
  const note = task.pollError
    ? '进度读取暂时失败，后台任务仍在继续，正在自动重连。'
    : running
      ? '可切换页面；后台会逐个打开已有好友会话，不会发送消息，但可能将会话标记为已读。'
      : complete
        ? `已绑定 ${bound} 个 · 未观察到 ${unresolved} 个 · 冲突 ${blocked} 个。未识别项不会猜测绑定。`
        : task.status === 'cancelled'
          ? `已取消，已完成 ${task.completed} / ${task.total} 个；已取得的可靠身份已保留。`
          : '任务已安全停止，可重新补采未完成好友。'

  return (
    <TaskDock
      kind="identity"
      account={account}
      mark="ID"
      kicker={complete ? 'IDENTITY COMPLETE' : failed ? 'IDENTITY STOPPED' : 'IDENTITY HYDRATION'}
      statusLabel={running ? `${percent}%` : complete ? '100%' : '需处理'}
      percent={percent}
      progressLabel={`${Math.min(task.completed, task.total)} / ${task.total} 位好友`}
      stage={stage}
      currentItem={currentItem}
      note={note}
      failed={failed}
      complete={complete}
      offset={offset}
      onRestore={onOpen}
      restoreLabel="查看好友页"
      onCancel={running ? onCancel : undefined}
      cancelLabel={cancelLabel ?? '取消补采'}
      cancelDisabled={cancelDisabled}
      onRetry={retryable ? onRetry : undefined}
      retryLabel="重试未完成项"
      onClose={!running ? onDismiss : undefined}
    />
  )
}
