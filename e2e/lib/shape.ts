import { expect } from '@playwright/test';

/**
 * Small structural checks against API contract v1 (TEAM_PLAN.md). They report the JSON path of the
 * first mismatch so a failure names the field, not just "objects differ".
 */

const ISO = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/;

export function isIsoDate(v: unknown): boolean {
  return typeof v === 'string' && ISO.test(v) && !Number.isNaN(Date.parse(v));
}

export function expectKeys(obj: unknown, keys: string[], where: string) {
  expect(obj, `${where} should be an object`).toEqual(expect.any(Object));
  const actual = Object.keys(obj as object);
  const missing = keys.filter((k) => !actual.includes(k));
  expect(missing, `${where} is missing keys`).toEqual([]);
}

export function expectEnum(v: unknown, allowed: readonly unknown[], where: string) {
  expect(allowed, `${where} = ${JSON.stringify(v)}`).toContain(v);
}

export function expectNumberIn(v: unknown, lo: number, hi: number, where: string) {
  expect(typeof v, `${where} type`).toBe('number');
  expect(v as number, where).toBeGreaterThanOrEqual(lo);
  expect(v as number, where).toBeLessThanOrEqual(hi);
}

export function expectNullableIso(v: unknown, where: string) {
  if (v === null) return;
  expect(isIsoDate(v), `${where} = ${JSON.stringify(v)} should be ISO 8601`).toBe(true);
}

const COMPONENTS = ['breadth', 'spend', 'frequency', 'underserved', 'intensity', 'recency', 'automatability'];
const SUMMARY_KEYS = ['id', 'title', 'canonical', 'score', 'confidence', 'n_signals', 'n_authors', 'source_types',
  'first_seen', 'last_seen', 'components', 'label', 'trend', 'has_synthesis'];
const SIGNAL_FIELDS = ['problem', 'segment', 'affected_role', 'likely_buyer', 'workaround', 'occurrence_freq',
  'financial_impact', 'existing_tool_complaint', 'automatability'];
export const EVIDENCE_STATUSES = ['observed', 'inferred', 'derived', 'unknown'] as const;

export function expectClusterSummary(c: any, where = 'ClusterSummary') {
  expectKeys(c, SUMMARY_KEYS, where);
  expect(c.id, `${where}.id`).toMatch(/^[0-9a-f]{6,64}$/);
  expect(typeof c.title, `${where}.title`).toBe('string');
  expect(typeof c.canonical, `${where}.canonical`).toBe('string');
  expectNumberIn(c.score, 0, 10, `${where}.score`);
  expectEnum(c.confidence, ['High', 'Medium', 'Low'], `${where}.confidence`);
  expect(Number.isInteger(c.n_signals) && c.n_signals >= 1, `${where}.n_signals = ${c.n_signals}`).toBe(true);
  expect(Number.isInteger(c.n_authors) && c.n_authors >= 1, `${where}.n_authors = ${c.n_authors}`).toBe(true);
  expect(c.n_authors, `${where}.n_authors <= n_signals`).toBeLessThanOrEqual(c.n_signals);
  expect(Array.isArray(c.source_types) && c.source_types.length > 0, `${where}.source_types`).toBe(true);
  expectNullableIso(c.first_seen, `${where}.first_seen`);
  expectNullableIso(c.last_seen, `${where}.last_seen`);
  expectKeys(c.components, COMPONENTS, `${where}.components`);
  for (const k of COMPONENTS) expectNumberIn(c.components[k], 0, 1, `${where}.components.${k}`);
  expectEnum(c.label, ['accept', 'reject', null], `${where}.label`);
  expectEnum(c.trend, ['new', 'rising', 'falling', 'steady', null], `${where}.trend`);
  expect(typeof c.has_synthesis, `${where}.has_synthesis`).toBe('boolean');
}

