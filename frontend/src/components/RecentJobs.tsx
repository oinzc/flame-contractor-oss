import type { SendJob } from '../types'
import { JOB_STATUS_LABELS, JOB_STATUS_TONES, formatDateTime } from '../lib/format'
import StatusBadge from './StatusBadge'

interface Props {
  jobs: SendJob[]
}

export default function RecentJobs({ jobs }: Props) {
  if (jobs.length === 0) {
    return (
      <div className="muted" style={{ fontSize: 12 }}>
        调度器尚未接入——这里将显示每日任务、发送终态与确认时间。
      </div>
    )
  }

  return (
    <div className="row-list">
      {jobs.map((job) => (
        <div className="row-item" key={job.id}>
          <div className="row-main">
            <div className="row-title">
              {formatDateTime(job.updatedAt)}
              <span className="stage-chip">{job.messageSlotId}</span>
            </div>
            <div className="row-sub">
              契约日 {job.businessDate} · 尝试 {job.attemptCount} 次
            </div>
          </div>
          <StatusBadge
            label={JOB_STATUS_LABELS[job.status]}
            tone={JOB_STATUS_TONES[job.status]}
          />
        </div>
      ))}
    </div>
  )
}
