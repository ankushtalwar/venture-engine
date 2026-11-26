import type {
  ClusterDetail,
  ClusterSummary,
  Evidence,
  Health,
  Override,
  ReportFile,
  SignalView,
  SourceRow,
  Stats,
  Trends,
} from '../api/types'

const unknown: Evidence = { value: '', status: 'unknown', quote: '', basis: '' }
const obs = (value: string, quote: string): Evidence => ({ value, status: 'observed', quote, basis: '' })
const inf = (value: string, basis: string): Evidence => ({ value, status: 'inferred', quote: '', basis })

export const health: Health = { status: 'ok', store: 'sqlite', llm_mock: true, version: '0.1.0' }

const components = {
  breadth: 0.58,
  spend: 0.4,
  frequency: 1,
  underserved: 0,
  intensity: 0.67,
  recency: 1,
  automatability: 0,
}

export const summaries: ClusterSummary[] = [
  {
    id: 'c-invoices',
    title: 'Small accounting practices still re-key vendor invoices from pdfs by…',
    canonical: 'small accounting practices still re-key vendor invoices from pdfs by hand',
    score: 0.57,
    confidence: 'Medium',
    n_signals: 3,
    n_authors: 3,
    source_types: ['forum', 'search_snippet'],
    first_seen: '2026-05-30T00:00:00Z',
    last_seen: '2026-10-20T00:00:00Z',
    components,
    label: null,
    trend: 'rising',
    has_synthesis: true,
  },
  {
    id: 'c-moveout',
    title: 'Move-out inspections are a nightmare: we take photos on phones, then…',
    canonical: 'move-out inspections are a nightmare',
    score: 0.42,
    confidence: 'Low',
    n_signals: 1,
    n_authors: 1,
    source_types: ['forum'],
    first_seen: '2026-08-18T00:00:00Z',
    last_seen: '2026-08-18T00:00:00Z',
    components: { ...components, breadth: 0.19, spend: 0.2 },
    label: 'accept',
    trend: 'new',
    has_synthesis: true,
  },
  {
    id: 'c-ap',
    title: 'Our accounts payable team spends hours every week copying invoice…',
    canonical: 'our accounts payable team spends hours every week copying invoice lines',
    score: 0.37,
    confidence: 'Low',
    n_signals: 1,
    n_authors: 1,
    source_types: ['issue_tracker'],
    first_seen: '2026-07-03T00:00:00Z',
    last_seen: '2026-07-03T00:00:00Z',
    components: { ...components, breadth: 0.19, spend: 0 },
    label: 'reject',
    trend: null,
    has_synthesis: false,
  },
]

function signal(
  id: string,
  quote: string,
  extra: Partial<SignalView['fields']> = {},
  doc: Partial<SignalView['document']> = {},
): SignalView {
  return {
    id,
    label: null,
    specificity: 'A',
    intensity: 2,
    author_type: 'operator',
    canonical: quote.toLowerCase(),
    fields: {
      problem: obs(quote, quote),
      segment: inf('unspecified business', 'Not stated; the author runs a small firm.'),
      affected_role: inf('author', 'The author describes their own work.'),
      likely_buyer: inf('owner or operations lead', 'Small firms buy their own tools.'),
      workaround: obs('re-key by hand', quote),
      occurrence_freq: obs('weekly', 'Every Friday'),
      financial_impact: unknown,
      existing_tool_complaint: unknown,
      automatability: inf('high', 'Structured input, fixed rules.'),
      ...extra,
    },
    spend_evidence: [],
    document: {
      id: `d-${id}`,
      source: 'demo-forum',
      source_type: 'forum',
      url: `https://example.com/forum/${id}`,
      title: `Thread ${id}`,
      published_at: '2026-09-12T00:00:00Z',
      ...doc,
    },
  }
}

export const AUTHOR_HASH = 'deadbeefcafe1234'

