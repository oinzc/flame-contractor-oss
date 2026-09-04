export type AccountStatus =
  | 'pending_login'
  | 'healthy'
  | 'running'
  | 'paused'
  | 'auth_expired'
  | 'verification_required'
  | 'dom_broken'
  | 'cooldown'

export type JobStatus =
  | 'scheduled'
  | 'leased'
  | 'opening_chat'
  | 'sending'
  | 'confirming'
  | 'succeeded'
  | 'failed_retryable'
  | 'failed_terminal'
  | 'unknown_requires_review'
  | 'cancelled'

export type FailureClass =
  | 'network'
  | 'auth'
  | 'verification'
  | 'target_uncertain'
  | 'dom_changed'
  | 'unknown_outcome'
  | 'other'

export type ScheduleStatus =
  | 'pending'
  | 'leased'
  | 'sending'
  | 'succeeded'
  | 'failed'
  | 'unknown_requires_review'
  | 'skipped'
  | 'cancelled'

export interface SendSchedule {
  id: string
  accountId: string
  businessDate: string
  scheduledAt: string | null
  status: ScheduleStatus
  attemptCount: number
  startedAt: string | null
  finishedAt: string | null
  lastError: string | null
}

export type AutoScanStatusKind =
  | 'scheduled'
  | 'due'
  | 'running'
  | 'paused'
  | 'waiting_login'
  | 'disabled'
  | 'no_targets'

export interface AutoScanStatus {
  accountId: string
  taskId: string | null
  status: AutoScanStatusKind
  lastScanAt: string | null
  nextScanAt: string | null
  detail: string | null
}

export interface SchedulerStatus {
  enabled: boolean
  paused: boolean
  runningAccounts: string[]
  todaySchedules: SendSchedule[]
  autoScanIntervalHours: number
  autoScans: AutoScanStatus[]
}

export interface Account {
  id: string
  displayName: string
  status: AccountStatus
  enabled: boolean
  timezone: string
  sendWindowStart: string
  sendWindowEnd: string
  /** 账号级精确发送时刻 HH:MM；为空=在发送窗口内随机取时 */
  sendTimeHhmm: string | null
  lastVerifiedAt: string | null
  lastSuccessAt: string | null
  /** 登录成功后读到的抖音身份昵称（与平台标签 display_name 的对照） */
  douyinNickname: string | null
  createdAt: string
  updatedAt: string
}

export interface ContractTarget {
  id: string
  accountId: string
  platformUserId: string | null
  /** 抖音公开展示号；稳定 sec_uid 只用于安全核对，不直接展示。 */
  platformHandle: string | null
  /** 群聊不使用用户 sec_uid；这里保存抖音会话 protobuf 的唯一 ID。 */
  platformConversationId: string | null
  conversationType: 'user' | 'group' | null
  nicknameSnapshot: string
  templateId: string | null
  enabled: boolean
  /** 好友级精确发送时刻 HH:MM；为空=跟随账号（账号精确时刻或发送窗口随机） */
  sendTimeHhmm: string | null
  /** 抖音会话列表最近一次扫描到的官方展示状态，不参与发送决策 */
  sparkKind: string | null
  sparkLabel: string | null
  sparkScannedAt: string | null
  streakDays: number
  lastSentAt: string | null
  lastConfirmedAt: string | null
  /** 软删除标记：非空表示该好友已从列表隐藏，但历史任务保留审计。 */
  deletedAt: string | null
  createdAt: string
  updatedAt: string
}

export interface ScanChatEntry {
  title: string
  platformUserId: string | null
  platformHandle: string | null
  platformConversationId: string | null
  conversationType: 'user' | 'group' | null
  identityStatus: 'bound' | 'ambiguous' | 'not_observed'
  identitySource: 'remark_name' | 'nickname' | 'unique_id' | 'short_id' | null
  identityCandidateCount: number
  identityDetail: string | null
  conversationStatus: 'bound' | 'ambiguous' | 'not_observed'
  conversationCandidateCount: number
  conversationDetail: string | null
  preview: string
  streakDays: number | null
  streakKind: string | null
  streakLabel: string | null
}

export interface TargetBatchResult {
  created: string[]
  refreshed: string[]
  duplicates: string[]
  failed: string[]
  identityBound: string[]
  identityUnresolved: string[]
  identityAmbiguous: string[]
  conversationBound: string[]
  conversationUnresolved: string[]
  conversationAmbiguous: string[]
}

export interface ScanProgress {
  taskId: string
  accountId: string
  source: 'manual' | 'automatic'
  status: 'running' | 'completed' | 'failed' | 'interrupted' | 'cancelled'
  /** 会话列表滚动到底前总数未知；为 null 时只展示已发现计数。 */
  total: number | null
  completed: number
  stage: string
  currentItem: string | null
  round: number
  maxRounds: number
  overall: 'ok' | 'auth_expired' | 'error' | null
  detail: string | null
  entries: ScanChatEntry[]
  startedAt: string | null
  finishedAt: string | null
}

export interface ScanTaskState extends ScanProgress {
  /** 扫描完成后的本地快照同步状态，不属于后端扫描进度。 */
  syncStatus: 'pending' | 'done' | 'failed'
  batchResult: TargetBatchResult | null
  syncError: string | null
  pollError: string | null
}

