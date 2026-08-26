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
  patchAccount: (id: string, patch: { enabled?: boolean }) =>
    request<import('./types').Account>(`/api/accounts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  targets: () => request<import('./types').ContractTarget[]>('/api/targets'),
  createTarget: (input: import('./types').TargetCreateInput) =>
    request<import('./types').ContractTarget>('/api/targets', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  deleteTarget: (id: string) =>
    request<void>(`/api/targets/${id}`, { method: 'DELETE' }),
  sendPreview: (id: string) =>
    request<import('./types').SendPreviewRead>(`/api/accounts/${id}/send/preview`),
  sendNow: (id: string) =>
    request<import('./types').SendRead>(`/api/accounts/${id}/send`, { method: 'POST' }),
  templates: () => request<import('./types').MessageTemplate[]>('/api/templates'),
  createTemplate: (input: import('./types').TemplateCreateInput) =>
    request<import('./types').MessageTemplate>('/api/templates', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
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
}
