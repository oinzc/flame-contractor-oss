import type { DashboardSummary } from '../types'
import { JOB_STATUS_LABELS, JOB_STATUS_TONES, formatDateTime } from '../lib/format'
import StatusBadge from './StatusBadge'

interface Props {
  summary: DashboardSummary
}

export default function JobsPage({ summary }: Props) {
  const jobs = summary.recentJobs

  return (
    <div className="main-col">
      <div className="page-head">
        <div>
          <h1>任务记录</h1>
          <p className="sub">每次发送任务的状态与时间。调度器接入后，这里会展示完整记录。</p>
        </div>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>最近任务（{jobs.length}）</h2>
        </div>
        {jobs.length === 0 ? (
          <div className="empty">
            <div className="empty-title">暂无任务记录</div>
            <p className="empty-copy">当前版本还没有接入调度器，完成账号登录和好友绑定后即可进入下一阶段。</p>
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>契约日期</th>
                <th>任务号</th>
                <th>尝试次数</th>
                <th>状态</th>
                <th>更新时间</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td className="mono">{job.businessDate}</td>
                  <td className="mono">{job.messageSlotId}</td>
                  <td className="mono">{job.attemptCount}</td>
                  <td>
                    <StatusBadge
                      label={JOB_STATUS_LABELS[job.status]}
                      tone={JOB_STATUS_TONES[job.status]}
                    />
                  </td>
                  <td className="mono">{formatDateTime(job.updatedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
