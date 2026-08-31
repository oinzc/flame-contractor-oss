import type { Account } from '../types'

export type TaskDockKind = 'scan' | 'send'

interface Props {
  kind: TaskDockKind
  account: Account | undefined
  mark: string
  kicker: string
  statusLabel: string
  percent: number | null
  progressLabel: string
  stage: string
  currentItem: string
  note: string
  failed?: boolean
  complete?: boolean
  indeterminate?: boolean
  offset?: boolean
  onRestore: () => void
  restoreLabel: string
  onCancel?: () => void
  cancelLabel?: string
  cancelDisabled?: boolean
  onRetry?: () => void
  retryLabel?: string
  onClose?: () => void
  closeLabel?: string
}

function ProgressBar({
  kind,
  percent,
  indeterminate = false,
}: Pick<Props, 'kind' | 'percent' | 'indeterminate'>) {
  const boundedPercent = percent === null ? null : Math.min(Math.max(percent, 0), 100)
  const isIndeterminate = indeterminate && boundedPercent === null

  return (
    <div
      className={`task-progress-track task-progress-${kind}${isIndeterminate ? ' is-indeterminate' : ''}`}
      role="progressbar"
      aria-label={kind === 'scan' ? '会话扫描进度' : '发送进度'}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={boundedPercent ?? undefined}
      aria-valuetext={boundedPercent === null ? '正在处理' : `${boundedPercent}%`}
    >
      <span
        className="task-progress-fill"
        style={
          boundedPercent === null
            ? isIndeterminate
              ? undefined
              : { transform: 'scaleX(0)' }
            : { transform: `scaleX(${boundedPercent / 100})` }
        }
      />
    </div>
  )
}

export default function TaskDock({
  kind,
  account,
  mark,
  kicker,
  statusLabel,
  percent,
  progressLabel,
  stage,
  currentItem,
  note,
  failed = false,
  complete = false,
  indeterminate = false,
  offset = false,
  onRestore,
  restoreLabel,
  onCancel,
  cancelLabel = '取消',
  cancelDisabled = false,
  onRetry,
  retryLabel = '重新开始',
  onClose,
  closeLabel = '关闭',
}: Props) {
  return (
    <aside
      className={`task-dock task-dock-${kind}${failed ? ' is-error' : ''}${complete ? ' is-complete' : ''}${offset ? ' is-offset' : ''}`}
      aria-label={`${kind === 'scan' ? '会话扫描' : '发送'}任务 · ${account?.displayName ?? '当前账号'}`}
      aria-live="polite"
    >
      <div className="task-dock-head">
        <div className="task-dock-identity">
          <span className="task-dock-mark" aria-hidden="true">
            {mark}
          </span>
          <div>
            <span className="task-dock-kicker">{kicker}</span>
            <strong>{account?.displayName ?? '当前账号'}</strong>
          </div>
        </div>
        <span className="task-dock-state">{statusLabel}</span>
      </div>

      <div className="task-dock-progress">
        <div className="task-progress-meta">
          <span>{progressLabel}</span>
          <b>{percent === null ? '—' : `${percent}%`}</b>
        </div>
        <ProgressBar kind={kind} percent={percent} indeterminate={indeterminate} />
      </div>

      <div className="task-dock-current">
        <span className="task-live-dot" aria-hidden="true" />
        <div>
          <span>{stage}</span>
          <strong title={currentItem}>{currentItem}</strong>
        </div>
      </div>

      <p className="task-dock-note">{note}</p>

      <div className="task-dock-actions">
        <button className="btn small" type="button" onClick={onRestore}>
          {restoreLabel}
        </button>
        {onCancel && (
          <button className="btn ghost small" type="button" disabled={cancelDisabled} onClick={onCancel}>
            {cancelLabel}
          </button>
        )}
        {onRetry && (
          <button className="btn small" type="button" onClick={onRetry}>
            {retryLabel}
          </button>
        )}
        {onClose && (
          <button className="btn ghost small" type="button" onClick={onClose}>
            {closeLabel}
          </button>
        )}
      </div>
    </aside>
  )
}
