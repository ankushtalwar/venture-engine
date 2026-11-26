import { api, reportUrl } from '../api/client'
import { ErrorNote, Loading } from '../components/Feedback'
import { bytes, dateTime } from '../lib/format'
import { useDocumentTitle } from '../lib/title'
import { useApi } from '../lib/useApi'

export function ReportsPage() {
  useDocumentTitle('Reports')
  const { data, error, loading, reload } = useApi(() => api.reports(), 'reports')
  const rows = data ? [...data].sort((a, b) => (b.modified ?? '').localeCompare(a.modified ?? '')) : []

  return (
    <section className="page page-reports">
      <header className="page-head">
        <h1>Reports</h1>
        <p className="lede">Pain Reports written by the pipeline, newest first. Each opens in a new tab.</p>
      </header>
      {loading && !data && <Loading what="reports" />}
      <ErrorNote error={error} onRetry={reload} />
      {data && rows.length === 0 && (
        <p className="empty">
          No reports yet. <code>uv run paindisc report</code> writes one to the reports folder.
        </p>
      )}
      {rows.length > 0 && (
        <table className="data reports">
          <thead>
            <tr>
              <th scope="col">File</th>
              <th scope="col">Format</th>
              <th scope="col" className="num">
                Size
              </th>
              <th scope="col">Written</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name} data-testid="report-row">
                <th scope="row">
                  <a href={reportUrl(r.name)} target="_blank" rel="noopener noreferrer">
                    {r.name}
                  </a>
                </th>
                <td>{r.kind === 'html' ? 'HTML' : 'Markdown'}</td>
                <td className="num">{bytes(r.size)}</td>
                <td>
                  <time dateTime={r.modified}>{dateTime(r.modified)}</time>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
