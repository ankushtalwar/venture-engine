import { useState } from 'react'
import { api } from '../api/client'
import type { Override } from '../api/types'
import { ErrorNote, Loading } from '../components/Feedback'
import { dateTime, plural } from '../lib/format'
import { useNotify } from '../lib/notify'
import { useDocumentTitle } from '../lib/title'
import { useApi } from '../lib/useApi'

export function OverridesPage() {
  useDocumentTitle('Overrides')
  const { data, error, loading, reload, setData } = useApi(() => api.overrides(), 'overrides')
  const [busy, setBusy] = useState<number | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)
  const notify = useNotify()

  const remove = async (o: Override) => {
    setBusy(o.id)
    setActionError(null)
    try {
      await api.deleteOverride(o.id)
      setData((prev) => (prev ?? []).filter((x) => x.id !== o.id))
      notify('Override deleted')
    } catch (err) {
      setActionError(err)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="page page-overrides">
      <header className="page-head">
        <h1>Overrides</h1>
        <p className="lede">
          Your merges and splits. They are re-applied after every automatic clustering run. Delete one to let
          automatic clustering decide again on the next run.
        </p>
      </header>
      {loading && !data && <Loading what="overrides" />}
      <ErrorNote error={error} onRetry={reload} />
      <ErrorNote error={actionError} />
      {data && data.length === 0 && <p className="empty">No overrides yet. Merge or split cards to create one.</p>}
      {data && data.length > 0 && (
        <table className="data">
          <caption className="visually-hidden">Overrides, oldest first</caption>
          <thead>
            <tr>
              <th scope="col" className="num">
                Id
              </th>
              <th scope="col">Kind</th>
              <th scope="col">What it does</th>
              <th scope="col">Created</th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {data.map((o) => {
              const ids = Array.isArray(o.payload?.signal_ids) ? o.payload.signal_ids : []
              return (
                <tr key={o.id} data-testid="override-row" data-override-id={o.id}>
                  <td className="num">{o.id}</td>
                  <td>
                    <span className={`kind kind-${o.kind}`}>{o.kind === 'merge' ? 'Merge' : 'Split'}</span>
                  </td>
                  <td>
                    {o.kind === 'merge'
                      ? `Keeps ${plural(ids.length, 'signal')} together in one card`
                      : `Moves ${plural(ids.length, 'signal')} into a card of ${ids.length === 1 ? 'its' : 'their'} own`}
                    {ids.length > 0 && (
                      <details className="ids">
                        <summary>Signal ids</summary>
                        <p className="sig-id">{ids.join(', ')}</p>
                      </details>
                    )}
                  </td>
                  <td>
                    <time dateTime={o.created_at}>{dateTime(o.created_at)}</time>
                  </td>
                  <td className="actions">
                    <button
                      type="button"
                      className="btn btn-small btn-danger"
                      disabled={busy === o.id}
                      aria-label={`Delete override ${o.id}`}
                      onClick={() => remove(o)}
                    >
                      {busy === o.id ? 'Deleting…' : 'Delete'}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}
