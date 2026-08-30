import { useState } from 'react'
import { api, ApiError } from '../api'
import type { DashboardSummary, SendJob } from '../types'
import { JOB_STATUS_LABELS, JOB_STATUS_TONES, formatDateTime } from '../lib/format'
import StatusBadge from './StatusBadge'

interface Props {
  summary: DashboardSummary
  onChanged: () => void
}

export default function JobsPage({ summary, onChanged }: Props) {
  const jobs = summary.recentJobs
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  async function handleConfirmSent(job: SendJob) {
    const ok = window.confirm(
      '确认这条消息已经实际发送成功？\n确认后任务按成功记录，好友火花天数 +1。',
    )
    if (!ok) return
    setActionError(null)
    setBusyId(job.id)
    try {
      await api.confirmJobSent(job.id)
      onChanged()
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '确认失败，请重试')
    } finally {
      setBusyId(null)
    }
  }

  async function handleRelease(job: SendJob) {
    const ok = window.confirm(
      '确认这条消息没有发送出去，放行重试？\n注意：若消息实际已发出，重试会导致好友收到重复消息。',
    )
    if (!ok) return
    setActionError(null)
    setBusyId(job.id)
    try {
      await api.releaseJob(job.id)
      onChanged()
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '放行失败，请重试')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="main-col">
      <div className="page-head">
        <div>
          <h1>任务记录</h1>
          <p className="sub">每次发送任务的状态与时间。待人工复核的任务可在此确认或放行。</p>
        </div>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>最近任务（{jobs.length}）</h2>
        </div>
        {actionError && <div className="form-error" style={{ marginBottom: 8 }}>{actionError}</div>}
        {jobs.length === 0 ? (
          <div className="empty">
            <div className="empty-title">暂无任务记录</div>
            <p className="empty-copy">完成账号登录和好友绑定后，发送任务会记录在这里。</p>
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
                <th>操作</th>
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
                  <td>
                    {job.status === 'unknown_requires_review' ? (
                      <span style={{ whiteSpace: 'nowrap' }}>
                        <button
                          className="btn primary small"
                          type="button"
                          disabled={busyId === job.id}
                          onClick={() => handleConfirmSent(job)}
                        >
                          确认已发送
                        </button>
                        <button
                          className="btn ghost small"
                          type="button"
                          style={{ marginLeft: 6 }}
                          disabled={busyId === job.id}
                          onClick={() => handleRelease(job)}
                        >
                          放行重试
                        </button>
                      </span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