export interface IdentityHydrationResult {
  targetId: string
  nickname: string
  status: 'bound' | 'ambiguous' | 'not_observed' | 'conflict'
  opened: boolean
  platformUserId: string | null
  platformHandle: string | null
  identitySource: 'remark_name' | 'nickname' | 'unique_id' | 'short_id' | null
  identityCandidateCount: number
  detail: string | null
}

export interface IdentityHydrationProgress {
  taskId: string
  accountId: string
  status: 'running' | 'completed' | 'failed' | 'interrupted' | 'cancelled'
  total: number
  completed: number
  stage: string
  currentItem: string | null
  overall: 'ok' | 'partial' | 'auth_expired' | 'error' | null
  detail: string | null
  results: IdentityHydrationResult[]
  startedAt: string | null
  finishedAt: string | null
}

export interface IdentityHydrationTaskState extends IdentityHydrationProgress {
  pollError: string | null
}

export interface PoolItem {
  id: string
  content: string
  weekdayMask: number
  enabled: boolean
  lastUsedAt: string | null
}

export interface MessageTemplate {
  id: string
  name: string
  type: 'text' | 'image' | 'sticker' | 'pool'
  content: string
  imagePath: string | null
  stickerName: string | null
  stickerLabel: string | null
  stickerCategory: string | null
  stickerFallbackIndex: number | null
  version: number
  enabled: boolean
  poolItems: PoolItem[]
  createdAt: string
  updatedAt: string
}

export interface PoolItemInput {
  content: string
  weekdayMask?: number
  enabled?: boolean
}

export interface StatsDailyRow {
  date: string
  total: number
  succeeded: number
  failed: number
  unknown: number
}

export interface StatsSummary {
  windowDays: number
  daily: StatsDailyRow[]
  currentSuccessStreak: number
  bestSuccessStreak: number
  totalJobs: number
  succeededJobs: number
  successRate: number | null
}

export interface SendJob {
  id: string
  accountId: string
  targetId: string
  templateId: string
  businessDate: string
  messageSlotId: string
  status: JobStatus
  scheduledAt: string | null
  attemptCount: number
  failureClass: FailureClass | null
  confirmedAt: string | null
  createdAt: string
  updatedAt: string
}

export interface DashboardSummary {
  accountCount: number
  healthyAccountCount: number
  activeContractCount: number
  todaySucceeded: number
  todayPending: number
  needsAttention: number
  accounts: Account[]
  recentJobs: SendJob[]
}

export interface Health {
  status: string
  app: string
  version: string
  database: string
}

export interface AccountCreateInput {
  displayName: string
  timezone: string
  sendWindowStart: string
  sendWindowEnd: string
  sendTimeHhmm?: string | null
}

export type LoginState = 'idle' | 'waiting_scan' | 'succeeded' | 'failed' | 'expired'

export interface LoginStatus {
  state: LoginState
  detail: string | null
  /** 登录成功后读到的当前抖音身份昵称（控制台标签与抖音身份的对照） */
  selfNickname?: string | null
}

export type DryRunTargetStatus = 'found' | 'not_found' | 'ambiguous'

export interface DryRunTargetResult {
  target: string
  status: DryRunTargetStatus
  title: string | null
  detail: string | null
}

export interface DryRunRead {
  accountId: string
  overall: 'ok' | 'auth_expired' | 'error'
  detail: string | null
  targets: DryRunTargetResult[]
  visibleTitles: string[]
  diagnostics: Record<string, unknown> | null
  artifactPath: string | null
}

export interface TargetCreateInput {
  accountId: string
  nicknameSnapshot: string
  platformUserId?: string
  platformHandle?: string
  platformConversationId?: string
  conversationType?: 'user' | 'group'
  templateId?: string
  streakDays: number
}

export interface TemplateCreateInput {
  name: string
  type: 'text' | 'image' | 'sticker' | 'pool'
  content: string
  imagePath?: string
  stickerName?: string
  stickerLabel?: string
  stickerCategory?: string
  poolItems?: PoolItemInput[]
}

export interface SendPreviewItem {
  targetId: string
  targetName: string
  templateId: string | null
  message: string | null
  reason: string | null
}

export interface SendPreviewRead {
  accountId: string
  businessDate: string
  items: SendPreviewItem[]
}

export interface SendTargetResult {
  target: string
  status: string
  failureClass: string | null
  detail: string | null
  message: string | null
  screenshot: string | null
  pageUrl: string | null
}

export interface SendRead {
  accountId: string
  overall: 'ok' | 'partial' | 'auth_expired' | 'error' | 'network_unavailable'
  detail: string | null
  results: SendTargetResult[]
  artifactPath: string | null
}

export interface SendTargetInput {
  templateId?: string
  content?: string
}

export interface SendProgress {
  accountId: string
  status: 'idle' | 'running' | 'completed' | 'failed'
  total: number
  completed: number
  currentTarget: string | null
  stage: string
  lastResult: string | null
  overall: SendRead['overall'] | null
  detail: string | null
  startedAt: string | null
  finishedAt: string | null
}
