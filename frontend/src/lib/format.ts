import type { AccountStatus, JobStatus, ScheduleStatus } from '../types'

export type Tone = 'default' | 'ember' | 'gold' | 'cyan' | 'crimson'

export const ACCOUNT_STATUS_LABELS: Record<AccountStatus, string> = {
  pending_login: '待登录',
  healthy: '健康',
  running: '运行中',
  paused: '已暂停',
  auth_expired: '登录失效',
  verification_required: '需验证',
  dom_broken: '页面不兼容',
  cooldown: '冷却中',
}

export const ACCOUNT_STATUS_TONES: Record<AccountStatus, Tone> = {
  pending_login: 'gold',
  healthy: 'ember',
  running: 'cyan',
  paused: 'default',
  auth_expired: 'crimson',
  verification_required: 'crimson',
  dom_broken: 'crimson',
  cooldown: 'gold',
}

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  scheduled: '已排程',
  leased: '已租用',
  opening_chat: '打开会话',
  sending: '发送中',
  confirming: '确认中',
  succeeded: '已续焰',
  failed_retryable: '待重试',
  failed_terminal: '失败',
  unknown_requires_review: '待人工复核',
  cancelled: '已取消',
}

export const JOB_STATUS_TONES: Record<JobStatus, Tone> = {
  scheduled: 'gold',
  leased: 'cyan',
  opening_chat: 'cyan',
  sending: 'cyan',
  confirming: 'cyan',
  succeeded: 'ember',
  failed_retryable: 'gold',
  failed_terminal: 'crimson',
  unknown_requires_review: 'crimson',
  cancelled: 'default',
}

export const ATTENTION_ACCOUNT_STATUSES: AccountStatus[] = [
  'auth_expired',
  'verification_required',
  'dom_broken',
]

export const ATTENTION_ACCOUNT_HINTS: Record<string, string> = {
  auth_expired: '登录已失效，需本人重新扫码确认。',
  verification_required: '平台要求验证，请本人处理，不自动绕过。',
  dom_broken: '页面结构变化，已暂停自动发送并保留诊断。',
}

export const SCHEDULE_STATUS_LABELS: Record<ScheduleStatus, string> = {
  pending: '待发送',
  leased: '已租用',
  sending: '发送中',
  succeeded: '已发送',
  failed: '发送失败',
  unknown_requires_review: '待人工复核',
  skipped: '已跳过',
  cancelled: '已取消',
}

export const SCHEDULE_STATUS_TONES: Record<ScheduleStatus, Tone> = {
  pending: 'gold',
  leased: 'cyan',
  sending: 'cyan',
  succeeded: 'ember',
  failed: 'crimson',
  unknown_requires_review: 'crimson',
  skipped: 'default',
  cancelled: 'default',
}

export function formatDateTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

export function formatTimeHM(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

export function formatDateOnly(value: string | null): string {
  if (!value) return '—'
  return value.slice(0, 10)
}
