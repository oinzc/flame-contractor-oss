import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../api'
import type {
  Account,
  ContractTarget,
  JobsPageData,
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
  accounts: Account[]
  targets: ContractTarget[]
  templates: MessageTemplate[]
  onChanged: () => void
}

const PAGE_SIZE = 20

/** 筛选 chips：key 为 /api/jobs?status= 参数（空串 = 全部），失败合并可重试与终态两类。 */
const STATUS_FILTERS: { key: string; label: string }[] = [
  { key: '', label: '全部' },
  { key: 'succeeded', label: '已续焰' },
  { key: 'failed_retryable,failed_terminal', label: '失败' },
  { key: 'unknown_requires_review', label: '待复核' },
]

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

function slotVersion(slot: string): string | null {
  const match = slot.match(/:v(\d+)$/)
  return match?.[1] ?? null
}

export default function JobsPage({ accounts, targets, templates, onChanged }: Props) {
  const [data, setData] = useState<JobsPageData | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [accountFilter, setAccountFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const [reloadKey, setReloadKey] = useState(0)

  const refresh = useCallback(() => {
    setReloadKey((key) => key + 1)
  }, [])

  const accountsById = useMemo(() => new Map(accounts.map((a) => [a.id, a])), [accounts])
  const targetsById = useMemo(() => new Map(targets.map((t) => [t.id, t])), [targets])
  const templatesById = useMemo(() => new Map(templates.map((t) => [t.id, t])), [templates])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .listJobs({
        limit: PAGE_SIZE,
        offset,
        accountId: accountFilter || undefined,
        status: statusFilter || undefined,
      })
      .then((page) => {
        if (cancelled) return
        setData(page)
        setLoadError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setLoadError(err instanceof ApiError ? err.message : '加载失败，请重试')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [accountFilter, statusFilter, offset, reloadKey])

  const jobs = data?.items ?? []
  const total = data?.total ?? 0
  const statusCounts = data?.statusCounts ?? {}
  const failedCount = (statusCounts.failed_retryable ?? 0) + (statusCounts.failed_terminal ?? 0)
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const pageNo = Math.floor(offset / PAGE_SIZE) + 1

  function pickStatus(key: string) {
    if (statusFilter === key) return
    setStatusFilter(key)
    setOffset(0)
  }

  async function handleConfirmSent(job: SendJob) {
    const ok = window.confirm(
      '确认这条消息已经实际发送成功？\n确认后任务按成功记录，好友火花天数 +1。',
    )
    if (!ok) return
    setActionError(null)
    setBusyId(job.id)
    try {
      await api.confirmJobSent(job.id)
      refresh()
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
      refresh()
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
            <h2>任务记录</h2>
            <p>按最近更新时间排序 · 每行是一位好友的一次续火记录</p>
          </div>
          {loading && <span className="jobs-loading">加载中…</span>}
        </div>
        <div className="jobs-filter">
          <div className="field jobs-filter-account">
            <label htmlFor="jobs-account">账号</label>
            <select
              id="jobs-account"
              value={accountFilter}
              onChange={(event) => {
                setAccountFilter(event.target.value)
                setOffset(0)
              }}
            >
              <option value="">全部账号</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.displayName}
                </option>
              ))}
            </select>
          </div>
          <div className="jobs-status-filter" role="group" aria-label="按结果筛选">
            {STATUS_FILTERS.map((filter) => {
              const count =
                filter.key === ''
                  ? total
                  : filter.key === 'failed_retryable,failed_terminal'
                    ? failedCount
                    : (statusCounts[filter.key] ?? 0)
              const active = statusFilter === filter.key
              return (
                <button
                  key={filter.label}
                  type="button"
                  className={`jobs-filter-chip${active ? ' is-active' : ''}`}
                  aria-pressed={active}
                  onClick={() => pickStatus(filter.key)}
                >
                  {filter.label}
                  <b>{count}</b>
                </button>
              )
            })}
          </div>
        </div>
        {loadError && <div className="form-error" style={{ marginBottom: 8 }}>{loadError}</div>}
        {actionError && <div className="form-error" style={{ marginBottom: 8 }}>{actionError}</div>}
        {jobs.length === 0 && !loading ? (
          <div className="empty">
            <div className="empty-title">没有匹配的任务记录</div>
            <p className="empty-copy">
              {total === 0 && !statusFilter && !accountFilter
                ? '完成账号登录和好友绑定后，发送任务会记录在这里。'
                : '当前筛选条件下没有任务，试试切换状态或账号。'}
            </p>
          </div>
        ) : (
          <div className="jobs-table-wrap">
            <table className="table jobs-table" aria-label="发送任务记录">
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
        <div className="jobs-pager">
          <span className="jobs-pager-info">
            共 {total} 条 · 第 {pageNo} / {pageCount} 页
          </span>
          <span className="jobs-pager-actions">
            <button
              className="btn ghost small"
              type="button"
              disabled={offset === 0 || loading}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              上一页
            </button>
            <button
              className="btn ghost small"
              type="button"
              disabled={offset + PAGE_SIZE >= total || loading}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              下一页
            </button>
          </span>
        </div>
      </section>
    </div>
  )
}
