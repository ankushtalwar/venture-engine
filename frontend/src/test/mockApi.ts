import { vi } from 'vitest'

export interface MockRequest {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
  headers: Record<string, string>
}

export type MockReply = { status: number; body?: unknown }
export type Handler = (req: MockRequest, params: Record<string, string>) => MockReply | unknown

/** Route table keyed by "METHOD /api/path/:param". Plain values are returned as 200 JSON. */
export function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: MockRequest[] = []
  const compiled = Object.entries(routes).map(([key, handler]) => {
    const [method, pattern] = key.split(' ')
    const names: string[] = []
    const re = new RegExp(
      '^' +
        pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/:(\w+)/g, (_, name: string) => {
          names.push(name)
          return '([^/]+)'
        }) +
        '$',
    )
    return { method, re, names, handler }
  })

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost')
    const method = (init?.method ?? 'GET').toUpperCase()
    const headers = Object.fromEntries(Object.entries((init?.headers as Record<string, string>) ?? {}))
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
    const req: MockRequest = { method, path: url.pathname, query: url.searchParams, body, headers }
    calls.push(req)

    for (const r of compiled) {
      if (r.method !== method) continue
      const m = r.re.exec(url.pathname)
      if (!m) continue
      const params = Object.fromEntries(r.names.map((n, i) => [n, decodeURIComponent(m[i + 1])]))
      const out = typeof r.handler === 'function' ? (r.handler as Handler)(req, params) : r.handler
      const reply: MockReply =
        out && typeof out === 'object' && 'status' in out && typeof (out as MockReply).status === 'number'
          ? (out as MockReply)
          : { status: 200, body: out }
      return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
        status: reply.status,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response(JSON.stringify({ detail: `No mock for ${method} ${url.pathname}` }), { status: 404 })
  })

  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock, writes: () => calls.filter((c) => c.method !== 'GET') }
}
