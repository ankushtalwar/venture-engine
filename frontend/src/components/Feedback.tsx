import { errorText } from '../lib/useApi'

export function Loading({ what }: { what: string }) {
  return (
    <p className="loading" aria-busy="true">
      Loading {what}…
    </p>
  )
}

export function ErrorNote({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null
  return (
    <div className="error" role="alert">
      <p>{errorText(error)}</p>
      {onRetry && (
        <button type="button" className="btn btn-small" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}
