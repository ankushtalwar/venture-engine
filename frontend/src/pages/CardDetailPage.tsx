import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ApiError, api } from '../api/client'
import type {
  ClusterDetail,
  ClusterLabel,
  Evidence,
  EvidenceStatus,
  SignalFieldKey,
  SignalLabel,
  SignalView,
} from '../api/types'
import { SIGNAL_FIELD_KEYS } from '../api/types'
import { ConfidenceMark, LabelMark, TrendMark } from '../components/Badges'
import { ComponentMeters } from '../components/ComponentBars'
import { EvidenceBadge, EvidenceLine, EvidenceValue } from '../components/Evidence'
import { ErrorNote, Loading } from '../components/Feedback'
import { Quote } from '../components/Quote'
import { ExternalLink, SourceLink } from '../components/SourceLink'
import { date, FIELD_NAMES, listJoin, plural, score, sentence, sourceType, span, splitRefLabel } from '../lib/format'
import { useNotify } from '../lib/notify'
import { useDocumentTitle } from '../lib/title'
import { useApi } from '../lib/useApi'

export function CardDetailPage() {
  const { id = '' } = useParams()
  const { data, error, loading, reload, setData } = useApi(() => api.cluster(id), id)
  useDocumentTitle(data ? sentence(data.title) : 'Pain card')

  // The route element is reused when :id changes (after a merge or split), so never show the previous card.
  if (loading && (!data || data.id !== id)) {
    return (
      <section className="page">
        <BackLink />
        <Loading what="card" />
      </section>
    )
  }
  if (error) {
    const gone = error instanceof ApiError && error.status === 404
    return (
      <section className="page">
        <BackLink />
        <h1>{gone ? 'Card not found' : 'Card could not be loaded'}</h1>
        {gone ? (
          <p>
            No card has this id any more. Card ids change when clustering re-runs, and after a merge or split.
          </p>
        ) : (
          <ErrorNote error={error} onRetry={reload} />
        )}
      </section>
    )
  }
  if (!data) return null
  // Keyed by id so per-card state (selection, busy flags, the note) starts fresh on every card.
  return <CardDetail key={data.id} detail={data} onChange={(d) => setData(d)} />
}

function BackLink() {
  return (
    <Link to="/cards" className="back">
      <span aria-hidden="true">‹ </span>Pain cards
    </Link>
  )
}

function CardDetail({ detail, onChange }: { detail: ClusterDetail; onChange: (d: ClusterDetail) => void }) {
  const sources = listJoin(detail.source_types.map((t) => sourceType(t).toLowerCase()))
  return (
    <article className="page detail" data-testid="card-detail" data-cluster-id={detail.id}>
      <BackLink />
      <header className="detail-head">
        <h1>{detail.title}</h1>
        {detail.canonical && detail.canonical.trim() !== detail.title.trim() && (
          <p className="canonical">
            <span className="canonical-key">Canonical statement</span> {detail.canonical}
          </p>
        )}
        <dl className="facts">
          <div>
            <dt>Confidence</dt>
            <dd>
              <ConfidenceMark value={detail.confidence} />
            </dd>
          </div>
          <div>
            <dt>Evidence</dt>
            <dd>
              {plural(detail.n_signals, 'signal')} from {plural(detail.n_authors, 'distinct author')}
            </dd>
          </div>
          <div>
            <dt>Sources</dt>
            <dd>{sources ? sentence(sources) : 'none'}</dd>
          </div>
          <div>
            <dt>Seen</dt>
            <dd>{span(detail.first_seen, detail.last_seen)}</dd>
          </div>
          {detail.trend && (
            <div>
              <dt>Trend</dt>
              <dd>
                <TrendMark value={detail.trend} />
              </dd>
            </div>
          )}
        </dl>
      </header>

      <div className="detail-grid">
        <div className="detail-main">
          <ModelWritten detail={detail} />
          <SkepticSection detail={detail} />
          <EvidenceSection detail={detail} />
          <MoneySection detail={detail} />
          <SignalsSection detail={detail} onChange={onChange} />
        </div>
        <aside className="detail-side" aria-label="Decision and score">
          <DecisionPanel detail={detail} onChange={onChange} />
          <section className="side-block" aria-labelledby="h-score">
            <h2 id="h-score">Score</h2>
            <p className="big-score">{score(detail.score)}</p>
            <ComponentMeters components={detail.components} />
            <p className="side-note">Weights live in config/scoring.yaml. Confidence is computed, not asked of a model.</p>
          </section>
        </aside>
      </div>
    </article>
  )
}