export function detail(overrides: Partial<ClusterDetail> = {}): ClusterDetail {
  const s1 = signal('s1aaaaaaaaaa', 'Every Friday we still do this manually: we re-key vendor invoices from PDFs into QuickBooks.', {
    financial_impact: {
      value: '$600/month',
      status: 'derived',
      quote: 'we pay a VA $15/hr',
      basis: '40 hours/month x $15/hr stated in the post',
    },
  })
  // A backend leak must never reach the screen: the UI renders only the fields it knows.
  ;(s1.document as unknown as Record<string, unknown>).author_hash = AUTHOR_HASH
  s1.spend_evidence = [{ type: 'freelancer', amount: '$15/hr', quote: 'we pay a VA $15/hr to help at month-end.' }]
  const s2 = signal(
    's2bbbbbbbbbb',
    'I run AP for a property management company and we re-key vendor invoices from PDFs every day.',
    { occurrence_freq: obs('daily', 'every day') },
    { url: 'https://example.com/forum/a4', published_at: '2026-05-30T00:00:00Z' },
  )
  const s3 = signal(
    's3cccccccccc',
    'Small accounting practices still re-key vendor invoices from PDFs by hand <img src=x onerror="window.__pwned=1">.',
    {},
    { source: 'demo-search', source_type: 'search_snippet', url: 'javascript:alert(1)', title: 'Blog post' },
  )
  return {
    ...summaries[0],
    synthesis: {
      title: 'Small accounting practices still re-key vendor invoices from pdfs by…',
      summary: '3 verified signals from 3 distinct authors across forum, search_snippet.',
      hypotheses: ['Hypothesis: the people describing this are the ones who would pay to fix it.'],
    },
    skeptic: {
      points: [
        { claim: 'Existing tools may already solve this well for most firms.', kind: 'hypothesis', signal_ids: [] },
        { claim: 'Fewer than three distinct authors: could be one loud voice.', kind: 'evidence', signal_ids: ['s1aaaaaaaaaa'] },
      ],
    },
    card: {
      id: 'c-invoices',
      canonical: 'small accounting practices still re-key vendor invoices from pdfs by hand',
      span: '2026-05-30 → 2026-10-20',
      frequency: 'weekly (1x), daily (1x)',
      frequency_quotes: [],
      financial: [
        {
          value: '$1,850',
          status: 'observed',
          quote: 'Last year we lost $1,850 in small claims because our inspection photos weren’t dated.',
          basis: '',
          url: 'https://example.com/forum/b1',
          label: 'demo-forum · 2026-08-18',
        },
      ],
      workarounds: [
        {
          quote: 'Every Friday we still do this manually: we re-key vendor invoices from PDFs into QuickBooks.',
          url: 'https://example.com/forum/a1',
          label: 'demo-forum · 2026-09-12',
        },
      ],
      tool_complaints: [],
      spend: [
        {
          type: 'staff',
          amount: '$22/hour',
          quote: 'We hired a part-time clerk at $22/hour just for invoice data entry.',
          url: 'https://example.com/forum/a4',
          label: 'demo-forum · 2026-05-30',
        },
      ],
      evidence: [
        {
          quote: 'Every Friday we still do this manually: we re-key vendor invoices from PDFs into QuickBooks.',
          url: 'https://example.com/forum/a1',
          label: 'demo-forum · 2026-09-12',
        },
      ],
      pricing: [
        {
          vendor: 'ExampleBooks',
          tiers: 'Starter $38 per month; Plus $75 per month',
          url: 'https://example.com/examplebooks/pricing@2026-07',
          label: 'demo-pricing · 2026-07-01',
        },
      ],
    },
    signals: [s1, s2, s3],
    ...overrides,
  }
}

export const trends: Trends = {
  run_at: '2026-11-26T08:00:00Z',
  previous_run_at: '2026-11-19T08:00:00Z',
  items: [
    { cluster_id: 'c-moveout', title: 'Move-out inspections', status: 'new', score: 0.42, previous_score: null, delta: null },
    { cluster_id: 'c-invoices', title: 'Vendor invoices re-keyed', status: 'rising', score: 0.57, previous_score: 0.41, delta: 0.16 },
    { cluster_id: 'c-ap', title: 'AP copying invoice lines', status: 'falling', score: 0.37, previous_score: 0.45, delta: -0.08 },
    { cluster_id: 'c-old', title: 'Timesheet chasing', status: 'gone', score: null, previous_score: 0.3, delta: null },
  ],
}

export const overrides: Override[] = [
  { id: 1, kind: 'merge', payload: { signal_ids: ['s1', 's2', 's3'] }, created_at: '2026-11-25T10:00:00Z' },
  { id: 2, kind: 'split', payload: { signal_ids: ['s4'] }, created_at: '2026-11-25T11:00:00Z' },
]

export const sources: SourceRow[] = [
  { source: 'reddit', allowed: false, route: 'official_api', domains: 'oauth.reddit.com', reviewed: '', reason: 'Needs Data API approval' },
  { source: 'hn', allowed: true, route: 'public_api', domains: 'hn.algolia.com', reviewed: '2026-11-01 reviewer', reason: '' },
]

export const stats: Stats = {
  sources: [
    { source: 'demo-forum', docs: 6, duplicates: 1, triaged: 5, pain: 4, signals: 4, dropped: 1 },
    { source: 'demo-issues', docs: 2, duplicates: 0, triaged: 2, pain: 1, signals: 1, dropped: 0 },
  ],
  arms: [
    { arm_id: 'a1', source: 'hn', query: 're-key invoices', runs: 3, empty_runs: 1, docs: 4, verified: 2 },
  ],
}

export const reports: ReportFile[] = [
  { name: 'demo-pain-report.md', kind: 'md', size: 9000, modified: '2026-11-26T00:24:00Z' },
  { name: 'demo-pain-report.html', kind: 'html', size: 21000, modified: '2026-11-26T00:25:00Z' },
]
