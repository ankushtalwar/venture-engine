import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { api } from '../api/client'
import type { ClusterLabelFilter, ClusterSummary, Confidence } from '../api/types'
import { ConfidenceMark, LabelMark, TrendMark } from '../components/Badges'
import { ComponentStrip } from '../components/ComponentBars'
import { ErrorNote, Loading } from '../components/Feedback'
import { listJoin, plural, score, sourceType, span } from '../lib/format'
import { useNotify } from '../lib/notify'
import { useDocumentTitle } from '../lib/title'
import { errorText, useApi } from '../lib/useApi'

const PAGE = 50
const CONFIDENCE: Confidence[] = ['High', 'Medium', 'Low']
const LABELS: { value: ClusterLabelFilter; name: string }[] = [
  { value: 'accept', name: 'Accepted' },
  { value: 'reject', name: 'Rejected' },
  { value: 'unlabelled', name: 'Unlabelled' },
]

function isConfidence(v: string | null): v is Confidence {
  return v === 'High' || v === 'Medium' || v === 'Low'
}
function isLabelFilter(v: string | null): v is ClusterLabelFilter {
  return v === 'accept' || v === 'reject' || v === 'unlabelled'
}

export function CardsPage() {
  useDocumentTitle('Pain cards')
  const [params, setParams] = useSearchParams()
  const confidence = isConfidence(params.get('confidence')) ? (params.get('confidence') as Confidence) : ''
  const label = isLabelFilter(params.get('label')) ? (params.get('label') as ClusterLabelFilter) : ''
  const filterKey = `${confidence}|${label}`

  const { data, error, loading, reload, setData } = useApi(
    () => api.clusters({ limit: PAGE, offset: 0, confidence, label }),
    filterKey,
  )
  const [selected, setSelected] = useState<string[]>([])
  const [more, setMore] = useState<{ busy: boolean; error?: unknown }>({ busy: false })
  const [merge, setMerge] = useState<{ busy: boolean; error?: unknown }>({ busy: false })
  const navigate = useNavigate()
  const notify = useNotify()

  useEffect(() => setSelected([]), [filterKey])

  const setFilter = (key: 'confidence' | 'label', value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  const loadMore = async () => {
    if (!data) return
    setMore({ busy: true })
    try {
      const page = await api.clusters({ limit: PAGE, offset: data.items.length, confidence, label })
      setData((prev) => (prev ? { items: [...prev.items, ...page.items], total: page.total } : page))
      setMore({ busy: false })
    } catch (err) {
      setMore({ busy: false, error: err })
    }
  }

  const doMerge = async () => {
    if (selected.length < 2) return
    setMerge({ busy: true })
    try {
      const merged = await api.merge(selected)
      notify({ text: 'Cards merged', link: { to: '/overrides', label: 'Undo in overrides' } })
      navigate(`/cards/${encodeURIComponent(merged.id)}`)
    } catch (err) {
      setMerge({ busy: false, error: err })
    }
  }

  const items = data?.items ?? []
  const filtered = Boolean(confidence || label)

  return (
    <section className="page page-cards">
      <header className="page-head">
        <h1>Pain cards</h1>
        <p className="lede">
          Clusters of verified complaints, ranked by score. Accept the ones worth customer interviews, reject the
          rest.
        </p>
      </header>

      <form className="filters" onSubmit={(e) => e.preventDefault()} aria-label="Filter cards">
        <div className="field">
          <label htmlFor="f-confidence">Confidence</label>
          <select id="f-confidence" value={confidence} onChange={(e) => setFilter('confidence', e.target.value)}>
            <option value="">All</option>
            {CONFIDENCE.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="f-label">Label</label>
          <select id="f-label" value={label} onChange={(e) => setFilter('label', e.target.value)}>
            <option value="">All</option>
            {LABELS.map((l) => (
              <option key={l.value} value={l.value}>
                {l.name}
              </option>
            ))}
          </select>
        </div>
        {data && (
          <p className="filters-count" aria-live="polite">
            {filtered ? `${plural(data.total, 'card')} match` : `${plural(data.total, 'card')}`}
          </p>
        )}
      </form>

      {loading && !data && <Loading what="cards" />}
      <ErrorNote error={error} onRetry={reload} />

      {data && items.length === 0 && (
        <div className="empty">
          {filtered ? (
            <>
              <p>No cards match these filters.</p>
              <button type="button" className="btn" onClick={() => setParams(new URLSearchParams(), { replace: true })}>
                Clear filters
              </button>
            </>
          ) : (
            <p>
              No cards yet. Run <code>uv run paindisc run</code> (or <code>uv run paindisc demo</code> for synthetic
              data) to collect and cluster signals.
            </p>
          )}
        </div>
      )}

      {items.length > 0 && (
        <>
          <div className="card-list-key" aria-hidden="true">
            <span>Score</span>
            <span>Problem</span>
            <span title="Breadth, Spend, Frequency, Underserved, Intensity, Recency, Automatability">Components</span>
            <span>Decision</span>
          </div>
          <ol className="card-list" aria-busy={loading}>
            {items.map((c, i) => (
              <CardRow key={c.id} card={c} rank={i + 1} checked={selected.includes(c.id)} onToggle={toggle} />
            ))}
          </ol>
        </>
      )}

      {data && items.length < data.total && (
        <div className="more">
          <button type="button" className="btn" onClick={loadMore} disabled={more.busy}>
            {more.busy ? 'Loading…' : `Show ${Math.min(PAGE, data.total - items.length)} more`}
          </button>
          <ErrorNote error={more.error} />
        </div>
      )}

      {items.length > 1 && (
        <div className={`selection-bar${selected.length ? ' is-active' : ''}`} role="region" aria-label="Merge cards">
          <p className="selection-text">
            {selected.length === 0
              ? 'Select two or more cards that describe the same problem to merge them.'
              : selected.length === 1
                ? '1 card selected. Select one more to merge.'
                : `${plural(selected.length, 'card')} selected`}
          </p>
          {merge.error ? (
            <p className="selection-error" role="alert">
              {errorText(merge.error)}
            </p>
          ) : null}
          {selected.length > 0 && (
            <button type="button" className="btn" onClick={() => setSelected([])}>
              Clear selection
            </button>
          )}
          <button
            type="button"
            className="btn btn-primary"
            onClick={doMerge}
            disabled={selected.length < 2 || merge.busy}
          >
            {merge.busy ? 'Merging…' : 'Merge selected'}
          </button>
        </div>
      )}
    </section>
  )
}

function CardRow({
  card,
  rank,
  checked,
  onToggle,
}: {
  card: ClusterSummary
  rank: number
  checked: boolean
  onToggle: (id: string) => void
}) {
  const sources = listJoin(card.source_types.map((t) => sourceType(t).toLowerCase()))
  const checkId = `sel-${card.id}`
  return (
    <li
      className={`card-row${checked ? ' is-selected' : ''}${card.label ? ` is-${card.label}` : ''}`}
      data-testid="card-row"
      data-cluster-id={card.id}
    >
      <span className="row-select">
        <input
          id={checkId}
          type="checkbox"
          checked={checked}
          onChange={() => onToggle(card.id)}
          aria-label={`Select ${card.title}`}
        />
      </span>
      <span className="row-rank" aria-label={`Rank ${rank}`}>
        {rank}
      </span>
      <span className="row-score">
        <span className="score-num" data-testid="card-score">
          {score(card.score)}
        </span>
        <ConfidenceMark value={card.confidence} />
      </span>
      <span className="row-main">
        <Link to={`/cards/${encodeURIComponent(card.id)}`} className="row-title">
          {card.title}
        </Link>
        <span className="row-meta">
          {plural(card.n_signals, 'signal')} from {plural(card.n_authors, 'author')}
          {sources && <> in {sources}</>}, {span(card.first_seen, card.last_seen)}
          {!card.has_synthesis && <span className="row-flag">No synthesis yet</span>}
        </span>
      </span>
      <span className="row-components">
        <ComponentStrip components={card.components} />
      </span>
      <span className="row-state">
        <LabelMark value={card.label} />
        <TrendMark value={card.trend} />
      </span>
    </li>
  )
}
