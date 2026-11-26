import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { setTokenRequester } from '../api/client'

interface Pending {
  reason: 'missing' | 'rejected'
  resolve: (token: string | null) => void
}

/** Registers the token prompt the API client calls on a 401, and renders it. */
export function TokenProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null)
  const queue = useRef<Pending[]>([])

  useEffect(() => {
    setTokenRequester(
      (reason) =>
        new Promise<string | null>((resolve) => {
          const item = { reason, resolve }
          queue.current.push(item)
          setPending((current) => current ?? item)
        }),
    )
    return () => setTokenRequester(null)
  }, [])

  const finish = (token: string | null) => {
    // Every request waiting on a token gets the same answer.
    const waiting = queue.current
    queue.current = []
    waiting.forEach((p) => p.resolve(token))
    setPending(null)
  }

  return (
    <>
      {children}
      {pending && <TokenDialog reason={pending.reason} onSubmit={finish} onCancel={() => finish(null)} />}
    </>
  )
}

function TokenDialog({
  reason,
  onSubmit,
  onCancel,
}: {
  reason: 'missing' | 'rejected'
  onSubmit: (token: string) => void
  onCancel: () => void
}) {
  const [value, setValue] = useState('')
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    input.current?.focus()
  }, [])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const token = value.trim()
    if (token) onSubmit(token)
  }

  return (
    <div className="scrim">
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="token-title"
        aria-describedby="token-help"
        onKeyDown={(e) => {
          if (e.key === 'Escape') onCancel()
        }}
      >
        <h2 id="token-title">API token</h2>
        <p id="token-help">
          {reason === 'rejected'
            ? 'The API rejected the saved token. Enter the value of PAINDISC_API_TOKEN to continue.'
            : 'This server needs a token for changes. Enter the value of PAINDISC_API_TOKEN to continue.'}{' '}
          It is kept for this browser tab only.
        </p>
        <form onSubmit={submit}>
          <label htmlFor="token-input">API token</label>
          <input
            id="token-input"
            ref={input}
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={onCancel}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={!value.trim()}>
              Save token
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
