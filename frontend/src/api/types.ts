// Types for HTTP API contract v1 (docs/api.md). Hand-written to match the FastAPI models.
// Deliberately absent: author hashes. The UI never receives or shows who wrote a document.

export type Confidence = 'High' | 'Medium' | 'Low'
export type ClusterLabel = 'accept' | 'reject'
export type ClusterLabelFilter = ClusterLabel | 'unlabelled'
export type SignalLabel = 'valid' | 'invalid' | 'duplicate'
export type Trend = 'new' | 'rising' | 'falling' | 'steady'
export type TrendStatus = 'new' | 'rising' | 'falling' | 'gone'
export type EvidenceStatus = 'observed' | 'inferred' | 'derived' | 'unknown'

export const COMPONENT_KEYS = [
  'breadth',
  'spend',
  'frequency',
  'underserved',
  'intensity',
  'recency',
  'automatability',
] as const
export type ComponentKey = (typeof COMPONENT_KEYS)[number]
export type Components = Partial<Record<ComponentKey, number>> & Record<string, number>

export interface Health {
  status: string
  store: 'sqlite' | 'postgres' | string
  llm_mock: boolean
  version: string
}

export interface ClusterSummary {
  id: string
  title: string
  canonical: string
  score: number
  confidence: Confidence
  n_signals: number
  n_authors: number
  source_types: string[]
  first_seen: string | null
  last_seen: string | null
  components: Components
  label: ClusterLabel | null
  trend: Trend | null
  has_synthesis: boolean
}

export interface ClusterList {
  items: ClusterSummary[]
  total: number
}

export interface Synthesis {
  title: string
  summary: string
  hypotheses: string[]
}

export interface SkepticPoint {
  claim: string
  kind: 'evidence' | 'hypothesis'
  signal_ids: string[]
}

export interface Skeptic {
  points: SkepticPoint[]
}

/** A link to a source document as report.card() builds it: label is "<source> · <date>". */
export interface CardRef {
  url: string
  label: string
}

export interface CardQuote extends CardRef {
  quote: string
}

export interface CardSpend extends CardRef {
  type: string
  amount: string
  quote: string
}

export interface CardFinancial extends CardRef {
  value: string
  status: 'observed' | 'derived'
  quote: string
  basis: string
}

export interface CardPricing extends CardRef {
  vendor: string
  tiers: string
}

/** report.card() output. Only the fields the UI reads are listed. */
export interface Card {
  id: string
  canonical: string
  span: string
  frequency: string
  frequency_quotes: CardQuote[]
  financial: CardFinancial[]
  workarounds: CardQuote[]
  tool_complaints: CardQuote[]
  spend: CardSpend[]
  evidence: CardQuote[]
  pricing: CardPricing[]
}

export interface Evidence {
  value: string
  status: EvidenceStatus
  quote: string
  basis: string
}

export const SIGNAL_FIELD_KEYS = [
  'problem',
  'segment',
  'affected_role',
  'likely_buyer',
  'workaround',
  'occurrence_freq',
  'financial_impact',
  'existing_tool_complaint',
  'automatability',
] as const
export type SignalFieldKey = (typeof SIGNAL_FIELD_KEYS)[number]

export interface SignalDocument {
  id: string
  source: string
  source_type: string
  url: string
  title: string
  published_at: string | null
}

export interface SignalView {
  id: string
  label: SignalLabel | null
  specificity: 'A' | 'B' | 'C'
  intensity: 1 | 2 | 3
  author_type: string
  canonical: string
  fields: Record<SignalFieldKey, Evidence>
  spend_evidence: { type: string; amount: string; quote: string }[]
  document: SignalDocument
}

export interface ClusterDetail extends ClusterSummary {
  synthesis: Synthesis | null
  skeptic: Skeptic | null
  card: Card
  signals: SignalView[]
}

export interface SplitResult {
  clusters: ClusterSummary[]
}

export interface Override {
  id: number
  kind: 'merge' | 'split'
  payload: { signal_ids?: string[] } & Record<string, unknown>
  created_at: string
}

export interface TrendItem {
  cluster_id: string
  title: string
  status: TrendStatus
  score: number | null
  previous_score: number | null
  delta: number | null
}

export interface Trends {
  run_at: string | null
  previous_run_at: string | null
  items: TrendItem[]
}

/** Registry.summary() row. */
export interface SourceRow {
  source: string
  allowed: boolean
  route: string
  domains: string
  reviewed: string
  reason: string
}

/** store.source_stats() row. */
export interface SourceStat {
  source: string
  docs: number
  duplicates: number | null
  triaged: number | null
  pain: number | null
  signals: number | null
  dropped: number | null
}

/** store.arm_stats() row. */
export interface ArmStat {
  arm_id: string
  source: string
  query: string
  runs: number
  empty_runs: number
  docs: number
  verified: number
}

export interface Stats {
  sources: SourceStat[]
  arms: ArmStat[]
}

export interface ReportFile {
  name: string
  kind: 'md' | 'html'
  size: number
  modified: string
}
