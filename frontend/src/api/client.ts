import type {
  ClusterDetail,
  ClusterLabel,
  ClusterLabelFilter,
  ClusterList,
  ClusterSummary,
  Confidence,
  Health,
  Override,
  ReportFile,
  SignalLabel,
  SourceRow,
  SplitResult,
  Stats,
  Trends,
} from './types'

export const TOKEN_KEY = 'paindisc.apiToken'
const BASE = '/api'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
  }
}

// ------------------------------------------------------------------ token

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // Storage can be unavailable (private mode); the token then lasts only for this request.
  }
}

/** Asks the person for a token. Resolves with the token, or null if they cancel. */
export type TokenRequester = (reason: 'missing' | 'rejected') => Promise<string | null>

let tokenRequester: TokenRequester | null = null

export function setTokenRequester(fn: TokenRequester | null): void {
  tokenRequester = fn
}

// ------------------------------------------------------------------ transport

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      // FastAPI validation errors: [{loc, msg, type}]
      return detail
        .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d)))
        .join('; ')
    }
  }
  return fallback
}

async function request<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Could not reach the API. Check that the server is running.')
  }

  if (res.status === 401 && !retried && tokenRequester) {
    const next = await tokenRequester(token ? 'rejected' : 'missing')
    if (next) {
      setToken(next)
      return request<T>(method, path, body, true)
    }
  }

  const text = await res.text()
  let parsed: unknown = undefined
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = undefined
    }
  }

  if (res.status === 401) {
    throw new ApiError(
      401,
      retried
        ? 'The API rejected that token. Try the action again to enter a different one.'
        : 'This action needs an API token. Try it again to enter one.',
    )
  }
  if (!res.ok) throw new ApiError(res.status, detailOf(parsed, `Request failed (${res.status}).`))
  return parsed as T
}

const get = <T>(path: string) => request<T>('GET', path)
const post = <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {})
const del = <T>(path: string) => request<T>('DELETE', path)
const enc = encodeURIComponent

// ------------------------------------------------------------------ endpoints

export interface ClusterQuery {
  limit?: number
  offset?: number
  confidence?: Confidence | ''
  label?: ClusterLabelFilter | ''
}

export const api = {
  health: () => get<Health>('/health'),

  clusters: (q: ClusterQuery = {}) => {
    const params = new URLSearchParams()
    params.set('limit', String(q.limit ?? 50))
    params.set('offset', String(q.offset ?? 0))
    if (q.confidence) params.set('confidence', q.confidence)
    if (q.label) params.set('label', q.label)
    return get<ClusterList>(`/clusters?${params.toString()}`)
  },

  cluster: (id: string) => get<ClusterDetail>(`/clusters/${enc(id)}`),

  labelCluster: (id: string, label: ClusterLabel | null, note?: string) =>
    post<ClusterSummary>(`/clusters/${enc(id)}/label`, note ? { label, note } : { label }),

  labelSignal: (id: string, label: SignalLabel | null) =>
    post<{ id: string; label: SignalLabel | null }>(`/signals/${enc(id)}/label`, { label }),

  merge: (ids: string[]) => post<ClusterDetail>('/clusters/merge', { ids }),

  split: (id: string, signalIds: string[]) =>
    post<SplitResult>(`/clusters/${enc(id)}/split`, { signal_ids: signalIds }),

  synthesize: (id: string) => post<ClusterDetail>(`/clusters/${enc(id)}/synthesize`),

  overrides: () => get<Override[]>('/overrides'),
  deleteOverride: (id: number) => del<unknown>(`/overrides/${enc(String(id))}`),

  trends: () => get<Trends>('/trends'),
  sources: () => get<SourceRow[]>('/sources'),
  stats: () => get<Stats>('/stats'),
  reports: () => get<ReportFile[]>('/reports'),
}

export function reportUrl(name: string): string {
  return `${BASE}/reports/${enc(name)}`
}
