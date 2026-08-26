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
  type: 'text'
  content: string
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
