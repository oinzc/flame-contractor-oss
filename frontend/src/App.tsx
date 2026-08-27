import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from './api'
import type {
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

const NAV_ITEMS: { key: Page; label: string }[] = [
  { key: 'dashboard', label: '总览' },
  { key: 'accounts', label: '账号' },
  { key: 'targets', label: '好友' },
  { key: 'templates', label: '模板' },
  { key: 'jobs', label: '任务记录' },
]

export default function App() {
  const [page, setPage] = useState<Page>('dashboard')
  const [health, setHealth] = useState<Health | null>(null)
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [targets, setTargets] = useState<ContractTarget[]>([])
  const [templates, setTemplates] = useState<MessageTemplate[]>([])
  const [schedulerStatus, setSchedulerStatus] = useState<SchedulerStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  const refresh = useCallback(async () => {
    setRefreshing(true)
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

  async function handleToggleScheduler() {
    if (!schedulerStatus) return
    if (schedulerStatus.paused) {
      await api.schedulerResume()
    } else {
      await api.schedulerPause()
    }
    await refresh()
  }

  const loading = health === null && error === null

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="glyph" aria-hidden="true">
            焰
          </span>
          <span className="name">焰之契约者</span>
        </div>
        <nav className="nav">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`nav-item${page === item.key ? ' active' : ''}`}
              onClick={() => setPage(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="topbar-right">
          <span className="health">
            <span className={`dot${health?.database === 'ready' ? '' : ' degraded'}`} />
            {health?.database === 'ready' ? '控制面正常' : '控制面未连接'}
          </span>
          <button className="btn primary small" type="button" onClick={() => setShowCreate(true)}>
            新建账号
          </button>
        </div>
      </header>

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
          {refreshing ? '连接控制面…' : '等待控制面…'}
        </div>
      ) : (
        summary && (
          <main className="content">
            {page === 'dashboard' && (
              <DashboardPage
                summary={summary}
                targets={targets}
                schedulerStatus={schedulerStatus}
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
  )
}
