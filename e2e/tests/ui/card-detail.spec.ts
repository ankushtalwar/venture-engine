import { clusterDetail, expect, listClusters, test } from '../../lib/fixtures';
import { detail } from '../../lib/ui';

// Journey 2: card detail shows verified quotes with source links and dates, evidence status badges
// (observed / inferred / derived / unknown), and hypotheses labelled as hypotheses.

test.use({ reseed: 'file' }); // read-only spec

const STATUS_NAMES = { observed: 'Observed', inferred: 'Inferred', derived: 'Derived', unknown: 'Unknown' } as const;

test('title, score, confidence and one row per signal', async ({ page, request }) => {
  const [top] = await listClusters(request);
  const d = await clusterDetail(request, top.id);
  await detail.goto(page, top.id);
  await expect(detail.heading(page)).toHaveText(d.title);
  await expect(page).toHaveTitle(new RegExp(d.title.slice(0, 20).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  await expect(detail.side(page)).toContainText(d.score.toFixed(2));
  await expect(page.getByTestId('card-confidence').first()).toHaveText(d.confidence);
  await expect(page.getByText(`${d.n_signals} signals from ${d.n_authors} distinct authors`)).toBeVisible();
  await expect(detail.signals(page)).toHaveCount(d.n_signals);
  for (const s of d.signals) await expect(detail.signal(page, s.id)).toBeVisible();
});

test('each signal shows its verbatim quote, a source link and a date', async ({ page, request }) => {
  const [top] = await listClusters(request);
  const d = await clusterDetail(request, top.id);
  await detail.goto(page, top.id);

  for (const s of d.signals) {
    const sig = detail.signal(page, s.id);
    await expect(sig.locator('blockquote')).toHaveText(s.fields.problem.quote);
    const link = sig.getByRole('link', { name: s.document.title });
    await expect(link).toHaveAttribute('href', s.document.url);
    await expect(link).toHaveAttribute('target', '_blank');
    await expect(link).toHaveAttribute('rel', /noopener/);
    await expect(sig.locator(`time[datetime="${s.document.published_at}"]`)).toBeVisible();
    await expect(sig.locator(`time[datetime="${s.document.published_at}"]`)).not.toHaveText('undated');
    await expect(sig).toContainText(s.document.source);
  }
});

test('the evidence section quotes sources verbatim, each linked and dated', async ({ page, request }) => {
  const [top] = await listClusters(request);
  const d = await clusterDetail(request, top.id);
  await detail.goto(page, top.id);

  const quotes = detail.quotes(page);
  const evidence = (d.card as any).evidence as { quote: string; url: string }[];
  expect(evidence.length).toBeGreaterThan(0);
  await expect(page.getByText('Every quote below was found verbatim in its source by code.')).toBeVisible();
  for (const e of evidence) {
    const q = quotes.filter({ hasText: e.quote }).first();
    await expect(q).toBeVisible();
    await expect(q.getByRole('link')).toHaveAttribute('href', e.url);
    await expect(q.locator('time')).not.toHaveText('undated');
  }
  for (const q of await quotes.all()) {
    await expect(q.locator('blockquote')).not.toBeEmpty();
    expect(await q.getByRole('link').getAttribute('href'), 'quotes link to http(s) sources').toMatch(/^https?:\/\//);
  }
});

test('every field of every signal carries its evidence status badge', async ({ page, request }) => {
  const [top] = await listClusters(request);
  const d = await clusterDetail(request, top.id);
  await detail.goto(page, top.id);

  for (const s of d.signals) {
    const sig = detail.signal(page, s.id);
    await detail.expandFields(sig);
    const expected = [
      ...Object.values(s.fields).map((e) => e.status),
      ...s.spend_evidence.map(() => 'observed' as const), // spend lines are always observed quotes
    ].sort();
    const badges = detail.evidenceBadges(sig.locator('details.signal-fields'));
    await expect(badges).toHaveCount(expected.length);
    const shown = (await badges.evaluateAll((els) => els.map((e) => e.getAttribute('data-status') ?? ''))).sort();
    expect(shown, `badges for signal ${s.id}`).toEqual(expected);
    for (const status of new Set(expected)) {
      const badge = sig.locator(`details [data-testid="evidence-status"][data-status="${status}"]`).first();
      await expect(badge).toBeVisible();
      await expect(badge).toHaveText(STATUS_NAMES[status]);
    }
    // Observed values show their quote; inferred ones show why the model thinks so.
    for (const [key, ev] of Object.entries(s.fields)) {
      if (ev.status === 'observed') await expect(sig.locator('details'), key).toContainText(ev.quote);
    }
  }
});

test('the status key explains all four statuses, including derived', async ({ page, request }) => {
  const [top] = await listClusters(request);
  await detail.goto(page, top.id);
  const key = page.getByRole('list', { name: 'Evidence status key' });
  for (const status of ['observed', 'inferred', 'derived', 'unknown'] as const) {
    await expect(key.locator(`[data-status="${status}"]`)).toHaveText(STATUS_NAMES[status]);
  }
  await expect(key).toContainText('model hypothesis');
});

test('the field table groups values with their status', async ({ page, request }) => {
  const [top] = await listClusters(request);
  await detail.goto(page, top.id);
  const table = page.getByRole('table', { name: /field by field/i });
  for (const field of ['Segment', 'Affected role', 'Likely buyer', 'How often it happens', 'Workaround today', 'Automatability']) {
    const row = table.getByRole('row').filter({ has: page.getByRole('rowheader', { name: field, exact: true }) });
    await expect(row.getByTestId('evidence-status').first()).toBeVisible();
  }
  // Automatability is always inferred (SPEC 3).
  const auto = table.getByRole('row').filter({ has: page.getByRole('rowheader', { name: 'Automatability' }) });
  await expect(auto.getByTestId('evidence-status')).toHaveAttribute('data-status', 'inferred');
});

test('inferred badges say they are model hypotheses', async ({ page, request }) => {
  const [top] = await listClusters(request);
  await detail.goto(page, top.id);
  const badge = page.locator('[data-testid="evidence-status"][data-status="inferred"]').first();
  await expect(badge).toHaveAttribute('title', /hypothesis/i);
});

test('the synthesis is marked as model-written and hypotheses are labelled', async ({ page, request }) => {
  const [top] = await listClusters(request);
  const d = await clusterDetail(request, top.id);
  await detail.goto(page, top.id);

  const model = detail.modelWritten(page);
  await expect(model.getByRole('heading', { name: 'Model-written summary' })).toBeVisible();
  await expect(model).toContainText('Written by the model');
  await expect(model).toContainText('not findings');
  await expect(model).toContainText(d.synthesis!.summary);
  await expect(model.getByRole('heading', { name: /hypotheses/i })).toBeVisible();
  await expect(detail.hypotheses(page)).toHaveText(d.synthesis!.hypotheses);
});

test('skeptic points are shown with their kind', async ({ page, request }) => {
  const [top] = await listClusters(request);
  const d = await clusterDetail(request, top.id);
  expect(d.skeptic?.points.length).toBeGreaterThan(0);
  await detail.goto(page, top.id);
  const skeptic = detail.skeptic(page);
  for (const p of d.skeptic!.points) {
    const item = skeptic.getByRole('listitem').filter({ hasText: p.claim });
    await expect(item).toBeVisible();
    await expect(item).toContainText(p.kind === 'hypothesis' ? 'Hypothesis' : 'From the evidence');
  }
});

test('an unknown card id shows "Card not found"', async ({ page }) => {
  await page.goto('/cards/000000000000');
  await expect(page.getByRole('heading', { level: 1, name: 'Card not found' })).toBeVisible();
  await page.getByRole('link', { name: /pain cards/i }).first().click();
  await expect(page).toHaveURL(/\/cards$/);
});
