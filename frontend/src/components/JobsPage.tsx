import { useState } from 'react'
import { api, ApiError } from '../api'
import type {
  Account,
  ContractTarget,
  DashboardSummary,
  MessageTemplate,
  SendJob,
} from '../types'
import {
  JOB_STATUS_LABELS,
  JOB_STATUS_TONES,
  formatDateOnly,
  formatDateTime,
  formatTimeHM,
} from '../lib/format'
import StatusBadge from './StatusBadge'

interface Props {
  summary: DashboardSummary
  accounts: Account[]
  targets: ContractTarget[]
  templates: MessageTemplate[]
  onChanged: () => void
}

const FAILURE_CLASS_LABELS: Record<NonNullable<SendJob['failureClass']>, string> = {
  network: '网络不可用',
  auth: '登录失效',
  verification: '需要验证',
  target_uncertain: '好友未确认',
  dom_changed: '页面结构变化',
  unknown_outcome: '结果未确认',
  other: '其他原因',
}

const TEMPLATE_TYPE_LABELS: Record<MessageTemplate['type'], string> = {
  text: '文字',
  image: '图片',
  sticker: '原生表情',
  pool: '文案池',
}

function isFailedJob(job: SendJob): boolean {
  return job.status === 'failed_retryable' || job.status === 'failed_terminal'
}

function slotVersion(slot: string): string | null {
  const match = slot.match(/:v(\d+)$/)
  return match?.[1] ?? null
}

export default function JobsPage({ summary, accounts, targets, templates, onChanged }: Props) {
  const jobs = summary.recentJobs
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const accountsById = new Map(accounts.map((account) => [account.id, account]))
  const targetsById = new Map(targets.map((target) => [target.id, target]))
  const templatesById = new Map(templates.map((template) => [template.id, template]))
  const recentCounts = jobs.reduce(
    (counts, job) => {
      if (job.status === 'succeeded') counts.succeeded += 1
      if (isFailedJob(job)) counts.failed += 1
      if (job.status === 'unknown_requires_review') counts.review += 1
      return counts
    },
    { succeeded: 0, failed: 0, review: 0 },
  )

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
        <div className="card-head jobs-card-head">
          <div className="jobs-card-heading">
            <h2>最近任务（{jobs.length}）</h2>
            <p>按最近更新时间排序 · 每行是一位好友的一次续火记录</p>
          </div>
          {jobs.length > 0 && (
            <div className="jobs-summary" aria-label="最近任务统计">
              <span className="jobs-summary-item is-success">
                <b>{recentCounts.succeeded}</b> 已续焰
              </span>
              <span className="jobs-summary-item is-failed">
                <b>{recentCounts.failed}</b> 失败
              </span>
              <span className="jobs-summary-item is-review">
                <b>{recentCounts.review}</b> 待处理
              </span>
            </div>
          )}
        </div>
        {actionError && <div className="form-error" style={{ marginBottom: 8 }}>{actionError}</div>}
        {jobs.length === 0 ? (
          <div className="empty">
            <div className="empty-title">暂无任务记录</div>
            <p className="empty-copy">完成账号登录和好友绑定后，发送任务会记录在这里。</p>
          </div>
        ) : (
          <div className="jobs-table-wrap">
            <table className="table jobs-table" aria-label="最近任务">
              <thead>
                <tr>
                  <th>发送对象</th>
                  <th>使用模板</th>
                  <th>结果</th>
                  <th>最近更新</th>
                  <th>处理</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  const account = accountsById.get(job.accountId)
                  const target = targetsById.get(job.targetId)
                  const template = templatesById.get(job.templateId)
                  const version = slotVersion(job.messageSlotId)
                  return (
                    <tr key={job.id}>
                      <td>
                        <div className="job-person">
                          <strong title={target?.nicknameSnapshot ?? job.targetId}>
                            {target?.nicknameSnapshot ?? '未知好友'}
                          </strong>
                          <span>{account?.displayName ?? '未知账号'}</span>
                        </div>
                      </td>
                      <td>
                        <div className="job-message">
                          <strong title={template?.name ?? job.templateId}>
                            {template?.name ?? '未知模板'}
                          </strong>
                          <span>
                            {template
                              ? `${TEMPLATE_TYPE_LABELS[template.type]}${version ? ` · 版本 v${version}` : ''}`
                              : '模板信息不可用'}
                          </span>
                        </div>
                      </td>
                      <td>
                        <div className="job-result">
                          <div className="job-result-main">
                            <StatusBadge
                              label={JOB_STATUS_LABELS[job.status]}
                              tone={JOB_STATUS_TONES[job.status]}
                            />
                            <span className="job-attempts">尝试 {job.attemptCount} 次</span>
                          </div>
                          {job.failureClass && (
                            <span className="job-failure">
                              {FAILURE_CLASS_LABELS[job.failureClass]}
                            </span>
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="job-time" title={formatDateTime(job.updatedAt)}>
                          <strong>{formatTimeHM(job.updatedAt)}</strong>
                          <span>业务日 {formatDateOnly(job.businessDate)}</span>
                        </div>
                      </td>
                      <td>
                        {job.status === 'unknown_requires_review' ? (
                          <span className="job-actions">
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
                              disabled={busyId === job.id}
                              onClick={() => handleRelease(job)}
                            >
                              放行重试
                            </button>
                          </span>
                        ) : (
                          <span className="muted job-no-action">无需处理</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