// ------------------------------------------------------------------ decision

function DecisionPanel({ detail, onChange }: { detail: ClusterDetail; onChange: (d: ClusterDetail) => void }) {
  const notify = useNotify()
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)

  const setLabel = async (label: ClusterLabel | null) => {
    setBusy(label ?? 'clear')
    setError(null)
    try {
      const summary = await api.labelCluster(detail.id, label, label ? note.trim() || undefined : undefined)
      onChange({ ...detail, ...summary, label: summary?.label ?? label })
      notify(label === 'accept' ? 'Card accepted' : label === 'reject' ? 'Card rejected' : 'Label cleared')
      setNote('')
    } catch (err) {
      setError(err)
    } finally {
      setBusy(null)
    }
  }

  const resynthesize = async () => {
    setBusy('synth')
    setError(null)
    try {
      const next = await api.synthesize(detail.id)
      onChange(next)
      notify('Synthesis updated')
    } catch (err) {
      setError(err)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="side-block decision" aria-labelledby="h-decision">
      <h2 id="h-decision">Decision</h2>
      <p className="decision-now">
        Now: <LabelMark value={detail.label} />
      </p>
      <div className="field">
        <label htmlFor="decision-note">Note (optional)</label>
        <textarea
          id="decision-note"
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Why, for the next reviewer"
        />
      </div>
      <div className="decision-buttons">
        <button
          type="button"
          className="btn btn-accept"
          aria-pressed={detail.label === 'accept'}
          disabled={busy !== null}
          onClick={() => setLabel('accept')}
        >
          Accept
        </button>
        <button
          type="button"
          className="btn btn-reject"
          aria-pressed={detail.label === 'reject'}
          disabled={busy !== null}
          onClick={() => setLabel('reject')}
        >
          Reject
        </button>
        <button
          type="button"
          className="btn"
          disabled={busy !== null || detail.label === null}
          onClick={() => setLabel(null)}
        >
          Clear label
        </button>
      </div>
      <div className="resynth">
        <button type="button" className="btn" disabled={busy !== null} onClick={resynthesize}>
          {busy === 'synth' ? 'Writing…' : 'Re-synthesize'}
        </button>
        <p className="side-note">Rewrites the title, summary and hypotheses from the current signals.</p>
      </div>
      <ErrorNote error={error} />
    </section>
  )
}

// ------------------------------------------------------------------ model-written

