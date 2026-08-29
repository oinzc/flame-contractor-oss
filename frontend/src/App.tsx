import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from './api'
import type {
  Account,
  AccountStatus,
  ContractTarget,
  DashboardSummary,
  Health,
  MessageTemplate,
  SchedulerStatus,
} from './types'
import CreateAccountModal from './components/CreateAccountModal'
import DashboardPage from './components/DashboardPage'
import AccountsPage from './components/AccountsPage'
import TargetsPage from './components/TargetsPage'
import TemplatesPage from './components/TemplatesPage'
import JobsPage from './components/JobsPage'

type Page = 'dashboard' | 'accounts' | 'targets' | 'templates' | 'jobs'

const NAV_ITEMS: { key: Page; ico: string; label: string }[] = [
  { key: 'dashboard', ico: '01', label: '总览' },
  { key: 'jobs', ico: '02', label: '任务' },
  { key: 'accounts', ico: '03', label: '账号' },
  { key: 'targets', ico: '04', label: '好友' },
  { key: 'templates', ico: '05', label: '模板' },
]

const LAMP_TONES: Record<AccountStatus, string> = {
  pending_login: 'amber',
  healthy: 'green',
  running: 'green',
  paused: 'amber',
  auth_expired: 'red',
  verification_required: 'red',
  dom_broken: 'red',
  cooldown: 'amber',
}

function lampClass(status: AccountStatus): string {
  const tone = LAMP_TONES[status] ?? 'amber'
  return `status-lamp ${tone}`
}

export default function App() {
  const [page, setPage] = useState<Page>('dashboard')
  const [health, setHealth] = useState<Health | null>(null)
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [targets, setTargets] = useState<ContractTarget[]>([])
  const [templates, setTemplates] = useState<MessageTemplate[]>([])
  const [schedulerStatus, setSchedulerStatus] = useState<SchedulerStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [now, setNow] = useState(() => new Date())

  const refresh = useCallback(async () => {
    try {
      const [nextHealth, nextSummary, nextTargets, nextTemplates, nextScheduler] =
        await Promise.all([
          api.health(),
          api.dashboard(),
          api.targets(),
          api.templates(),
          api.schedulerStatus(),
        ])
      setHealth(nextHealth)
      setSummary(nextSummary)
      setTargets(nextTargets)
      setTemplates(nextTemplates)
      setSchedulerStatus(nextScheduler)
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '无法连接本机控制面')
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 驾驶舱活性：15s 轮询刷新（页面隐藏时暂停），NOW 时钟 1s 走秒
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh()
    }, 15000)
    return () => window.clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) setNow(new Date())
    }, 1000)
    return () => window.clearInterval(timer)
  }, [])

  async function handleCreateAccount(input: Parameters<typeof api.createAccount>[0]) {
    await api.createAccount(input)
    setShowCreate(false)
    await refresh()
  }

  async function handleToggleScheduler() {
    if (!schedulerStatus) return
    if (schedulerStatus.paused) {
      await api.schedulerResume()
    } else {
      await api.schedulerPause()
    }
    await refresh()
  }

  const accounts = summary?.accounts ?? []
  const loading = health === null && error === null

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="side-brand">
          <span className="side-glyph" aria-hidden="true">
            焰
          </span>
          <div>
            <span className="name">焰之契约者</span>
            <span className="sub-name">FLAME CONSOLE</span>
          </div>
        </div>

        <div className="side-section-label">账号总线 / BUS</div>
        <div className="account-tiles">
          {accounts.map((account: Account) => (
            <button
              key={account.id}
              type="button"
              className="account-tile"
              title={`${account.displayName} · ${account.sendWindowStart}–${account.sendWindowEnd}`}
              onClick={() => setPage('accounts')}
            >
              <span className={lampClass(account.status)} aria-hidden="true" />
              <span className="tile-name">{account.displayName}</span>
              <span className="tile-window">
                {account.sendWindowStart.slice(0, 2)}时
              </span>
            </button>
          ))}
          <button className="tile-add" type="button" onClick={() => setShowCreate(true)}>
            + 新建账号
          </button>
        </div>

        <nav className="side-nav" aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`side-nav-item${page === item.key ? ' active' : ''}`}
              onClick={() => setPage(item.key)}
            >
              <span className="nav-ico" aria-hidden="true">
                {item.ico}
              </span>
              <span className="nav-label">{item.label}</span>
            </button>
          ))}
        </nav>

        <div className="engine-clock">
          <span className="health-dot">
            <span
              className={`status-lamp ${health?.database === 'ready' ? 'green' : 'red'}`}
              aria-hidden="true"
            />
            {health?.database === 'ready' ? 'ENGINE' : 'OFFLINE'}
          </span>
          <span>{now.toLocaleTimeString('zh-CN', { hour12: false })}</span>
        </div>
      </aside>

      <div className="main">
        {error && (
          <div className="error-banner" role="alert">
            <span>
              控制面不可达：{error}。请确认后端已运行（backend 目录：.venv\Scripts\python -m uvicorn
              app.main:app --host 127.0.0.1 --port 8000）。
            </span>
            <button className="btn small" type="button" onClick={() => void refresh()}>
              重试
            </button>
          </div>
        )}

        {loading ? (
          <div className="loading" aria-live="polite">
            CONNECTING CONTROL PLANE…
          </div>
        ) : (
          summary && (
            <main className="content">
              {page === 'dashboard' && (
                <DashboardPage
                  summary={summary}
                  targets={targets}
                  schedulerStatus={schedulerStatus}
                  now={now}
                  onToggleScheduler={() => void handleToggleScheduler()}
                  onNavigate={setPage}
                />
              )}
              {page === 'accounts' && (
                <AccountsPage
                  accounts={summary.accounts}
                  onChanged={() => void refresh()}
                  onCreateAccount={() => setShowCreate(true)}
                />
              )}
              {page === 'targets' && (
                <TargetsPage
                  accounts={summary.accounts}
                  targets={targets}
                  templates={templates}
                  onChanged={() => void refresh()}
                />
              )}
              {page === 'templates' && (
                <TemplatesPage templates={templates} onChanged={() => void refresh()} />
              )}
              {page === 'jobs' && <JobsPage summary={summary} />}
            </main>
          )
        )}

        {!loading && (
          <footer className="footer-note">
            本机优先 · 登录凭据只保存在本机浏览器 Profile，不入库、不上传
          </footer>
        )}

        {showCreate && (
          <CreateAccountModal
            onSubmit={handleCreateAccount}
            onClose={() => setShowCreate(false)}
          />
        )}
      </div>
    </div>
  )
}
