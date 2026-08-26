import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from './api'
import type { Account, ContractTarget, DashboardSummary, Health } from './types'
import TopBar from './components/TopBar'
import FlameLine from './components/FlameLine'
import AccountRail from './components/AccountRail'
import AttentionPanel from './components/AttentionPanel'
import RecentJobs from './components/RecentJobs'
import CreateAccountPanel from './components/CreateAccountPanel'

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [targets, setTargets] = useState<ContractTarget[]>([])
  const [error, setError] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  const refresh = useCallback(async () => {
    setRefreshing(true)
    try {
      const [nextHealth, nextSummary, nextTargets] = await Promise.all([
        api.health(),
        api.dashboard(),
        api.targets(),
      ])
      setHealth(nextHealth)
      setSummary(nextSummary)
      setTargets(nextTargets)
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '无法连接本机控制面')
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function handleCreateAccount(input: Parameters<typeof api.createAccount>[0]) {
    await api.createAccount(input)
    setShowCreate(false)
    await refresh()
  }

  async function handleToggleAccount(account: Account) {
    await api.patchAccount(account.id, { enabled: !account.enabled })
    await refresh()
  }

  const loading = health === null && error === null

  return (
    <div className="app">
      <TopBar
        databaseReady={health?.database === 'ready'}
        onCreateAccount={() => setShowCreate(true)}
      />

      {error && (
        <div className="error-banner" role="alert">
          <span>
            控制面不可达：{error}。请确认后端已在 backend 目录运行（.venv\Scripts\python -m
            uvicorn app.main:app --host 127.0.0.1 --port 8000）。
          </span>
          <button className="btn ghost small" type="button" onClick={() => void refresh()}>
            重试
          </button>
        </div>
      )}

      {loading ? (
        <div className="loading" aria-live="polite">
          {refreshing ? '连接控制面…' : '等待控制面…'}
        </div>
      ) : (
        summary && (
          <main className="main">
            <div className="column">
              {showCreate && (
                <CreateAccountPanel
                  onSubmit={handleCreateAccount}
                  onCancel={() => setShowCreate(false)}
                />
              )}

              <section className="panel">
                <h2 className="panel-title">今日焰线</h2>
                <FlameLine targets={targets} succeeded={summary.todaySucceeded} />
              </section>

              <section className="panel">
                <h2 className="panel-title">账号炉心</h2>
                <p className="panel-sub">
                  {summary.accountCount} 个账号 · 状态与焰窗一览
                </p>
                <AccountRail
                  accounts={summary.accounts}
                  onToggle={handleToggleAccount}
                  onCreateAccount={() => setShowCreate(true)}
                  onChanged={() => void refresh()}
                />
              </section>
            </div>

            <aside className="column">
              <section className="panel">
                <h2 className="panel-title">待处理</h2>
                <p className="panel-sub">{summary.needsAttention} 项需要你的关注</p>
                <AttentionPanel accounts={summary.accounts} />
              </section>

              <section className="panel">
                <h2 className="panel-title">最近任务</h2>
                <p className="panel-sub">
                  今日已续 {summary.todaySucceeded} · 待执行 {summary.todayPending}
                </p>
                <RecentJobs jobs={summary.recentJobs} />
              </section>

              <section className="panel">
                <h2 className="panel-title">本机边界</h2>
                <ul className="muted" style={{ fontSize: 12, margin: 0, paddingLeft: 18 }}>
                  <li>骨架版本：调度器与抖音执行器尚未接入。</li>
                  <li>数据库不保存 Cookie、密码或 Storage State。</li>
                  <li>登录失效与验证码只允许本人处理，不自动绕过。</li>
                </ul>
              </section>
            </aside>
          </main>
        )
      )}

      {!loading && (
        <footer className="footer-note">
          Flame Contractor 0.1 · 本地优先 · 账号凭据永不进入数据库与日志
        </footer>
      )}
    </div>
  )
}
