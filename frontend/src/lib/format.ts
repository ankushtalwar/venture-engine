import type { ClusterLabel, ComponentKey, EvidenceStatus, SignalFieldKey, Trend, TrendStatus } from '../api/types'

const SOURCE_TYPES: Record<string, string> = {
  forum: 'Forum',
  issue_tracker: 'Issue tracker',
  job_post: 'Job post',
  search_snippet: 'Search snippet',
  pricing_page: 'Pricing page',
  review: 'Review',
}

export function sourceType(value: string): string {
  return SOURCE_TYPES[value] ?? sentence(value.replace(/_/g, ' '))
}

export function sentence(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value
}

/** "forum, search_snippet" -> "forums and search snippets" style list for prose. */
export function listJoin(items: string[]): string {
  if (items.length <= 1) return items.join('')
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

export function score(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '–'
}

export function signed(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '–'
  const s = Math.abs(value).toFixed(2)
  return value > 0 ? `+${s}` : value < 0 ? `−${s}` : s
}

const dateFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
const dateTimeFmt = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

function parse(value: string | null | undefined): Date | null {
  if (!value) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : d
}

export function date(value: string | null | undefined): string {
  const d = parse(value)
  return d ? dateFmt.format(d) : 'undated'
}

export function dateTime(value: string | null | undefined): string {
  const d = parse(value)
  return d ? dateTimeFmt.format(d) : 'unknown time'
}

export function span(first: string | null, last: string | null): string {
  if (!first && !last) return 'undated'
  const a = date(first)
  const b = date(last)
  return a === b ? a : `${a} to ${b}`
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export const COMPONENT_NAMES: Record<ComponentKey, string> = {
  breadth: 'Breadth',
  spend: 'Spend',
  frequency: 'Frequency',
  underserved: 'Underserved',
  intensity: 'Intensity',
  recency: 'Recency',
  automatability: 'Automatability',
}

export const COMPONENT_HELP: Record<ComponentKey, string> = {
  breadth: 'Distinct authors across source types',
  spend: 'Signals with observed spend or financial impact',
  frequency: 'Best observed per-business frequency',
  underserved: 'Complaints about existing tools',
  intensity: 'How strongly people describe it (inferred)',
  recency: 'Share of evidence from the last 12 months',
  automatability: 'Inferred; weight 0 by default',
}

export const FIELD_NAMES: Record<SignalFieldKey, string> = {
  problem: 'Problem',
  segment: 'Segment',
  affected_role: 'Affected role',
  likely_buyer: 'Likely buyer',
  workaround: 'Workaround today',
  occurrence_freq: 'How often it happens',
  financial_impact: 'Financial impact',
  existing_tool_complaint: 'Complaint about existing tools',
  automatability: 'Automatability',
}

export const STATUS_NAMES: Record<EvidenceStatus, string> = {
  observed: 'Observed',
  inferred: 'Inferred',
  derived: 'Derived',
  unknown: 'Unknown',
}

export const STATUS_HELP: Record<EvidenceStatus, string> = {
  observed: 'Stated in the source; the quote was found verbatim by code',
  inferred: 'Model hypothesis, not stated in the source',
  derived: 'Computed from a stated quantity and a stated or cited rate',
  unknown: 'Not stated in the source',
}

export const LABEL_NAMES: Record<ClusterLabel, string> = { accept: 'Accepted', reject: 'Rejected' }

export function labelName(label: ClusterLabel | null): string {
  return label ? LABEL_NAMES[label] : 'Unlabelled'
}

export const TREND_NAMES: Record<Trend | TrendStatus, string> = {
  new: 'New',
  rising: 'Rising',
  falling: 'Falling',
  steady: 'Steady',
  gone: 'Gone',
}

/** report.card() refs carry "<source> · <date>" in one label; split it so the UI can set them apart. */
export function splitRefLabel(label: string): { source: string; when: string } {
  const i = label.lastIndexOf(' · ')
  return i === -1 ? { source: label, when: '' } : { source: label.slice(0, i), when: label.slice(i + 3) }
}
