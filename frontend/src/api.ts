export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function readDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown }
    if (typeof payload.detail === 'string') {
      return payload.detail
    }
    if (Array.isArray(payload.detail)) {
      return payload.detail.map((item) => (item as { msg?: string }).msg ?? '').join('；')
    }
  } catch {
    // ignore malformed error bodies
  }
  return `请求失败（HTTP ${response.status}）`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    throw new ApiError(response.status, await readDetail(response))
  }
  // 204 No Content 无响应体，json() 会抛解析错误——删除接口依赖此分支
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export const api = {
  health: () => request<import('./types').Health>('/health'),
  dashboard: () => request<import('./types').DashboardSummary>('/api/dashboard'),
  accounts: () => request<import('./types').Account[]>('/api/accounts'),
  createAccount: (input: import('./types').AccountCreateInput) =>
    request<import('./types').Account>('/api/accounts', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  patchAccount: (id: string, patch: { enabled?: boolean; displayName?: string }) =>
    request<import('./types').Account>(`/api/accounts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  deleteAccount: (id: string) =>
    request<void>(`/api/accounts/${id}`, { method: 'DELETE' }),
  targets: () => request<import('./types').ContractTarget[]>('/api/targets'),
  createTarget: (input: import('./types').TargetCreateInput) =>
    request<import('./types').ContractTarget>('/api/targets', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  patchTarget: (id: string, patch: { streakDays?: number; enabled?: boolean; templateId?: string | null }) =>
    request<import('./types').ContractTarget>(`/api/targets/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  deleteTarget: (id: string) =>
    request<void>(`/api/targets/${id}`, { method: 'DELETE' }),
  createTargetsBatch: (input: {
    accountId: string
    templateId?: string
    items: {
      nickname: string
      platformUserId?: string | null
      platformHandle?: string | null
      platformConversationId?: string | null
      conversationType?: 'user' | 'group' | null
      identityStatus?: 'bound' | 'ambiguous' | 'not_observed'
      conversationStatus?: 'bound' | 'ambiguous' | 'not_observed'
      streakDays?: number
      sparkKind?: string | null
      sparkLabel?: string | null
    }[]
  }) =>
    request<import('./types').TargetBatchResult>('/api/targets/batch', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  scanChats: (accountId: string) =>
    request<{
      overall: 'ok' | 'auth_expired' | 'error'
      detail: string | null
      entries: import('./types').ScanChatEntry[]
    }>(`/api/accounts/${accountId}/scan-chats`, { method: 'POST' }),
  startScanChats: (accountId: string) =>
    request<import('./types').ScanProgress>(`/api/accounts/${accountId}/scan-chats/start`, {
      method: 'POST',
    }),
  scanChatsProgress: (accountId: string, taskId: string) =>
    request<import('./types').ScanProgress>(
      `/api/accounts/${accountId}/scan-chats/progress?taskId=${encodeURIComponent(taskId)}`,
    ),
  cancelScanChats: (accountId: string, taskId: string) =>
    request<import('./types').ScanProgress>(
      `/api/accounts/${accountId}/scan-chats/cancel?taskId=${encodeURIComponent(taskId)}`,
      { method: 'POST' },
    ),
  latestScanChats: (accountId: string) =>
    request<import('./types').ScanProgress | null>(`/api/accounts/${accountId}/scan-chats/latest`),
  sendPreview: (id: string) =>
    request<import('./types').SendPreviewRead>(`/api/accounts/${id}/send/preview`),
  sendProgress: (id: string) =>
    request<import('./types').SendProgress>(`/api/accounts/${id}/send/progress`),
  sendNow: (id: string) =>
    request<import('./types').SendRead>(`/api/accounts/${id}/send`, { method: 'POST' }),
  templates: () => request<import('./types').MessageTemplate[]>('/api/templates'),
  createTemplate: (input: import('./types').TemplateCreateInput) =>
    request<import('./types').MessageTemplate>('/api/templates', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  patchTemplate: (
    id: string,
    patch: {
      name?: string
      content?: string
      imagePath?: string
      stickerName?: string
      stickerCategory?: string
      enabled?: boolean
      poolItems?: { content: string; weekdayMask: number }[]
    },
  ) =>
    request<import('./types').MessageTemplate>(`/api/templates/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  deleteTemplate: (id: string) =>
    request<void>(`/api/templates/${id}`, { method: 'DELETE' }),
  confirmJobSent: (id: string) =>
    request<import('./types').SendJob>(`/api/jobs/${id}/confirm-sent`, { method: 'POST' }),
  releaseJob: (id: string) =>
    request<import('./types').SendJob>(`/api/jobs/${id}/release`, { method: 'POST' }),
  startLogin: (id: string) =>
    request<import('./types').LoginStatus>(`/api/accounts/${id}/login`, {
      method: 'POST',
    }),
  loginStatus: (id: string) =>
    request<import('./types').LoginStatus>(`/api/accounts/${id}/login`),
  cancelLogin: (id: string) =>
    request<import('./types').LoginStatus>(`/api/accounts/${id}/login/cancel`, {
      method: 'POST',
    }),
  dryRun: (id: string) =>
    request<import('./types').DryRunRead>(`/api/accounts/${id}/dry-run`, {
      method: 'POST',
    }),
  schedulerStatus: () =>
    request<import('./types').SchedulerStatus>('/api/scheduler'),
  schedulerPause: () =>
    request<import('./types').SchedulerStatus>('/api/scheduler/pause', {
      method: 'POST',
    }),
  schedulerResume: () =>
    request<import('./types').SchedulerStatus>('/api/scheduler/resume', {
      method: 'POST',
    }),
  statsSummary: () => request<import('./types').StatsSummary>('/api/stats/summary'),
  startQrLogin: (id: string) =>
    request<import('./types').LoginStatus>(`/api/accounts/${id}/login/qr`, {
      method: 'POST',
    }),
  qrLoginStatus: (id: string) =>
    request<import('./types').LoginStatus>(`/api/accounts/${id}/login/qr`),
  qrImageUrl: (id: string) => `/api/accounts/${id}/login/qr/image`,
  downloadBackup: async () => {
    const response = await fetch('/api/backup')
    if (!response.ok) throw new ApiError(response.status, '备份导出失败')
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `flame-backup-${new Date().toISOString().slice(0, 10)}.json`
    anchor.click()
    URL.revokeObjectURL(url)
  },
}
