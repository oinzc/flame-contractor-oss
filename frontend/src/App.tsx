import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import type {
  Account,
  AccountStatus,
  ContractTarget,
  DashboardSummary,
  Health,
  MessageTemplate,
  ScanTaskState,
  SchedulerStatus,
  TargetBatchResult,
} from './types'
import CreateAccountModal from './components/CreateAccountModal'
import DashboardPage from './components/DashboardPage'
import AccountsPage from './components/AccountsPage'
import TargetsPage from './components/TargetsPage'
import TemplatesPage from './components/TemplatesPage'
import JobsPage from './components/JobsPage'
import ScanProgressDock from './components/ScanProgress'
import SendModal from './components/SendModal'

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
  const [scanTask, setScanTask] = useState<ScanTaskState | null>(null)
  const [scanStarting, setScanStarting] = useState(false)
  const [scanCancelling, setScanCancelling] = useState(false)
  const [scanDockVisible, setScanDockVisible] = useState(false)
  const scanCompletionHandledRef = useRef<string | null>(null)
  const scanRecoveryStartedRef = useRef(false)
  const [sendAccount, setSendAccount] = useState<Account | null>(null)
  const [sendMinimized, setSendMinimized] = useState(false)

  const accounts = summary?.accounts ?? []

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

  const scanTaskId = scanTask?.taskId ?? null
  const scanTaskStatus = scanTask?.status ?? null

  // 扫描任务属于 App 生命周期：好友页卸载、切到总览或任务页时，轮询不会中断。
  useEffect(() => {
    if (!scanTaskId || scanTaskStatus !== 'running') return undefined

    let cancelled = false
    let timer: number | null = null

    const poll = async () => {
      try {
        const next = await api.scanChatsProgress(scanTask!.accountId, scanTaskId)
        if (cancelled) return
        setScanTask((previous) => {
          if (!previous || previous.taskId !== scanTaskId) return previous
          return { ...previous, ...next, pollError: null }
        })
        if (next.status === 'running') {
          timer = window.setTimeout(() => void poll(), 800)
        }
      } catch (err) {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) {
          setScanTask((previous) => {
            if (!previous || previous.taskId !== scanTaskId) return previous
            return {
              ...previous,
              status: 'failed',
              stage: '扫描任务失效',
              overall: 'error',
              detail: '后端扫描任务已失效，请重新发起扫描',
              currentItem: null,
              pollError: null,
            }
          })
          return
        }
        setScanTask((previous) => {
          if (!previous || previous.taskId !== scanTaskId) return previous
          return {
            ...previous,
            pollError: '进度读取暂时失败，正在自动重连…',
          }
        })
        timer = window.setTimeout(() => void poll(), 1200)
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [scanTaskId, scanTaskStatus])

  // 页面首次加载时从 SQLite 接管最近一次扫描；running 任务已由后端收束为 interrupted。
  useEffect(() => {
    if (scanRecoveryStartedRef.current || accounts.length === 0 || scanTask !== null) {
      return undefined
    }
    scanRecoveryStartedRef.current = true
    let cancelled = false

    async function recoverLatestScan() {
      const snapshots = await Promise.all(
        accounts.map(async (account) => {
          try {
            return await api.latestScanChats(account.id)
          } catch {
            return null
          }
        }),
      )
      if (cancelled) return
      const candidate = snapshots
        .filter((snapshot): snapshot is NonNullable<typeof snapshot> => snapshot !== null)
        .sort((left, right) => {
          const leftTime = Date.parse(left.finishedAt ?? left.startedAt ?? '') || 0
          const rightTime = Date.parse(right.finishedAt ?? right.startedAt ?? '') || 0
          return rightTime - leftTime
        })[0]
      if (!candidate) return

      scanCompletionHandledRef.current = null
      setScanTask({
        ...candidate,
        syncStatus: candidate.status === 'completed' ? 'pending' : 'done',
        batchResult: null,
        syncError: null,
        pollError: null,
      })
      setScanDockVisible(true)
    }

    void recoverLatestScan()
    return () => {
      cancelled = true
    }
  }, [accounts, scanTask])

  async function finalizeScan(task: ScanTaskState) {
    const accountTargets = targets.filter((target) => target.accountId === task.accountId)
    const existingNicknames = new Set(accountTargets.map((target) => target.nicknameSnapshot))
    const existingPlatformIds = new Set(
      accountTargets
        .map((target) => target.platformUserId)
        .filter((platformUserId): platformUserId is string => Boolean(platformUserId)),
    )
    const existingEntries = task.entries.filter(
      (entry) =>
        existingNicknames.has(entry.title) ||
        (entry.platformUserId != null && existingPlatformIds.has(entry.platformUserId)),
    )
    let batchResult: TargetBatchResult = {
      created: [],
      refreshed: [],
      duplicates: [],
      failed: [],
    }

    try {
      if (existingEntries.length > 0) {
        // 扫描不会自动新增好友；只把已存在好友的官方状态快照同步回列表。
        batchResult = await api.createTargetsBatch({
          accountId: task.accountId,
          items: existingEntries.map((entry) => ({
            nickname: entry.title,
            platformUserId: entry.platformUserId,
            streakDays: entry.streakDays ?? undefined,
            sparkKind: entry.streakKind,
            sparkLabel: entry.streakLabel,
          })),
        })
      }
      await refresh()
      setScanTask((previous) => {
        if (!previous || previous.taskId !== task.taskId) return previous
        return {
          ...previous,
          syncStatus: 'done',
          batchResult,
          syncError: null,
        }
      })
    } catch (err) {
      const message =
        err instanceof ApiError
          ? `扫描完成，但刷新官方状态失败：${err.message}`
          : '扫描完成，但刷新官方状态失败，请重试'
      setScanTask((previous) => {
        if (!previous || previous.taskId !== task.taskId) return previous
        return {
          ...previous,
          syncStatus: 'failed',
          syncError: message,
        }
      })
    }
  }

  useEffect(() => {
    if (!scanTask || scanTask.status !== 'completed') return
    if (scanCompletionHandledRef.current === scanTask.taskId) return
    scanCompletionHandledRef.current = scanTask.taskId
    void finalizeScan(scanTask)
  }, [scanTask?.taskId, scanTask?.status])

  useEffect(() => {
    if (
      !scanTask ||
      !scanDockVisible ||
      scanTask.status === 'running' ||
      scanTask.syncStatus !== 'done'
    ) {
      return undefined
    }
    const timer = window.setTimeout(() => setScanDockVisible(false), 4500)
    return () => window.clearTimeout(timer)
  }, [scanTask?.taskId, scanTask?.status, scanTask?.syncStatus, scanDockVisible])

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

  async function handleStartScan(accountId: string) {
    if (scanStarting || scanCancelling || scanTask?.status === 'running') return
    setScanStarting(true)
    try {
      const initial = await api.startScanChats(accountId)
      scanCompletionHandledRef.current = null
      setScanTask({
        ...initial,
        syncStatus: 'pending',
        batchResult: null,
        syncError: null,
        pollError: null,
      })
      setScanDockVisible(true)
    } finally {
      setScanStarting(false)
    }
  }

  async function handleCancelScan() {
    if (!scanTask || scanTask.status !== 'running' || scanCancelling) return
    setScanCancelling(true)
    try {
      const next = await api.cancelScanChats(scanTask.accountId, scanTask.taskId)
      setScanTask((previous) =>
        previous && previous.taskId === next.taskId
              ? {
                  ...previous,
                  ...next,
                  syncStatus: next.status === 'completed' ? 'pending' : 'done',
                  batchResult: null,
                  syncError: null,
                  pollError: null,
            }
          : previous,
      )
      setScanDockVisible(true)
    } catch (err) {
      setScanTask((previous) =>
        previous
          ? {
              ...previous,
              pollError: err instanceof ApiError ? `取消失败：${err.message}` : '取消失败，正在重新读取进度…',
            }
          : previous,
      )
    } finally {
      setScanCancelling(false)
    }
  }

  async function handleRetryScan(accountId: string) {
    try {
      await handleStartScan(accountId)
    } catch (err) {
      setScanTask((previous) =>
        previous
          ? {
              ...previous,
              pollError: err instanceof ApiError ? `重新扫描失败：${err.message}` : '重新扫描失败，请重试',
            }
          : previous,
      )
    }
  }

  function handleOpenSend(account: Account) {
    setSendAccount(account)
    setSendMinimized(false)
  }

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
              <span className="tile-name">
                {account.displayName}
                {account.douyinNickname && (
                  <span className="tile-douyin">{account.douyinNickname}</span>
                )}
              </span>
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
                  onOpenSend={handleOpenSend}
                />
              )}
              {page === 'targets' && (
                <TargetsPage
                  accounts={summary.accounts}
                  targets={targets}
                  templates={templates}
                  onChanged={() => void refresh()}
                  scanTask={scanTask}
                  scanStarting={scanStarting}
                  onStartScan={handleStartScan}
                />
              )}
              {page === 'templates' && (
                <TemplatesPage templates={templates} onChanged={() => void refresh()} />
              )}
              {page === 'jobs' && (
                <JobsPage
                  summary={summary}
                  accounts={summary.accounts}
                  targets={targets}
                  templates={templates}
                  onChanged={() => void refresh()}
                />
              )}
            </main>
          )
        )}

        {scanTask && scanDockVisible && (
          <ScanProgressDock
            task={scanTask}
            accounts={accounts}
            onOpen={() => setPage('targets')}
            onDismiss={() => setScanDockVisible(false)}
            onCancel={() => void handleCancelScan()}
            onRetry={() => void handleRetryScan(scanTask.accountId)}
            cancelLabel={scanCancelling ? '正在取消…' : '取消扫描'}
            cancelDisabled={scanCancelling}
            offset={Boolean(sendAccount && sendMinimized)}
          />
        )}

        {sendAccount && (
          <SendModal
            account={sendAccount}
            minimized={sendMinimized}
            offset={Boolean(scanTask && scanDockVisible && sendMinimized)}
            onClose={() => {
              setSendAccount(null)
              setSendMinimized(false)
            }}
            onMinimize={() => setSendMinimized(true)}
            onRestore={() => setSendMinimized(false)}
            onChanged={() => void refresh()}
          />
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
