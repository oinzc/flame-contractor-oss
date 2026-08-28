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

export interface SchedulerStatus {
  enabled: boolean
  paused: boolean
  runningAccounts: string[]
  todaySchedules: SendSchedule[]
}

export interface Account {
  id: string
  displayName: string
  status: AccountStatus
  enabled: boolean
  timezone: string
  sendWindowStart: string
  sendWindowEnd: string
  lastVerifiedAt: string | null
  lastSuccessAt: string | null
  createdAt: string
  updatedAt: string
}

export interface ContractTarget {
  id: string
  accountId: string
  platformUserId: string | null
  nicknameSnapshot: string
  templateId: string | null
  enabled: boolean
  streakDays: number
  lastSentAt: string | null
  lastConfirmedAt: string | null
  createdAt: string
  updatedAt: string
}

export interface MessageTemplate {
  id: string
  name: string
  type: 'text' | 'image'
  content: string
  imagePath: string | null
  version: number
  enabled: boolean
  createdAt: string
  updatedAt: string
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
}

export type LoginState = 'idle' | 'waiting_scan' | 'succeeded' | 'failed' | 'expired'

export interface LoginStatus {
  state: LoginState
  detail: string | null
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
  templateId?: string
  streakDays: number
}

export interface TemplateCreateInput {
  name: string
  type: 'text' | 'image'
  content: string
  imagePath?: string
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
}

export interface SendRead {
  accountId: string
  overall: 'ok' | 'partial' | 'auth_expired' | 'error'
  detail: string | null
  results: SendTargetResult[]
  artifactPath: string | null
}
