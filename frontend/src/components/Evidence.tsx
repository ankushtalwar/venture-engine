import type { Evidence, EvidenceStatus } from '../api/types'
import { STATUS_HELP, STATUS_NAMES } from '../lib/format'

export function EvidenceBadge({ status }: { status: EvidenceStatus }) {
  return (
    <span className={`ev ev-${status}`} data-testid="evidence-status" data-status={status} title={STATUS_HELP[status]}>
      {STATUS_NAMES[status]}
    </span>
  )
}

/** The value itself, marked the way a fact-checker would: highlighted when observed, dashed when inferred. */
export function EvidenceValue({ value, status }: { value: string; status: EvidenceStatus }) {
  if (status === 'unknown' || !value) return <span className="val val-unknown">Not stated</span>
  return <span className={`val val-${status}`}>{value}</span>
}

/** Value, badge and the quote or basis behind it. */
export function EvidenceLine({ ev }: { ev: Evidence }) {
  return (
    <div className={`ev-line ev-line-${ev.status}`}>
      <div className="ev-line-head">
        <EvidenceBadge status={ev.status} />
        <EvidenceValue value={ev.value} status={ev.status} />
      </div>
      {ev.status === 'observed' && ev.quote && <q className="ev-quote">{ev.quote}</q>}
      {ev.status === 'derived' && (
        <>
          {ev.quote && <q className="ev-quote">{ev.quote}</q>}
          {ev.basis && <p className="ev-basis">Formula: {ev.basis}</p>}
        </>
      )}
      {ev.status === 'inferred' && ev.basis && <p className="ev-basis">Why the model thinks so: {ev.basis}</p>}
    </div>
  )
}
