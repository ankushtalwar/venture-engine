import { api } from '../api/client'
import { ErrorNote, Loading } from '../components/Feedback'
import { plural } from '../lib/format'
import { useDocumentTitle } from '../lib/title'
import { useApi } from '../lib/useApi'

export function SourcesPage() {
  useDocumentTitle('Sources')
  const { data, error, loading, reload } = useApi(() => api.sources(), 'sources')
  const rows = data ? [...data].sort((a, b) => Number(b.allowed) - Number(a.allowed) || a.source.localeCompare(b.source)) : []
  const allowed = rows.filter((r) => r.allowed).length

  return (
    <section className="page page-sources">
      <header className="page-head">
        <h1>Sources</h1>
        <p className="lede">
          The registry in config/registry.yaml is the only access control. A source stays blocked until someone
          reads its terms, records the review and sets it to allowed.
        </p>
      </header>
      {loading && !data && <Loading what="sources" />}
      <ErrorNote error={error} onRetry={reload} />
      {data && (
        <>
          <p className="run-compare">
            {plural(allowed, 'source')} allowed, {rows.length - allowed} blocked.
          </p>
          <table className="data sources">
            <thead>
              <tr>
                <th scope="col">Source</th>
                <th scope="col">Status</th>
                <th scope="col">Route</th>
                <th scope="col">Domains</th>
                <th scope="col">Terms reviewed</th>
                <th scope="col">Reason or notes</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.source}
                  data-testid="source-row"
                  data-source={r.source}
                  data-allowed={String(r.allowed)}
                  className={r.allowed ? 'is-allowed' : 'is-blocked'}
                >
                  <th scope="row">{r.source}</th>
                  <td>
                    <span className={r.allowed ? 'access access-allowed' : 'access access-blocked'}>
                      {r.allowed ? 'Allowed' : 'Blocked'}
                    </span>
                  </td>
                  <td>{r.route.replace(/_/g, ' ')}</td>
                  <td className="domains">{r.domains}</td>
                  <td>{r.reviewed || <span className="muted">not recorded</span>}</td>
                  <td className="reason">{r.reason || <span className="muted">–</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  )
}
