import { Link } from 'react-router'
import { api } from '../api/client'
import type { TrendItem, TrendStatus } from '../api/types'
import { ErrorNote, Loading } from '../components/Feedback'
import { dateTime, plural, score, signed, TREND_NAMES } from '../lib/format'
import { useDocumentTitle } from '../lib/title'
import { useApi } from '../lib/useApi'

const GROUPS: { status: TrendStatus; help: string }[] = [
  { status: 'new', help: 'Clusters with no match in the previous run.' },
  { status: 'rising', help: 'Score went up by more than the rising threshold.' },
  { status: 'falling', help: 'Score went down by more than the falling threshold.' },
  { status: 'gone', help: 'Clusters from the previous run with no match now.' },
]

export function TrendsPage() {
  useDocumentTitle('Trends')
  const { data, error, loading, reload } = useApi(() => api.trends(), 'trends')

  return (
    <section className="page page-trends">
      <header className="page-head">
        <h1>Trends</h1>
        <p className="lede">
          How clusters moved between the two most recent clustering runs. Clusters are matched across runs by the
          signals they share, so ids may differ.
        </p>
      </header>
      {loading && !data && <Loading what="trends" />}
      <ErrorNote error={error} onRetry={reload} />
      {data && !data.previous_run_at && (
        <p className="empty">
          There is only one clustering run so far, so there is nothing to compare. Trends appear after the next run.
        </p>
      )}
      {data && data.previous_run_at && (
        <>
          <p className="run-compare">
            Run of <time dateTime={data.run_at ?? undefined}>{dateTime(data.run_at)}</time> compared with run of{' '}
            <time dateTime={data.previous_run_at}>{dateTime(data.previous_run_at)}</time>.
          </p>
          <div className="trend-groups">
            {GROUPS.map((g) => (
              <TrendGroup
                key={g.status}
                status={g.status}
                help={g.help}
                items={data.items.filter((i) => i.status === g.status)}
              />
            ))}
          </div>
        </>
      )}
    </section>
  )
}

function TrendGroup({ status, help, items }: { status: TrendStatus; help: string; items: TrendItem[] }) {
  const id = `h-trend-${status}`
  return (
    <section className={`trend-group tg-${status}`} aria-labelledby={id}>
      <h2 id={id}>
        {TREND_NAMES[status]} <span className="count">{items.length}</span>
      </h2>
      <p className="block-intro">{help}</p>
      {items.length === 0 ? (
        <p className="muted">None.</p>
      ) : (
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Cluster</th>
              <th scope="col" className="num">
                Score
              </th>
              <th scope="col" className="num">
                Before
              </th>
              <th scope="col" className="num">
                Change
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={`${item.status}-${item.cluster_id}`} data-testid="trend-row" data-status={item.status}>
                <td>
                  {item.status === 'gone' ? (
                    <span>{item.title}</span>
                  ) : (
                    <Link to={`/cards/${encodeURIComponent(item.cluster_id)}`}>{item.title}</Link>
                  )}
                </td>
                <td className="num">{score(item.score)}</td>
                <td className="num">{score(item.previous_score)}</td>
                <td className="num">
                  <Delta value={item.delta} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {items.length > 0 && <p className="visually-hidden">{plural(items.length, 'cluster')}</p>}
    </section>
  )
}

function Delta({ value }: { value: number | null }) {
  if (typeof value !== 'number') return <span className="muted">–</span>
  const dir = value > 0 ? 'up' : value < 0 ? 'down' : 'flat'
  const width = Math.min(Math.abs(value), 0.5) * 2 * 100
  return (
    <span className={`delta delta-${dir}`}>
      <span className="delta-bar" aria-hidden="true">
        <span style={{ width: `${width}%` }} />
      </span>
      {signed(value)}
    </span>
  )
}
