import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ContractTarget, DashboardSummary, SchedulerStatus, StatsSummary } from '../types'
import {
  ATTENTION_ACCOUNT_HINTS,
  ATTENTION_ACCOUNT_STATUSES,
  SCHEDULE_STATUS_LABELS,
  formatTimeHM,
  parseBackendTime,
} from '../lib/format'
import SparkBadge from './SparkBadge'

interface Props {
  summary: DashboardSummary
  targets: ContractTarget[]
  schedulerStatus: SchedulerStatus | null
  now: Date
  onToggleScheduler: () => void
  onNavigate: (page: 'accounts' | 'targets' | 'jobs') => void
}

function hhmmToFraction(value: string): number {
  const [hour, minute] = value.split(':').map(Number)
  return (hour + minute / 60) / 24
}

function isoToFraction(value: string | null): number | null {
  if (!value) return null
  const date = parseBackendTime(value)
  if (Number.isNaN(date.getTime())) return null
  return (date.getHours() + date.getMinutes() / 60 + date.getSeconds() / 3600) / 24
}

export default function DashboardPage({
  summary,
  targets,
  schedulerStatus,
  now,
  onToggleScheduler,
  onNavigate,
}: Props) {
  const [stats, setStats] = useState<StatsSummary | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .statsSummary()
      .then((payload) => {
        if (!cancelled) setStats(payload)
      })
      .catch(() => {
        /* 统计失败不打扰驾驶舱 */
      })
    return () => {
      cancelled = true
    }
  }, [summary])

  const enabledTargets = targets.filter((target) => target.enabled)
  const succeededToday = summary.todaySucceeded
  const totalToday = Math.max(enabledTargets.length, 1)
  const ledCount = Math.min(enabledTargets.length, 46)
  const ledLit = Math.round((succeededToday / totalToday) * ledCount)
  const nowFraction =
    (now.getHours() + now.getMinutes() / 60 + now.getSeconds() / 3600) / 24
  const attentionAccounts = summary.accounts.filter((account) =>
    ATTENTION_ACCOUNT_STATUSES.includes(account.status),
  )
  const rankedTargets = [...targets].sort((a, b) => b.streakDays - a.streakDays)
  const maxStreak = Math.max(1, ...targets.map((target) => target.streakDays))

  const scheduleBands = summary.accounts.map((account) => ({
    id: account.id,
    start: hhmmToFraction(account.sendWindowStart),
    end: hhmmToFraction(account.sendWindowEnd),
  }))

  const schedulePoints = (schedulerStatus?.todaySchedules ?? []).map((schedule) => ({
    id: schedule.id,
    fraction: isoToFraction(schedule.scheduledAt),
    status: schedule.status,
    name:
      summary.accounts.find((account) => account.id === schedule.accountId)?.displayName ??
      schedule.accountId.slice(0, 6),
  }))

  return (
    <div className="bento board" style={{ margin: 18 }}>
      {/* MOD·01 今日之焰 */}
      <section className="mod mod-flame" aria-label="今日之焰">
        <div className="mod-tag">
          <span>MOD·01 今日之焰</span>
          <span>TODAY</span>
        </div>
        <div className="flame-core">
          <span className="flame-mark" aria-hidden="true">
            🔥
          </span>
          <div className="flame-number">
            {succeededToday}
            <span className="of">/ {enabledTargets.length}</span>
          </div>
          <div className="flame-caption">
            今日已续焰 · {summary.healthyAccountCount} 账号在编
            {schedulerStatus?.paused ? ' · 调度已暂停' : ''}
          </div>
          <div className="led-strip" aria-hidden="true">
            {Array.from({ length: ledCount }).map((_, index) => (
              <span
                key={index}
                className={`led-cell${index < ledLit ? ' lit' : ''}`}
                style={index < ledLit ? { animationDelay: `${index * 14}ms` } : undefined}
              />
            ))}
          </div>
        </div>
      </section>

      {/* MOD·02 24h 调度标尺 */}
      <section className="mod mod-ruler" aria-label="24h 调度标尺">
        <div className="mod-tag">
          <span>MOD·02 调度标尺</span>
          <span>24H</span>
        </div>
        <div className="ruler-wrap">
          <div className="ruler">
            {scheduleBands.map((band) => (
              <div
                key={band.id}
                className="ruler-band"
                style={{ left: `${band.start * 100}%`, width: `${(band.end - band.start) * 100}%` }}
              />
            ))}
            {schedulePoints.map((point) =>
              point.fraction === null ? null : (
                <span
                  key={point.id}
                  className={`ruler-point ${
                    point.status === 'succeeded'
                      ? 'sent'
                      : point.status === 'failed' || point.status === 'unknown_requires_review'
                        ? 'failed'
                        : point.status === 'skipped'
                          ? 'skipped'
                          : 'pending'
                  }`}
                  style={{ left: `${point.fraction * 100}%` }}
                  title={`${point.name} · ${point.status}`}
                />
              ),
            )}
            <span className="ruler-now" style={{ left: `${nowFraction * 100}%` }} />
          </div>
          <div className="ruler-axis">
            <span>00:00</span>
            <span>06:00</span>
            <span>12:00</span>
            <span>18:00</span>
            <span>24:00</span>
          </div>
        </div>

        <div className="mini-rows">
          {schedulerStatus === null ? (
            <div className="mini-row">
              <span className="name">无法读取调度状态</span>
            </div>
          ) : schedulerStatus.todaySchedules.length === 0 ? (
            <div className="mini-row">
              <span className="name">今日暂无自动调度计划</span>
              <span className="state idle">—</span>
            </div>
          ) : (
            schedulerStatus.todaySchedules.map((schedule) => {
              const name =
                summary.accounts.find((account) => account.id === schedule.accountId)
                  ?.displayName ?? schedule.accountId.slice(0, 6)
              const stateClass =
                schedule.status === 'succeeded'
                  ? 'ok'
                  : schedule.status === 'pending' || schedule.status === 'sending'
                    ? 'warn'
                    : schedule.status === 'skipped'
                      ? 'idle'
                      : 'bad'
              return (
                <div className="mini-row" key={schedule.id}>
                  <span
                    className={`lamp ${stateClass === 'ok' ? '' : stateClass === 'warn' ? '' : ''}`}
                    style={{
                      background:
                        stateClass === 'ok'
                          ? 'var(--flame)'
                          : stateClass === 'warn'
                            ? 'var(--amber)'
                            : stateClass === 'bad'
                              ? 'var(--red)'
                              : 'var(--text-3)',
                    }}
                  />
                  <span className="name">{name}</span>
                  <span className="time">{formatTimeHM(schedule.scheduledAt)}</span>
                  <span className={`state ${stateClass}`}>
                    {SCHEDULE_STATUS_LABELS[schedule.status]}
                  </span>
                </div>
              )
            })
          )}
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <button
            className={`btn small ${schedulerStatus?.paused ? 'primary' : ''}`}
            type="button"
            onClick={onToggleScheduler}
            disabled={schedulerStatus === null}
          >
            {schedulerStatus?.paused ? '恢复自动调度' : '暂停自动调度'}
          </button>
          {schedulerStatus && schedulerStatus.runningAccounts.length > 0 && (
            <span className="flame-caption" style={{ alignSelf: 'center' }}>
              发送中：{schedulerStatus.runningAccounts.join('、')}
            </span>
          )}
        </div>
      </section>

      {/* MOD·05 统计 */}
      <section className="mod mod-stats" aria-label="统计与导出">
        <div className="mod-tag">
          <span>MOD·05 统计</span>
          <span>30D</span>
        </div>
        <div className="stat-blocks">
          <div className="stat-block">
            <span className="l">成功率</span>
            <span className="v hot">
              {stats?.successRate === null || stats?.successRate === undefined
                ? '—'
                : `${Math.round(stats.successRate * 100)}%`}
            </span>
          </div>
          <div className="stat-block">
            <span className="l">连燃</span>
            <span className="v">{stats?.currentSuccessStreak ?? '—'}</span>
          </div>
          <div className="stat-block">
            <span className="l">最长</span>
            <span className="v">{stats?.bestSuccessStreak ?? '—'}</span>
          </div>
          <div className="stat-block">
            <span className="l">任务</span>
            <span className="v">{stats?.totalJobs ?? '—'}</span>
          </div>
        </div>
        <div className="side-actions">
          <a className="btn small" href="/api/stats/export.csv" style={{ justifyContent: 'center' }}>
            导出任务 CSV
          </a>
          <button
            className="btn small"
            type="button"
            onClick={() => void api.downloadBackup()}
          >
            备份全部数据
          </button>
        </div>
        {attentionAccounts.length > 0 && (
          <div style={{ marginTop: 12 }}>
            {attentionAccounts.map((account) => (
              <div className="mini-row" key={account.id}>
                <span className="lamp" style={{ background: 'var(--red)' }} />
                <span className="name">{account.displayName}</span>
                <span className="state bad">
                  {ATTENTION_ACCOUNT_HINTS[account.status]?.slice(0, 12) ?? '需要处理'}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* MOD·03 好友火花榜 */}
      <section className="mod mod-streak" aria-label="好友火花榜">
        <div className="mod-tag">
          <span>MOD·03 好友火花榜</span>
          <span onClick={() => onNavigate('targets')} style={{ cursor: 'pointer' }}>
            管理 →
          </span>
        </div>
        {rankedTargets.length === 0 ? (
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>
            还没有契约好友。到「好友」页添加后，这里会按火花天数排行。
          </p>
        ) : (
          <div className="streak-rows">
            {rankedTargets.map((target) => (
              <div className="streak-row" key={target.id}>
                <span className="who">
                  {target.nicknameSnapshot}
                  {!target.enabled && <span className="off">已暂停</span>}
                </span>
                <span className="streak-bar-track">
                  <span
                    className="streak-bar"
                    style={{ width: `${(target.streakDays / maxStreak) * 100}%` }}
                  />
                </span>
                <span className="days">
                  <SparkBadge
                    days={target.streakDays}
                    kind={target.sparkKind}
                    label={target.sparkLabel}
                    scannedAt={target.sparkScannedAt}
                  />
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* MOD·04 任务流水 */}
      <section className="mod mod-stream" aria-label="任务流水">
        <div className="mod-tag">
          <span>MOD·04 任务流水</span>
          <span onClick={() => onNavigate('jobs')} style={{ cursor: 'pointer' }}>
            全部 →
          </span>
        </div>
        {summary.recentJobs.length === 0 ? (
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>
            暂无任务记录。
          </p>
        ) : (
          <div className="stream">
            {summary.recentJobs.map((job) => {
              const ok = job.status === 'succeeded'
              const bad =
                job.status === 'failed_retryable' ||
                job.status === 'failed_terminal' ||
                job.status === 'unknown_requires_review'
              const target = targets.find((item) => item.id === job.targetId)
              const account = summary.accounts.find((item) => item.id === job.accountId)
              const targetName = target?.nicknameSnapshot ?? '未知好友'
              return (
                <div className="stream-row" key={job.id}>
                  <span className="ts">{formatTimeHM(job.updatedAt)}</span>
                  <span
                    className="what"
                    title={`账号：${account?.displayName ?? '未知账号'} · 好友：${targetName}`}
                  >
                    {targetName}
                  </span>
                  <span className={`st ${ok ? 'ok' : bad ? (job.status === 'unknown_requires_review' ? 'warn' : 'bad') : 'idle'}`}>
                    {job.status === 'succeeded'
                      ? 'OK'
                      : job.status === 'unknown_requires_review'
                        ? 'REVIEW'
                        : job.status === 'failed_retryable'
                          ? 'RETRY'
                          : job.status === 'failed_terminal'
                            ? 'FAIL'
                            : job.status.toUpperCase()}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
