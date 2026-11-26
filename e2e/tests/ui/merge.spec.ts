import { clusterDetail, expect, getJson, listClusters, test, waitForWrite } from '../../lib/fixtures';
import { cards, detail, gotoPage } from '../../lib/ui';

// Journey 5: merging two clusters creates a new card and an override.

test('merge two cards from the list', async ({ page, request }) => {
  const before = await listClusters(request);
  const [x, y] = before.filter((c) => c.n_signals === 1);
  const signalIds = [
    ...(await clusterDetail(request, x.id)).signals.map((s) => s.id),
    ...(await clusterDetail(request, y.id)).signals.map((s) => s.id),
  ].sort();

  await cards.goto(page);
  await expect(cards.mergeButton(page)).toBeDisabled();
  await cards.select(cards.row(page, x.id)).check();
  await expect(page.getByText('1 card selected. Select one more to merge.')).toBeVisible();
  await expect(cards.mergeButton(page)).toBeDisabled();
  await cards.select(cards.row(page, y.id)).check();
  await expect(page.getByText('2 cards selected')).toBeVisible();

  const merged = waitForWrite(page, 'POST', /\/api\/clusters\/merge$/);
  await cards.mergeButton(page).click();
  const res = await merged;
  expect(res.status()).toBe(200);
  expect([...res.request().postDataJSON().ids].sort()).toEqual([x.id, y.id].sort());
  const newId = (await res.json()).id as string;

  // The UI opens the new card.
  await expect(page).toHaveURL(new RegExp(`/cards/${newId}$`));
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(detail.signals(page)).toHaveCount(2);

  // The list now has one card fewer; the new one is there and has no synthesis yet.
  await cards.goto(page);
  await expect(cards.rows(page)).toHaveCount(before.length - 1);
  await expect(cards.row(page, x.id)).toHaveCount(0);
  await expect(cards.row(page, y.id)).toHaveCount(0);
  await expect(cards.row(page, newId)).toContainText('No synthesis yet');

  // An override was stored with every signal of both cards.
  const overrides = await getJson<any[]>(request, '/api/overrides');
  expect(overrides).toHaveLength(1);
  expect(overrides[0].kind).toBe('merge');
  expect([...overrides[0].payload.signal_ids].sort()).toEqual(signalIds);

  await gotoPage(page, '/overrides', 'Overrides');
  const row = page.locator(`[data-testid="override-row"][data-override-id="${overrides[0].id}"]`);
  await expect(row).toContainText('Merge');
  await expect(row).toContainText('Keeps 2 signals together in one card');
});

test('after a merge the notice links to the overrides page', async ({ page, request }) => {
  const [x, y] = (await listClusters(request)).filter((c) => c.n_signals === 1);
  await cards.goto(page);
  await cards.select(cards.row(page, x.id)).check();
  await cards.select(cards.row(page, y.id)).check();
  await cards.mergeButton(page).click();
  await expect(page.getByText('Cards merged')).toBeVisible();
  await page.getByRole('link', { name: 'Undo in overrides' }).click();
  await expect(page).toHaveURL(/\/overrides$/);
  await expect(page.getByTestId('override-row')).toHaveCount(1);
});

test('clearing the selection disables merge again', async ({ page, request }) => {
  const [x, y] = await listClusters(request);
  await cards.goto(page);
  await cards.select(cards.row(page, x.id)).check();
  await cards.select(cards.row(page, y.id)).check();
  await expect(cards.mergeButton(page)).toBeEnabled();
  await page.getByRole('button', { name: 'Clear selection' }).click();
  await expect(cards.mergeButton(page)).toBeDisabled();
  await expect(cards.select(cards.row(page, x.id))).not.toBeChecked();
  expect(await getJson<any[]>(request, '/api/overrides')).toEqual([]);
});

test('deleting the merge override from the overrides page', async ({ page, request, authHeaders }) => {
  const singles = (await listClusters(request)).filter((c) => c.n_signals === 1);
  const res = await request.post('/api/clusters/merge', { data: { ids: [singles[0].id, singles[1].id] }, headers: authHeaders });
  expect(res.status()).toBe(200);
  const [override] = await getJson<any[]>(request, '/api/overrides');

  await gotoPage(page, '/overrides', 'Overrides');
  const deleted = waitForWrite(page, 'DELETE', new RegExp(`/api/overrides/${override.id}$`));
  await page.getByRole('button', { name: `Delete override ${override.id}` }).click();
  expect([200, 204]).toContain((await deleted).status());
  await expect(page.getByTestId('override-row')).toHaveCount(0);
  await expect(page.getByText('No overrides yet.')).toBeVisible();
  expect(await getJson<any[]>(request, '/api/overrides')).toEqual([]);
  await page.reload();
  await expect(page.getByText('No overrides yet.')).toBeVisible();
});
