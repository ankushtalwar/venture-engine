import type { ClusterLabel, Confidence, Trend } from '../api/types'
import { labelName, TREND_NAMES } from '../lib/format'

const LEVEL: Record<Confidence, number> = { High: 3, Medium: 2, Low: 1 }

/** Confidence is computed from authors, source types, spend and recency (SPEC 5); three pips show the level. */
export function ConfidenceMark({ value }: { value: Confidence }) {
  const level = LEVEL[value] ?? 1
  return (
    <span className={`conf conf-${value.toLowerCase()}`} data-testid="card-confidence">
      <span className="pips" aria-hidden="true">
        {[1, 2, 3].map((i) => (
          <span key={i} className={i <= level ? 'pip on' : 'pip'} />
        ))}
      </span>
      {value}
    </span>
  )
}

export function LabelMark({ value }: { value: ClusterLabel | null }) {
  return (
    <span className={`lbl lbl-${value ?? 'none'}`} data-testid="card-label">
      {labelName(value)}
    </span>
  )
}

const TREND_GLYPH: Record<Trend, string> = { new: '✦', rising: '▲', falling: '▼', steady: '–' }

export function TrendMark({ value }: { value: Trend | null }) {
  if (!value) return null
  return (
    <span className={`trend trend-${value}`} data-testid="card-trend" title="Score change since the previous run">
      <span aria-hidden="true">{TREND_GLYPH[value]}</span>
      {TREND_NAMES[value]}
    </span>
  )
}