function ModelWritten({ detail }: { detail: ClusterDetail }) {
  const s = detail.synthesis
  return (
    <section className="block model" data-testid="model-written" aria-labelledby="h-model">
      <h2 id="h-model">Model-written summary</h2>
      <p className="model-tag">
        <span className="model-key">Written by the model</span> from the verified quotes on this page. Read it as
        hypotheses to test, not findings.
      </p>
      {s ? (
        <>
          {s.summary && <p className="model-summary">{s.summary}</p>}
          {s.hypotheses.length > 0 && (
            <>
              <h3>Hypotheses to test in interviews</h3>
              <ul className="hypotheses">
                {s.hypotheses.map((h, i) => (
                  <li key={i} data-testid="hypothesis">
                    {h}
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      ) : (
        <p className="model-empty">
          No summary yet. Use Re-synthesize to write one from the signals below.
        </p>
      )}
    </section>
  )
}

function SkepticSection({ detail }: { detail: ClusterDetail }) {
  const points = detail.skeptic?.points ?? []
  return (
    <section className="block skeptic" aria-labelledby="h-skeptic">
      <h2 id="h-skeptic">Skeptic</h2>
      <p className="block-intro">The case against this card, from a second model pass.</p>
      {points.length === 0 ? (
        <p className="muted">No skeptic points yet.</p>
      ) : (
        <ul className="skeptic-list">
          {points.map((p, i) => (
            <li key={i} className={`skeptic-${p.kind}`}>
              <span className={`skeptic-kind skeptic-kind-${p.kind}`}>
                {p.kind === 'evidence' ? 'From the evidence' : 'Hypothesis'}
              </span>
              <span className="skeptic-claim">{p.claim}</span>
              {p.signal_ids.length > 0 && (
                <span className="skeptic-refs">
                  {p.signal_ids.map((sid) => (
                    <a key={sid} href={`#signal-${sid}`} className="sig-ref">
                      Signal {shortId(sid)}
                    </a>
                  ))}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// ------------------------------------------------------------------ evidence

const SUMMARY_FIELDS: SignalFieldKey[] = [
  'segment',
  'affected_role',
  'likely_buyer',
  'occurrence_freq',
  'workaround',
  'existing_tool_complaint',
  'automatability',
]

interface FieldGroup {
  value: string
  status: EvidenceStatus
  count: number
}

const STATUS_ORDER: Record<EvidenceStatus, number> = { observed: 0, derived: 1, inferred: 2, unknown: 3 }

export function fieldGroups(signals: SignalView[], key: SignalFieldKey): FieldGroup[] {
  const groups = new Map<string, FieldGroup>()
  for (const s of signals) {
    const ev: Evidence | undefined = s.fields?.[key]
    if (!ev) continue
    const known = ev.status !== 'unknown' && ev.value.trim() !== ''
    const status: EvidenceStatus = known ? ev.status : 'unknown'
    const norm = known ? ev.value.trim().replace(/\s+/g, ' ') : ''
    const k = `${status}|${norm.toLowerCase()}`
    const g = groups.get(k)
    if (g) g.count += 1
    else groups.set(k, { value: norm, status, count: 1 })
  }
  return [...groups.values()].sort(
    (a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || b.count - a.count,
  )
}

function EvidenceSection({ detail }: { detail: ClusterDetail }) {
  const card = detail.card
  const quoteGroups = [
    { title: 'The problem, in their words', items: card?.evidence ?? [] },
    { title: 'What they do today', items: card?.workarounds ?? [] },
    { title: 'Complaints about existing tools', items: card?.tool_complaints ?? [] },
    { title: 'How often it happens', items: card?.frequency_quotes ?? [] },
  ].filter((g) => g.items.length > 0)

  return (
    <section className="block evidence" aria-labelledby="h-evidence">
      <h2 id="h-evidence">Evidence</h2>
      <StatusKey />
      <table className="fields">
        <caption>What the signals say, field by field</caption>
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">Values across {plural(detail.signals.length, 'signal')}</th>
          </tr>
        </thead>
        <tbody>
          {SUMMARY_FIELDS.map((key) => {
            const groups = fieldGroups(detail.signals, key)
            return (
              <tr key={key}>
                <th scope="row">{FIELD_NAMES[key]}</th>
                <td>
                  {groups.length === 0 ? (
                    <EvidenceValue value="" status="unknown" />
                  ) : (
                    <ul className="field-values">
                      {groups.map((g) => (
                        <li key={`${g.status}|${g.value}`}>
                          <EvidenceBadge status={g.status} />
                          <EvidenceValue value={g.value} status={g.status} />
                          {detail.signals.length > 1 && <span className="count">{plural(g.count, 'signal')}</span>}
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {quoteGroups.length > 0 ? (
        <div className="quotes">
          <p className="block-intro">Every quote below was found verbatim in its source by code.</p>
          {quoteGroups.map((g) => (
            <div key={g.title} className="quote-group">
              <h3>{g.title}</h3>
              {g.items.map((q, i) => (
                <Quote key={i} quote={q.quote} url={q.url} label={q.label} />
              ))}
            </div>
          ))}
        </div>
      ) : (
        <p className="muted">No verified quotes on this card.</p>
      )}
    </section>
  )
}

function StatusKey() {
  return (
    <ul className="status-key" aria-label="Evidence status key">
      <li>
        <EvidenceBadge status="observed" /> stated in the source, quote checked by code
      </li>
      <li>
        <EvidenceBadge status="inferred" /> model hypothesis
      </li>
      <li>
        <EvidenceBadge status="derived" /> computed from stated figures
      </li>
      <li>
        <EvidenceBadge status="unknown" /> not stated
      </li>
    </ul>
  )
}

function MoneySection({ detail }: { detail: ClusterDetail }) {
  const card = detail.card
  const financial = card?.financial ?? []
  const spend = card?.spend ?? []
  const pricing = card?.pricing ?? []
  const empty = financial.length === 0 && spend.length === 0 && pricing.length === 0
  return (
    <section className="block money" aria-labelledby="h-money">
      <h2 id="h-money">Money</h2>
      {empty && (
        <p className="muted">
          No sources state a cost, spend or price for this problem. Figures appear here only when a source states
          them.
        </p>
      )}
      {financial.length > 0 && (
        <div className="money-group">
          <h3>Financial impact</h3>
          <ul className="money-list">
            {financial.map((f, i) => {
              const ref = splitRefLabel(f.label)
              return (
                <li key={i} className="money-item">
                  <div className="money-head">
                    <EvidenceBadge status={f.status} />
                    <EvidenceValue value={f.value} status={f.status} />
                  </div>
                  {f.quote && <q className="ev-quote">{f.quote}</q>}
                  {f.status === 'derived' && f.basis && <p className="ev-basis">Formula: {f.basis}</p>}
                  <SourceLink url={f.url} source={ref.source} when={ref.when !== 'undated' ? ref.when : null} />
                </li>
              )
            })}
          </ul>
        </div>
      )}
      {spend.length > 0 && (
        <div className="money-group">
          <h3>Money or people already spent on it</h3>
          <ul className="money-list">
            {spend.map((s, i) => {
              const ref = splitRefLabel(s.label)
              return (
                <li key={i} className="money-item">
                  <div className="money-head">
                    <EvidenceBadge status="observed" />
                    <span className="spend-type">{sentence(s.type)}</span>
                    {s.amount && <EvidenceValue value={s.amount} status="observed" />}
                  </div>
                  <q className="ev-quote">{s.quote}</q>
                  <SourceLink url={s.url} source={ref.source} when={ref.when !== 'undated' ? ref.when : null} />
                </li>
              )
            })}
          </ul>
        </div>
      )}
      {pricing.length > 0 && (
        <div className="money-group">
          <h3>Prices on record for tools they mention</h3>
          <table className="prices">
            <thead>
              <tr>
                <th scope="col">Vendor</th>
                <th scope="col">Tiers</th>
                <th scope="col">Pricing page</th>
              </tr>
            </thead>
            <tbody>
              {pricing.map((p, i) => {
                const ref = splitRefLabel(p.label)
                return (
                  <tr key={i}>
                    <th scope="row">{p.vendor}</th>
                    <td>{p.tiers}</td>
                    <td>
                      <SourceLink url={p.url} source={ref.source} when={ref.when !== 'undated' ? ref.when : null} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

// ------------------------------------------------------------------ signals

const SIGNAL_LABELS: { value: SignalLabel | null; name: string }[] = [
  { value: 'valid', name: 'Valid' },
  { value: 'invalid', name: 'Invalid' },
  { value: 'duplicate', name: 'Duplicate' },
  { value: null, name: 'None' },
]

function shortId(id: string): string {
  return id.slice(0, 6)
}

function SignalsSection({ detail, onChange }: { detail: ClusterDetail; onChange: (d: ClusterDetail) => void }) {
  const [selected, setSelected] = useState<string[]>([])
  const [splitting, setSplitting] = useState(false)
  const [splitError, setSplitError] = useState<unknown>(null)
  const navigate = useNavigate()
  const notify = useNotify()
  const total = detail.signals.length
  const canSplit = selected.length > 0 && selected.length < total

  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  const split = async () => {
    if (!canSplit) return
    setSplitting(true)
    setSplitError(null)
    try {
      const result = await api.split(detail.id, selected)
      const [first, second] = result.clusters
      notify({
        text: 'Card split into 2',
        link: second ? { to: `/cards/${encodeURIComponent(second.id)}`, label: `Open the other card` } : undefined,
      })
      navigate(first ? `/cards/${encodeURIComponent(first.id)}` : '/cards')
    } catch (err) {
      setSplitError(err)
      setSplitting(false)
    }
  }

  const onLabelled = (signalId: string, label: SignalLabel | null) => {
    onChange({ ...detail, signals: detail.signals.map((s) => (s.id === signalId ? { ...s, label } : s)) })
  }

  return (
    <section className="block signals" aria-labelledby="h-signals">
      <h2 id="h-signals">Signals</h2>
      <p className="block-intro">
        Each signal is one verified complaint. Label it, or select the ones that belong to a different problem and
        split them into their own card.
      </p>
      <div className="split-bar">
        <p className="split-text">
          {total < 2
            ? 'A card needs two or more signals to split.'
            : selected.length === 0
              ? 'No signals selected.'
              : selected.length >= total
                ? 'Leave at least one signal on this card.'
                : `${plural(selected.length, 'signal')} selected to move into a new card.`}
        </p>
        <button type="button" className="btn btn-primary" disabled={!canSplit || splitting} onClick={split}>
          {splitting ? 'Splitting…' : 'Split selected signals'}
        </button>
      </div>
      <ErrorNote error={splitError} />
      <ol className="signal-list">
        {detail.signals.map((s) => (
          <SignalRow
            key={s.id}
            signal={s}
            checked={selected.includes(s.id)}
            onToggle={toggle}
            onLabelled={onLabelled}
            canSelect={total > 1}
          />
        ))}
      </ol>
    </section>
  )
}

function SignalRow({
  signal,
  checked,
  onToggle,
  onLabelled,
  canSelect,
}: {
  signal: SignalView
  checked: boolean
  onToggle: (id: string) => void
  onLabelled: (id: string, label: SignalLabel | null) => void
  canSelect: boolean
}) {
  const notify = useNotify()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const problem = signal.fields?.problem
  const doc = signal.document

  const setLabel = async (label: SignalLabel | null) => {
    if (label === signal.label) return
    const previous = signal.label
    onLabelled(signal.id, label)
    setBusy(true)
    setError(null)
    try {
      const res = await api.labelSignal(signal.id, label)
      onLabelled(signal.id, res?.label ?? label)
      notify(label ? `Signal labelled ${label}` : 'Signal label cleared')
    } catch (err) {
      onLabelled(signal.id, previous)
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li
      id={`signal-${signal.id}`}
      className={`signal-row${checked ? ' is-selected' : ''}${signal.label ? ` sig-${signal.label}` : ''}`}
      data-testid="signal-row"
      data-signal-id={signal.id}
    >
      <div className="signal-top">
        {canSelect && (
          <label className="signal-select">
            <input type="checkbox" checked={checked} onChange={() => onToggle(signal.id)} />
            <span className="visually-hidden">Select signal for split</span>
          </label>
        )}
        <div className="signal-body">
          <blockquote className="signal-quote">{problem?.quote || problem?.value || signal.canonical}</blockquote>
          <p className="signal-source">
            <ExternalLink href={doc.url} className="signal-doc">
              {doc.title || doc.url || 'Source document'}
            </ExternalLink>
            <span className="signal-src">
              {doc.source}, {sourceType(doc.source_type).toLowerCase()}
            </span>
            <SourceDate when={doc.published_at} />
          </p>
          <dl className="signal-facts">
            <div>
              <dt>Specificity</dt>
              <dd title="A: names a concrete task and context; C: vague">{signal.specificity}</dd>
            </div>
            <div>
              <dt>Intensity</dt>
              <dd title="1 to 3, inferred">{signal.intensity} of 3</dd>
            </div>
            <div>
              <dt>Author is</dt>
              <dd>{signal.author_type === 'unknown' ? 'not clear' : `${article(signal.author_type)} ${signal.author_type}`}</dd>
            </div>
            <div>
              <dt>Signal</dt>
              <dd className="sig-id">{shortId(signal.id)}</dd>
            </div>
          </dl>
        </div>
        <div className="signal-label" role="radiogroup" aria-label="Signal label">
          {SIGNAL_LABELS.map((opt) => (
            <label key={opt.name} className={`seg seg-${opt.value ?? 'none'}`}>
              <input
                type="radio"
                name={`label-${signal.id}`}
                checked={signal.label === opt.value}
                disabled={busy}
                onChange={() => setLabel(opt.value)}
              />
              <span>{opt.name}</span>
            </label>
          ))}
        </div>
      </div>
      <ErrorNote error={error} />
      <details className="signal-fields">
        <summary>All fields for this signal</summary>
        {signal.canonical && (
          <p className="signal-canonical">
            <span className="canonical-key">Canonical statement</span> {signal.canonical}
          </p>
        )}
        <dl className="field-lines">
          {SIGNAL_FIELD_KEYS.map((key) => {
            const ev = signal.fields?.[key]
            if (!ev) return null
            return (
              <div key={key}>
                <dt>{FIELD_NAMES[key]}</dt>
                <dd>
                  <EvidenceLine ev={ev} />
                </dd>
              </div>
            )
          })}
          {signal.spend_evidence.length > 0 && (
            <div>
              <dt>Spend</dt>
              <dd>
                <ul className="spend-lines">
                  {signal.spend_evidence.map((sp, i) => (
                    <li key={i}>
                      <EvidenceBadge status="observed" /> {sentence(sp.type)}
                      {sp.amount && (
                        <>
                          {' '}
                          <EvidenceValue value={sp.amount} status="observed" />
                        </>
                      )}
                      <q className="ev-quote">{sp.quote}</q>
                    </li>
                  ))}
                </ul>
              </dd>
            </div>
          )}
        </dl>
      </details>
    </li>
  )
}

function SourceDate({ when }: { when: string | null }) {
  return (
    <time className="source-date" dateTime={when ?? undefined}>
      {date(when)}
    </time>
  )
}

function article(word: string): string {
  return /^[aeiou]/i.test(word) ? 'an' : 'a'
}

