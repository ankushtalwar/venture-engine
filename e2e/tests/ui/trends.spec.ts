import { expect, getJson, listClusters, test } from '../../lib/fixtures';
import { isIsoDate } from '../../lib/shape';
import { cards, gotoPage } from '../../lib/ui';

// Journey 8: trends. Seed `trends` = demo + a second, tighter clustering run: the 3-signal card splits
// into three single-signal cards (new), the old card is gone, the other four are steady.

test.use({ seed: 'trends', reseed: 'file' });

test('API: the latest run is compared with the previous one', async ({ request }) => {
  const t = await getJson<any>(request, '/api/trends');
  expect(isIsoDate(t.run_at)).toBe(true);
  expect(isIsoDate(t.previous_run_at)).toBe(true);
  expect(Date.parse(t.run_at)).toBeGreaterThan(Date.parse(t.previous_run_at));
  const count = (s: string) => t.items.filter((i: any) => i.status === s).length;
  expect(count('new')).toBe(3);
  expect(count('gone')).toBe(1);
  for (const i of t.items) {
    if (i.status === 'new') expect([i.previous_score, i.delta]).toEqual([null, null]);
    if (i.status === 'gone') expect(i.previous_score).toBeGreaterThan(0);
    if (i.status === 'rising') expect(i.delta).toBeGreaterThan(0);
    if (i.status === 'falling') expect(i.delta).toBeLessThan(0);
  }
  // Cluster summaries carry the same trend.
  const byId = Object.fromEntries((await listClusters(request)).map((c) => [c.id, c.trend]));
  for (const i of t.items.filter((x: any) => x.status === 'new')) expect(byId[i.cluster_id]).toBe('new');
});

test('the trends page groups new, rising, falling and gone clusters', async ({ page, request }) => {
  const t = await getJson<any>(request, '/api/trends');
  await gotoPage(page, '/trends', 'Trends');
  await expect(page.getByText(/^Run of .* compared with run of /)).toBeVisible();
  await expect(page.locator(`time[datetime="${t.run_at}"]`)).toBeVisible();
  await expect(page.locator(`time[datetime="${t.previous_run_at}"]`)).toBeVisible();

  for (const [status, name] of [['new', 'New'], ['rising', 'Rising'], ['falling', 'Falling'], ['gone', 'Gone']]) {
    const items = t.items.filter((i: any) => i.status === status);
    const group = page.getByRole('region', { name: new RegExp(`^${name}`) });
    await expect(group.getByRole('heading', { level: 2 })).toHaveText(`${name} ${items.length}`);
    await expect(group.getByTestId('trend-row')).toHaveCount(items.length);
    if (items.length === 0) await expect(group).toContainText('None.');
    for (const item of items) {
      const row = group.getByTestId('trend-row').filter({ hasText: item.title });
      await expect(row).toHaveAttribute('data-status', status);
      await expect(row).toContainText(item.score === null ? '–' : item.score.toFixed(2));
    }
  }
  // Steady clusters are not listed.
  await expect(page.locator('[data-testid="trend-row"][data-status="steady"]')).toHaveCount(0);
});

test('new clusters link to their card; gone clusters do not', async ({ page, request }) => {
  const t = await getJson<any>(request, '/api/trends');
  const fresh = t.items.find((i: any) => i.status === 'new');
  const gone = t.items.find((i: any) => i.status === 'gone');
  await gotoPage(page, '/trends', 'Trends');
  await expect(page.locator('[data-testid="trend-row"][data-status="gone"]').getByRole('link')).toHaveCount(0);
  await expect(page.locator('[data-testid="trend-row"][data-status="gone"]')).toContainText(gone.title);
  await page.locator('[data-testid="trend-row"][data-status="new"]').getByRole('link', { name: fresh.title }).click();
  await expect(page).toHaveURL(new RegExp(`/cards/${fresh.cluster_id}$`));
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(fresh.title);
});

test('cards show their trend badge in the list', async ({ page, request }) => {
  const all = await listClusters(request);
  await cards.goto(page);
  for (const c of all) {
    const badge = cards.row(page, c.id).getByTestId('card-trend');
    if (c.trend) await expect(badge).toContainText(c.trend.charAt(0).toUpperCase() + c.trend.slice(1));
    else await expect(badge).toHaveCount(0);
  }
  expect(all.filter((c) => c.trend === 'new')).toHaveLength(3);
});
