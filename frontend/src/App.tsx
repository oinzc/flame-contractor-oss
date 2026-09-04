import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, getToken, setToken } from './api'
import type {
  Account,
  AccountStatus,
  ContractTarget,
  DashboardSummary,
  Health,
  IdentityHydrationTaskState,
  MessageTemplate,
  ScanTaskState,
  SchedulerStatus,
  TargetBatchResult,
  User,
} from './types'
import CreateAccountModal from './components/CreateAccountModal'
import DashboardPage from './components/DashboardPage'
import AccountsPage from './components/AccountsPage'
import TargetsPage from './components/TargetsPage'
import TemplatesPage from './components/TemplatesPage'
import JobsPage from './components/JobsPage'
import ScanProgressDock from './components/ScanProgress'
import SendModal from './components/SendModal'
import SendTargetModal from './components/SendTargetModal'
import IdentityHydrationProgress from './components/IdentityHydrationProgress'
import LoginPage from './components/LoginPage'

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
  const [currentUser, setCurrentUser] = useState<User | null>(null)
  const [authInitializing, setAuthInitializing] = useState(() => getToken() !== null)
  const [health, setHealth] = useState<Health | null>(null)
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [targets, setTargets] = useState<ContractTarget[]>([])
  const [templates, setTemplates] = useState<MessageTemplate[]>([])
  const [schedulerStatus, setSchedulerStatus] = useState<SchedulerStatus | null>(null)
  const [autoScanIntervalSaving, setAutoScanIntervalSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [now, setNow] = useState(() => new Date())
  const [scanTask, setScanTask] = useState<ScanTaskState | null>(null)
  const [scanStarting, setScanStarting] = useState(false)
  const [scanCancelling, setScanCancelling] = useState(false)
  const [scanDockVisible, setScanDockVisible] = useState(false)
  const scanCompletionHandledRef = useRef<string | null>(null)
  const scanRecoveryStartedRef = useRef(false)
  const [autoScanTask, setAutoScanTask] = useState<ScanTaskState | null>(null)
  const [autoScanDockVisible, setAutoScanDockVisible] = useState(false)
  const autoScanCompletionHandledRef = useRef<string | null>(null)
  const [identityTask, setIdentityTask] = useState<IdentityHydrationTaskState | null>(null)
  const [identityStarting, setIdentityStarting] = useState(false)
  const [identityCancelling, setIdentityCancelling] = useState(false)
  const [identityDockVisible, setIdentityDockVisible] = useState(false)
  const identityRecoveryStartedRef = useRef(false)
  const identityCompletionHandledRef = useRef<string | null>(null)
  const [sendAccount, setSendAccount] = useState<Account | null>(null)
  const [sendMinimized, setSendMinimized] = useState(false)
  const [sendForce, setSendForce] = useState(false)
  const [sendTarget, setSendTarget] = useState<{ account: Account; target: ContractTarget } | null>(null)

  const accounts = summary?.accounts ?? []
  const visibleDockKeys = [
    sendAccount && sendMinimized ? 'send' : null,
    autoScanTask && autoScanDockVisible ? 'auto-scan' : null,
    scanTask && scanDockVisible ? 'scan' : null,
    identityTask && identityDockVisible ? 'identity' : null,
  ].filter((key): key is string => key !== null)
  const dockOffset = (key: string): number => {
    const index = visibleDockKeys.indexOf(key)
    return 20 + Math.max(index, 0) * 216
  }

  const refresh = useCallback(async () => {
    // 未登录（无 token）不拉取任何业务数据，避免每次涌入 401
    if (!getToken()) return
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
      if (err instanceof ApiError && err.status === 401) {
        // 登录失效：清 token 回到登录页，避免不断重试
        setToken(null)
        setCurrentUser(null)
        setAuthInitializing(false)
        return
      }
      setError(err instanceof ApiError ? err.message : '无法连接本机控制面')
    }
  }, [])

  // 首次进入：若有 token 则验证并拉取当前用户；无 token 直接进登录页
  useEffect(() => {
    if (!getToken()) {
      setAuthInitializing(false)
      return
    }
    api
      .authMe()
      .then((user) => {
        setCurrentUser(user)
        setAuthInitializing(false)
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setToken(null)
        }
        setCurrentUser(null)
        setAuthInitializing(false)
      })
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

  // 自动同步需要比驾驶舱普通刷新更快地发现后台任务；这里只读调度状态，不触发扫描。
  useEffect(() => {
    if (!schedulerStatus?.enabled) return undefined
    let cancelled = false
    const pollScheduler = async () => {
      if (document.hidden) return
      try {
        const next = await api.schedulerStatus()
        if (!cancelled) setSchedulerStatus(next)
      } catch {
        // 普通驾驶舱刷新负责显示控制面错误；任务发现轮询保持安静并等待下一轮。
      }
    }
    const timer = window.setInterval(() => void pollScheduler(), 3000)
    void pollScheduler()
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [schedulerStatus?.enabled])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) setNow(new Date())
    }, 1000)
    return () => window.clearInterval(timer)
  }, [])

  const scanTaskId = scanTask?.taskId ?? null
  const scanTaskStatus = scanTask?.status ?? null
  const autoScanTaskId = autoScanTask?.taskId ?? null
  const autoScanTaskStatus = autoScanTask?.status ?? null
  const identityTaskId = identityTask?.taskId ?? null
  const identityTaskStatus = identityTask?.status ?? null

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

  // 自动同步与手动扫描共用后端进度快照，但单独存放，避免触发好友页的手动导入逻辑。
  useEffect(() => {
    if (!autoScanTaskId || autoScanTaskStatus !== 'running' || !autoScanTask) return undefined

    let cancelled = false
    let timer: number | null = null
    const accountId = autoScanTask.accountId

    const poll = async () => {
      try {
        const next = await api.scanChatsProgress(accountId, autoScanTaskId)
        if (cancelled) return
        setAutoScanTask((previous) => {
          if (!previous || previous.taskId !== autoScanTaskId) return previous
          return {
            ...previous,
            ...next,
            syncStatus: next.status === 'completed' ? 'pending' : previous.syncStatus,
            pollError: null,
          }
        })
        if (next.status === 'running') timer = window.setTimeout(() => void poll(), 800)
      } catch (err) {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) {
          setAutoScanTask((previous) =>
            previous && previous.taskId === autoScanTaskId
              ? {
                  ...previous,
                  status: 'failed',
                  stage: '自动同步任务失效',
                  overall: 'error',
                  detail: '后端自动同步任务已失效，请等待下一周期重试',
                  currentItem: null,
                  pollError: null,
                  syncStatus: 'done',
                }
              : previous,
          )
          return
        }
        setAutoScanTask((previous) =>
          previous && previous.taskId === autoScanTaskId
            ? { ...previous, pollError: '进度读取暂时失败，正在自动重连…' }
            : previous,
        )
        timer = window.setTimeout(() => void poll(), 1200)
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [autoScanTaskId, autoScanTaskStatus])

  // 调度器只返回当前自动任务 ID；拿到后再读取同一份 SQLite/内存进度快照。
  useEffect(() => {
    const runningScan = schedulerStatus?.autoScans.find(
      (scan) => scan.status === 'running' && scan.taskId,
    )
    if (!runningScan?.taskId || autoScanTask?.taskId === runningScan.taskId) {
      return undefined
    }

    let cancelled = false
    api
      .scanChatsProgress(runningScan.accountId, runningScan.taskId)
      .then((next) => {
        if (cancelled || next.source !== 'automatic') return
        setAutoScanTask({
          ...next,
          syncStatus: next.status === 'completed' ? 'pending' : 'done',
          batchResult: null,
          syncError: null,
          pollError: null,
        })
        setAutoScanDockVisible(true)
      })
      .catch(() => {
        // 任务可能刚创建尚未提交快照；下一轮调度状态会再次尝试接管。
      })
    return () => {
      cancelled = true
    }
  }, [schedulerStatus, autoScanTask?.taskId])

  // 调度器保持 running 到官方状态写回完成，100% 后用它解除自动同步任务坞的收尾态。
  useEffect(() => {
    if (
      !autoScanTask ||
      autoScanTask.status !== 'completed' ||
      autoScanTask.syncStatus !== 'pending'
    ) {
      return
    }
    const status = schedulerStatus?.autoScans.find(
      (scan) => scan.accountId === autoScanTask.accountId,
    )
    if (status?.status === 'running') return
    setAutoScanTask((previous) =>
      previous && previous.taskId === autoScanTask.taskId
        ? { ...previous, syncStatus: 'done' }
        : previous,
    )
  }, [autoScanTask?.taskId, autoScanTask?.status, autoScanTask?.syncStatus, schedulerStatus])

  // 调度器完成自动同步后刷新好友状态；不会调用手动扫描的批量导入收尾。
  useEffect(() => {
    if (
      !autoScanTask ||
      autoScanTask.status === 'running' ||
      autoScanTask.syncStatus !== 'done'
    ) {
      return
    }
    if (autoScanCompletionHandledRef.current === autoScanTask.taskId) return
    autoScanCompletionHandledRef.current = autoScanTask.taskId
    void refresh()
  }, [autoScanTask?.taskId, autoScanTask?.status, autoScanTask?.syncStatus, refresh])

  // 身份补采也属于 App 生命周期，离开好友页后继续由后端执行并持久化进度。
  useEffect(() => {
    if (!identityTaskId || identityTaskStatus !== 'running' || !identityTask) return undefined

    let cancelled = false
    let timer: number | null = null
    const accountId = identityTask.accountId

    const poll = async () => {
      try {
        const next = await api.identityHydrationProgress(accountId, identityTaskId)
        if (cancelled) return
        setIdentityTask((previous) =>
          previous?.taskId === identityTaskId
            ? { ...previous, ...next, pollError: null }
            : previous,
        )
        if (next.status === 'running') timer = window.setTimeout(() => void poll(), 800)
      } catch (err) {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) {
          setIdentityTask((previous) =>
            previous?.taskId === identityTaskId
              ? {
                  ...previous,
                  status: 'failed',
                  stage: '身份补采任务失效',
                  overall: 'error',
                  detail: '后端任务已失效，请重新发起补采',
                  currentItem: null,
                  pollError: null,
                }
              : previous,
          )
          return
        }
        setIdentityTask((previous) =>
          previous?.taskId === identityTaskId
            ? { ...previous, pollError: '进度读取暂时失败，正在自动重连…' }
            : previous,
        )
        timer = window.setTimeout(() => void poll(), 1200)
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [identityTaskId, identityTaskStatus])

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
      const candidates = snapshots
        .filter((snapshot): snapshot is NonNullable<typeof snapshot> => snapshot !== null)
        .sort((left, right) => {
          const leftTime = Date.parse(left.finishedAt ?? left.startedAt ?? '') || 0
          const rightTime = Date.parse(right.finishedAt ?? right.startedAt ?? '') || 0
          return rightTime - leftTime
        })
      const manualCandidate = snapshots
        .filter(
          (snapshot): snapshot is NonNullable<typeof snapshot> =>
            snapshot !== null && snapshot.source !== 'automatic',
        )
        .sort((left, right) => {
          const leftTime = Date.parse(left.finishedAt ?? left.startedAt ?? '') || 0
          const rightTime = Date.parse(right.finishedAt ?? right.startedAt ?? '') || 0
          return rightTime - leftTime
        })[0]
      const automaticCandidate = candidates.find((candidate) => candidate.source === 'automatic')

      if (manualCandidate) {
        scanCompletionHandledRef.current = null
        setScanTask({
          ...manualCandidate,
          syncStatus: manualCandidate.status === 'completed' ? 'pending' : 'done',
          batchResult: null,
          syncError: null,
          pollError: null,
        })
        setScanDockVisible(true)
      }
      if (
        automaticCandidate &&
        (automaticCandidate.status === 'running' || automaticCandidate.status === 'interrupted')
      ) {
        setAutoScanTask({
          ...automaticCandidate,
          syncStatus: 'done',
          batchResult: null,
          syncError: null,
          pollError: null,
        })
        setAutoScanDockVisible(true)
      }
    }

    void recoverLatestScan()
    return () => {
      cancelled = true
    }
  }, [accounts, scanTask])

  useEffect(() => {
    if (
      identityRecoveryStartedRef.current ||
      accounts.length === 0 ||
      identityTask !== null
    ) {
      return undefined
    }
    identityRecoveryStartedRef.current = true
    let cancelled = false

    async function recoverLatestIdentityHydration() {
      const snapshots = await Promise.all(
        accounts.map(async (account) => {
          try {
            return await api.latestIdentityHydration(account.id)
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
      setIdentityTask({ ...candidate, pollError: null })
      setIdentityDockVisible(true)
    }

    void recoverLatestIdentityHydration()
    return () => {
      cancelled = true
    }
  }, [accounts, identityTask])

  async function finalizeScan(task: ScanTaskState) {
    const accountTargets = targets.filter((target) => target.accountId === task.accountId)
    const existingNicknames = new Set(accountTargets.map((target) => target.nicknameSnapshot))
    const existingPlatformIds = new Set(
      accountTargets
        .map((target) => target.platformUserId)
        .filter((platformUserId): platformUserId is string => Boolean(platformUserId)),
    )
    const existingConversationIds = new Set(
      accountTargets
        .map((target) => target.platformConversationId)
        .filter((conversationId): conversationId is string => Boolean(conversationId)),
    )
    const existingEntries = task.entries.filter(
      (entry) =>
        existingNicknames.has(entry.title) ||
        (entry.platformUserId != null && existingPlatformIds.has(entry.platformUserId)) ||
        (entry.platformConversationId != null &&
          existingConversationIds.has(entry.platformConversationId)),
    )
    let batchResult: TargetBatchResult = {
      created: [],
      refreshed: [],
      duplicates: [],
      failed: [],
      identityBound: [],
      identityUnresolved: [],
      identityAmbiguous: [],
      conversationBound: [],
      conversationUnresolved: [],
      conversationAmbiguous: [],
    }

    try {
      if (existingEntries.length > 0) {
        // 扫描不会自动新增好友；只把已存在好友的官方状态快照同步回列表。
        batchResult = await api.createTargetsBatch({
          accountId: task.accountId,
          items: existingEntries.map((entry) => ({
            nickname: entry.title,
            platformUserId: entry.platformUserId,
            platformHandle: entry.platformHandle,
            platformConversationId: entry.platformConversationId,
            conversationType: entry.conversationType,
            identityStatus: entry.identityStatus,
            conversationStatus: entry.conversationStatus,
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

  useEffect(() => {
    if (
      !autoScanTask ||
      !autoScanDockVisible ||
      autoScanTask.status === 'running' ||
      autoScanTask.syncStatus !== 'done'
    ) {
      return undefined
    }
    const timer = window.setTimeout(() => setAutoScanDockVisible(false), 4500)
    return () => window.clearTimeout(timer)
  }, [autoScanTask?.taskId, autoScanTask?.status, autoScanTask?.syncStatus, autoScanDockVisible])

  useEffect(() => {
    if (!identityTask || identityTask.status === 'running') return
    if (identityCompletionHandledRef.current !== identityTask.taskId) {
      identityCompletionHandledRef.current = identityTask.taskId
      void refresh()
    }
    if (!identityDockVisible || identityTask.status !== 'completed') return
    const timer = window.setTimeout(() => setIdentityDockVisible(false), 4500)
    return () => window.clearTimeout(timer)
  }, [identityTask?.taskId, identityTask?.status, identityDockVisible, refresh])

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

  async function handleUpdateAutoScanInterval(hours: number) {
    if (!schedulerStatus || autoScanIntervalSaving) return
    setAutoScanIntervalSaving(true)
    try {
      const next = await api.schedulerUpdateSettings(hours)
      setSchedulerStatus(next)
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '自动同步周期保存失败')
    } finally {
      setAutoScanIntervalSaving(false)
    }
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

  async function handleStartIdentityHydration(accountId: string, targetIds: string[]) {
    if (identityStarting || identityCancelling || identityTask?.status === 'running') return
    setIdentityStarting(true)
    try {
      const initial = await api.startIdentityHydration(accountId, targetIds)
      identityCompletionHandledRef.current = null
      setIdentityTask({ ...initial, pollError: null })
      setIdentityDockVisible(true)
    } finally {
      setIdentityStarting(false)
    }
  }

  async function handleCancelIdentityHydration() {
    if (!identityTask || identityTask.status !== 'running' || identityCancelling) return
    setIdentityCancelling(true)
    try {
      const next = await api.cancelIdentityHydration(identityTask.accountId, identityTask.taskId)
      setIdentityTask((previous) =>
        previous?.taskId === next.taskId ? { ...next, pollError: null } : previous,
      )
      setIdentityDockVisible(true)
    } catch (err) {
      setIdentityTask((previous) =>
        previous
          ? {
              ...previous,
              pollError: err instanceof ApiError ? `取消失败：${err.message}` : '取消失败，正在重新读取进度…',
            }
          : previous,
      )
    } finally {
      setIdentityCancelling(false)
    }
  }

  async function handleRetryIdentityHydration() {
    if (!identityTask) return
    const targetIds = identityTask.results
      .filter((result) => result.status !== 'bound')
      .map((result) => result.targetId)
    if (targetIds.length === 0) return
    try {
      await handleStartIdentityHydration(identityTask.accountId, targetIds)
    } catch (err) {
      setIdentityTask((previous) =>
        previous
          ? {
              ...previous,
              pollError: err instanceof ApiError ? `重新补采失败：${err.message}` : '重新补采失败，请重试',
            }
          : previous,
      )
    }
  }

  function handleOpenSend(account: Account, force = false) {
    setSendAccount(account)
    setSendMinimized(false)
    setSendForce(force)
  }

  function handleOpenSendTarget(account: Account, target: ContractTarget) {
    setSendTarget({ account, target })
  }

  const loading = health === null && error === null

  return (
    <div className="app">
      {authInitializing ? (
        <div className="login-page">
          <div className="login-card">
            <p className="login-sub">正在确认登录状态…</p>
          </div>
        </div>
      ) : currentUser === null ? (
        <LoginPage
          onAuthed={() => {
            // 登录成功后读取当前用户并进入控制台，随后拉取一次全量数据
            void api
              .authMe()
              .then((user) => {
                setCurrentUser(user)
              })
              .catch(() => setCurrentUser(null))
            void refresh()
          }}
        />
      ) : (
        <>
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
          {currentUser && (
            <button
              className="btn ghost small"
              type="button"
              onClick={() => {
                setToken(null)
                setCurrentUser(null)
              }}
              title={`当前平台账号：${currentUser.username}`}
            >
              退出
            </button>
          )}
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
                  onUpdateAutoScanInterval={(hours) => void handleUpdateAutoScanInterval(hours)}
                  autoScanIntervalSaving={autoScanIntervalSaving}
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
                  onOpenSendTarget={handleOpenSendTarget}
                  scanTask={scanTask}
                  scanStarting={scanStarting}
                  onStartScan={handleStartScan}
                  identityTask={identityTask}
                  identityStarting={identityStarting}
                  onStartIdentityHydration={handleStartIdentityHydration}
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
            offset={dockOffset('scan')}
          />
        )}

        {autoScanTask && autoScanDockVisible && (
          <ScanProgressDock
            task={autoScanTask}
            accounts={accounts}
            automatic
            onOpen={() => setPage('targets')}
            onDismiss={() => setAutoScanDockVisible(false)}
            offset={dockOffset('auto-scan')}
          />
        )}

        {sendAccount && (
          <SendModal
            account={sendAccount}
            force={sendForce}
            minimized={sendMinimized}
            offset={sendMinimized ? dockOffset('send') : false}
            onClose={() => {
              setSendAccount(null)
              setSendMinimized(false)
              setSendForce(false)
            }}
            onMinimize={() => setSendMinimized(true)}
            onRestore={() => setSendMinimized(false)}
            onChanged={() => void refresh()}
          />
        )}

        {sendTarget && (
          <SendTargetModal
            account={sendTarget.account}
            target={sendTarget.target}
            templates={templates}
            onClose={() => setSendTarget(null)}
            onChanged={() => void refresh()}
          />
        )}

        {identityTask && identityDockVisible && (
          <IdentityHydrationProgress
            task={identityTask}
            accounts={accounts}
            onOpen={() => setPage('targets')}
            onDismiss={() => setIdentityDockVisible(false)}
            onCancel={() => void handleCancelIdentityHydration()}
            onRetry={() => void handleRetryIdentityHydration()}
            cancelLabel={identityCancelling ? '正在取消…' : '取消补采'}
            cancelDisabled={identityCancelling}
            offset={dockOffset('identity')}
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
        </>
      )}
    </div>
  )
}
