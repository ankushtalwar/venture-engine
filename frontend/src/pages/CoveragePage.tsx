import { api } from '../api/client'
import type { ArmStat, SourceStat } from '../api/types'
import { ErrorNote, Loading } from '../components/Feedback'
import { useDocumentTitle } from '../lib/title'
import { useApi } from '../lib/useApi'

const n = (v: number | null | undefined) => (typeof v === 'number' ? v : 0)

function pct(num: number, den: number): string {
  return den > 0 ? `${Math.round((num / den) * 100)}%` : '–'
}

export function CoveragePage() {
  useDocumentTitle('Coverage')
  const { data, error, loading, reload } = useApi(() => api.stats(), 'stats')

  return (
    <section className="page page-coverage">
      <header className="page-head">
        <h1>Coverage</h1>
        <p className="lede">
          What each source has produced, and which search queries the planner keeps picking. A query arm earns its
          keep when the documents it finds yield verified signals.
        </p>
      </header>
      {loading && !data && <Loading what="coverage" />}
      <ErrorNote error={error} onRetry={reload} />
      {data && (
        <>
          <SourceTable rows={data.sources ?? []} />
          <ArmTable rows={data.arms ?? []} />
        </>
      )}
    </section>
  )
}

function SourceTable({ rows }: { rows: SourceStat[] }) {
  const max = Math.max(1, ...rows.map((r) => n(r.docs)))
  return (
    <table className="data coverage">
      <caption>Per source</caption>
      <thead>
        <tr>
          <th scope="col">Source</th>
          <th scope="col" className="num">
            Documents
          </th>
          <th scope="col" className="num">
            Duplicates
          </th>
          <th scope="col" className="num">
            Triaged
          </th>
          <th scope="col" className="num">
            Pain
          </th>
          <th scope="col" className="num">
            Verified signals
          </th>
          <th scope="col" className="num">
            Dropped
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && (
          <tr>
            <td colSpan={7} className="muted">
              Nothing collected yet.
            </td>
          </tr>
        )}
        {rows.map((r) => (
          <tr key={r.source} data-testid="coverage-source-row">
            <th scope="row">{r.source}</th>
            <td className="num">
              <span className="inline-bar" aria-hidden="true">
                <span style={{ width: `${(n(r.docs) / max) * 100}%` }} />
              </span>
              {n(r.docs)}
            </td>
            <td className="num">{n(r.duplicates)}</td>
            <td className="num">{n(r.triaged)}</td>
            <td className="num">{n(r.pain)}</td>
            <td className="num">{n(r.signals)}</td>
            <td className="num" title="Signals dropped because the problem quote was not found in the document">
              {n(r.dropped)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ArmTable({ rows }: { rows: ArmStat[] }) {
  return (
    <table className="data arms">
      <caption>Top query arms</caption>
      <thead>
        <tr>
          <th scope="col">Query</th>
          <th scope="col">Source</th>
          <th scope="col" className="num">
            Runs
          </th>
          <th scope="col" className="num">
            Empty runs
          </th>
          <th scope="col" className="num">
            Documents
          </th>
          <th scope="col" className="num">
            With a verified signal
          </th>
          <th scope="col" className="num">
            Yield
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && (
          <tr>
            <td colSpan={7} className="muted">
              No query arms have run yet.
            </td>
          </tr>
        )}
        {rows.map((r) => (
          <tr key={r.arm_id} data-testid="arm-row">
            <td className="query">{r.query}</td>
            <td>{r.source}</td>
            <td className="num">{n(r.runs)}</td>
            <td className="num">{n(r.empty_runs)}</td>
            <td className="num">{n(r.docs)}</td>
            <td className="num">{n(r.verified)}</td>
            <td className="num">{pct(n(r.verified), n(r.docs))}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
