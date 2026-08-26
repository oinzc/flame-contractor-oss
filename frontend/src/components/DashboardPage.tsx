import type { ContractTarget, DashboardSummary } from '../types'
import {
  ATTENTION_ACCOUNT_HINTS,
  ATTENTION_ACCOUNT_STATUSES,
  JOB_STATUS_LABELS,
  JOB_STATUS_TONES,
  formatDateTime,
} from '../lib/format'
import StatusBadge from './StatusBadge'

interface Props {
  summary: DashboardSummary
  targets: ContractTarget[]
  onNavigate: (page: 'accounts' | 'targets' | 'jobs') => void
}

export default function DashboardPage({ summary, targets, onNavigate }: Props) {
  const enabledTargets = targets.filter((target) => target.enabled)
  const progress = enabledTargets.length > 0 ? summary.todaySucceeded / enabledTargets.length : 0
  const percent = Math.round(progress * 100)
  const attentionAccounts = summary.accounts.filter((account) =>
    ATTENTION_ACCOUNT_STATUSES.includes(account.status),
  )

  return (
    <>
      <div className="main-col">
        <div className="page-head">
          <div>
            <h1>总览</h1>
            <p className="sub">账号、好友与今日任务的最新状态。</p>
          </div>
        </div>

        <div className="stat-group">
          <div className="stat">
            <div className="l">账号总数</div>
            <div className="v">{summary.accountCount}</div>
          </div>
          <div className="stat">
            <div className="l">正常账号</div>
            <div className="v">{summary.healthyAccountCount}</div>
          </div>
          <div className="stat">
            <div className="l">启用好友</div>
            <div className="v">{summary.activeContractCount}</div>
          </div>
          <div className="stat">
            <div className="l">今日已发送</div>
            <div className="v hot">{summary.todaySucceeded}</div>
          </div>
        </div>

        <section className="card">
          <div className="card-head">
            <h2>好友发送状态</h2>
            <button className="link-btn" type="button" onClick={() => onNavigate('targets')}>
              管理好友 →
            </button>
          </div>
          {targets.length === 0 ? (
            <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
              还没有好友。到「好友」页添加后，这里会显示每位好友的状态。
            </p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>好友</th>
                  <th>火花天数</th>
                  <th>上次确认发送</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {targets.map((target) => (
                  <tr key={target.id}>
                    <td>{target.nicknameSnapshot}</td>
                    <td className="mono">{target.streakDays} 天</td>
                    <td className="mono">{formatDateTime(target.lastConfirmedAt)}</td>
                    <td>
                      <StatusBadge label={target.enabled ? '启用' : '暂停'} tone={target.enabled ? 'cyan' : 'default'} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="card">
          <div className="card-head">
            <h2>最近任务</h2>
            <button className="link-btn" type="button" onClick={() => onNavigate('jobs')}>
              全部记录 →
            </button>
          </div>
          {summary.recentJobs.length === 0 ? (
            <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
              暂无任务记录。调度器接入后，这里会显示每次发送的状态与时间。
            </p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>任务号</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {summary.recentJobs.map((job) => (
                  <tr key={job.id}>
                    <td className="mono">{formatDateTime(job.updatedAt)}</td>
                    <td className="mono">{job.messageSlotId}</td>
                    <td>
                      <StatusBadge
                        label={JOB_STATUS_LABELS[job.status]}
                        tone={JOB_STATUS_TONES[job.status]}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>

      <aside className="rail">
        <section className="card">
          <h2 style={{ margin: '0 0 10px', fontSize: 13.5, fontWeight: 600 }}>今日进度</h2>
          <div className="progress-meta">
            <span>
              已发送 <b>{summary.todaySucceeded}</b> / {enabledTargets.length}
            </span>
            <span>{percent}%</span>
          </div>
          <div className="progress-bar">
            <span style={{ width: `${percent}%` }} />
          </div>
          <p className="muted" style={{ fontSize: 11.5, margin: '8px 0 0' }}>
            节点对应启用中的好友，进度来自已确认发送的任务。
          </p>
        </section>

        <section className="card">
          <h2 style={{ margin: '0 0 10px', fontSize: 13.5, fontWeight: 600 }}>
            待处理（{summary.needsAttention}）
          </h2>
          {attentionAccounts.length === 0 ? (
            <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
              没有需要处理的事项。
            </p>
          ) : (
            <div style={{ display: 'grid', gap: 8 }}>
              {attentionAccounts.map((account) => (
                <div className="rail-item" key={account.id}>
                  <span className="t">{account.displayName}</span>
                  <span className="s">{ATTENTION_ACCOUNT_HINTS[account.status]}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="card">
          <h2 style={{ margin: '0 0 10px', fontSize: 13.5, fontWeight: 600 }}>本机边界</h2>
          <ul className="muted" style={{ fontSize: 12, margin: 0, paddingLeft: 16 }}>
            <li>登录凭据只存在本机浏览器 Profile，不入库、不上传。</li>
            <li>验证码与安全验证只允许本人处理，不自动绕过。</li>
          </ul>
        </section>
      </aside>
    </>
  )
}
