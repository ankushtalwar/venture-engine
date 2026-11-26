import { COMPONENT_KEYS, type Components } from '../api/types'
import { COMPONENT_HELP, COMPONENT_NAMES } from '../lib/format'

function clamp(v: number | undefined): number {
  return typeof v === 'number' && Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0
}

/** Compact column strip for list rows: one column per score component, 0 to 1. */
export function ComponentStrip({ components }: { components: Components }) {
  const summary = COMPONENT_KEYS.map((k) => `${COMPONENT_NAMES[k]} ${clamp(components[k]).toFixed(2)}`).join(', ')
  return (
    <div className="strip" data-testid="card-components" role="img" aria-label={`Score components: ${summary}`}>
      {COMPONENT_KEYS.map((k) => {
        const v = clamp(components[k])
        return (
          <span key={k} className="strip-col" title={`${COMPONENT_NAMES[k]} ${v.toFixed(2)}`}>
            <span className="strip-track">
              <span className="strip-fill" style={{ height: `${Math.max(v * 100, v > 0 ? 6 : 0)}%` }} />
            </span>
            <span className="strip-key" aria-hidden="true">
              {COMPONENT_NAMES[k].charAt(0)}
            </span>
          </span>
        )
      })}
    </div>
  )
}

/** Meter list for the detail view: name, bar and value per component. */
export function ComponentMeters({ components }: { components: Components }) {
  return (
    <dl className="meters" data-testid="card-components">
      {COMPONENT_KEYS.map((k) => {
        const v = clamp(components[k])
        return (
          <div key={k} className="meter" title={COMPONENT_HELP[k]}>
            <dt>{COMPONENT_NAMES[k]}</dt>
            <dd>
              <span className="meter-track" aria-hidden="true">
                <span className="meter-fill" style={{ width: `${v * 100}%` }} />
              </span>
              <span className="meter-value">{v.toFixed(2)}</span>
            </dd>
          </div>
        )
      })}
    </dl>
  )
}