export function expectEvidence(e: any, where: string) {
  expectKeys(e, ['value', 'status', 'quote', 'basis'], where);
  expectEnum(e.status, EVIDENCE_STATUSES, `${where}.status`);
  if (e.status === 'observed') {
    expect(typeof e.quote === 'string' && e.quote.length > 0, `${where}: observed needs a quote`).toBe(true);
  }
  if (e.status === 'derived') {
    expect(typeof e.basis === 'string' && e.basis.length > 0, `${where}: derived needs a basis`).toBe(true);
  }
}

export function expectSignalView(s: any, where = 'SignalView') {
  expectKeys(s, ['id', 'label', 'specificity', 'intensity', 'author_type', 'canonical', 'fields', 'spend_evidence', 'document'], where);
  expectEnum(s.label, ['valid', 'invalid', 'duplicate', null], `${where}.label`);
  expectKeys(s.fields, SIGNAL_FIELDS, `${where}.fields`);
  for (const f of SIGNAL_FIELDS) expectEvidence(s.fields[f], `${where}.fields.${f}`);
  // Evidence contract: problem is always observed (verify.py drops the signal otherwise).
  expect(s.fields.problem.status, `${where}.fields.problem.status`).toBe('observed');
  // financial_impact is never inferred (SPEC section 3, rule 4); derived only for financial impact.
  expect(s.fields.financial_impact.status, `${where}.fields.financial_impact.status`).not.toBe('inferred');
  for (const f of SIGNAL_FIELDS.filter((x) => x !== 'financial_impact')) {
    expect(s.fields[f].status, `${where}.fields.${f}: derived is only allowed for financial_impact`).not.toBe('derived');
  }
  // automatability is always inferred (or unknown).
  expectEnum(s.fields.automatability.status, ['inferred', 'unknown'], `${where}.fields.automatability.status`);
  expect(Array.isArray(s.spend_evidence), `${where}.spend_evidence`).toBe(true);
  s.spend_evidence.forEach((x: any, i: number) => expectKeys(x, ['type', 'amount', 'quote'], `${where}.spend_evidence[${i}]`));
  expectKeys(s.document, ['id', 'source', 'source_type', 'url', 'title', 'published_at'], `${where}.document`);
  expectNullableIso(s.document.published_at, `${where}.document.published_at`);
}

export function expectClusterDetail(c: any, where = 'ClusterDetail') {
  expectClusterSummary(c, where);
  expectKeys(c, ['synthesis', 'skeptic', 'card', 'signals'], where);
  if (c.synthesis !== null) {
    expectKeys(c.synthesis, ['title', 'summary', 'hypotheses'], `${where}.synthesis`);
    expect(Array.isArray(c.synthesis.hypotheses), `${where}.synthesis.hypotheses`).toBe(true);
  }
  expect(c.has_synthesis, `${where}.has_synthesis matches synthesis`).toBe(c.synthesis !== null);
  if (c.skeptic !== null) {
    expectKeys(c.skeptic, ['points'], `${where}.skeptic`);
    c.skeptic.points.forEach((p: any, i: number) => {
      expectKeys(p, ['claim', 'kind', 'signal_ids'], `${where}.skeptic.points[${i}]`);
      expectEnum(p.kind, ['evidence', 'hypothesis'], `${where}.skeptic.points[${i}].kind`);
    });
  }
  expect(c.card, `${where}.card`).toEqual(expect.any(Object));
  expect(c.signals.length, `${where}.signals length = n_signals`).toBe(c.n_signals);
  c.signals.forEach((s: any, i: number) => expectSignalView(s, `${where}.signals[${i}]`));
}

/** Every object key anywhere in a JSON value (for "no forbidden fields" checks). */
export function allKeys(value: unknown, out: Set<string> = new Set()): Set<string> {
  if (Array.isArray(value)) value.forEach((v) => allKeys(v, out));
  else if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) {
      out.add(k);
      allKeys(v, out);
    }
  }
  return out;
}

/** Field names that would mean the tool is proposing solutions, or exposing people. */
export const FORBIDDEN_KEY = /solution|product|idea|pitch|proposal|recommend|author_hash|username|^author$|user_?name|handle/i;
