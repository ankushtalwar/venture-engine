import { clusterDetail, expect, getJson, listClusters, test, waitForWrite } from '../../lib/fixtures';
import { cards, detail } from '../../lib/ui';

// Journey 4: signal labels persist. Journey 6: split moves signals into a new card.

test('signal labels are saved and survive a reload', async ({ page, request }) => {
  const [top] = await listClusters(request);
  const d = await clusterDetail(request, top.id);
  const [s1, s2, s3] = d.signals;
  await detail.goto(page, top.id);
  await expect(detail.signalLabelOption(detail.signal(page, s1.id), 'None')).toBeChecked();

  let saved = waitForWrite(page, 'POST', new RegExp(`/api/signals/${s1.id}/label$`));
  await detail.signalLabelOption(detail.signal(page, s1.id), 'Invalid').check();
  expect((await saved).status()).toBe(200);
  saved = waitForWrite(page, 'POST', new RegExp(`/api/signals/${s2.id}/label$`));
  await detail.signalLabelOption(detail.signal(page, s2.id), 'Duplicate').check();
  expect((await saved).status()).toBe(200);

  await page.reload();
  await expect(detail.signals(page)).toHaveCount(d.n_signals);
  await expect(detail.signalLabelOption(detail.signal(page, s1.id), 'Invalid')).toBeChecked();
  await expect(detail.signalLabelOption(detail.signal(page, s2.id), 'Duplicate')).toBeChecked();
  await expect(detail.signalLabelOption(detail.signal(page, s3.id), 'None')).toBeChecked();

  const after = await clusterDetail(request, top.id);
  expect(after.signals.find((s) => s.id === s1.id)?.label).toBe('invalid');
  expect(after.signals.find((s) => s.id === s2.id)?.label).toBe('duplicate');
  expect(after.signals.find((s) => s.id === s3.id)?.label).toBeNull();

  // Change of mind, then clear.
  saved = waitForWrite(page, 'POST', new RegExp(`/api/signals/${s1.id}/label$`));
  await detail.signalLabelOption(detail.signal(page, s1.id), 'Valid').check();
  expect((await saved).request().postDataJSON()).toEqual({ label: 'valid' });
  saved = waitForWrite(page, 'POST', new RegExp(`/api/signals/${s2.id}/label$`));
  await detail.signalLabelOption(detail.signal(page, s2.id), 'None').check();
  expect((await saved).request().postDataJSON()).toEqual({ label: null });

  await page.reload();
  await expect(detail.signalLabelOption(detail.signal(page, s1.id), 'Valid')).toBeChecked();
  await expect(detail.signalLabelOption(detail.signal(page, s2.id), 'None')).toBeChecked();
  const final = await clusterDetail(request, top.id);
  expect(final.signals.find((s) => s.id === s1.id)?.label).toBe('valid');
  expect(final.signals.find((s) => s.id === s2.id)?.label).toBeNull();
});

test('split moves the selected signal into a new card', async ({ page, request }) => {
  const before = await listClusters(request);
  const top = before[0];
  const d = await clusterDetail(request, top.id);
  const moved = d.signals[d.signals.length - 1];
  await detail.goto(page, top.id);

  await expect(detail.splitButton(page)).toBeDisabled();
  await expect(page.getByText('No signals selected.')).toBeVisible();
  await detail.signalSelect(detail.signal(page, moved.id)).check();
  await expect(page.getByText('1 signal selected to move into a new card.')).toBeVisible();

  const split = waitForWrite(page, 'POST', new RegExp(`/api/clusters/${top.id}/split$`));
  await detail.splitButton(page).click();
  const res = await split;
  expect(res.status()).toBe(200);
  expect(res.request().postDataJSON()).toEqual({ signal_ids: [moved.id] });
  const body = await res.json();
  const single = body.clusters.find((c: any) => c.n_signals === 1);
  const rest = body.clusters.find((c: any) => c.n_signals === d.n_signals - 1);
  expect(single && rest).toBeTruthy();

  // The UI opens the first of the two new cards and offers the other one.
  await expect(page).toHaveURL(new RegExp(`/cards/${body.clusters[0].id}$`));
  await expect(page.locator(`[data-testid="card-detail"][data-cluster-id="${body.clusters[0].id}"]`)).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open the other card' })).toHaveAttribute('href', `/cards/${body.clusters[1].id}`);

  await detail.goto(page, single.id);
  await expect(detail.signals(page)).toHaveCount(1);
  await expect(detail.signal(page, moved.id)).toBeVisible();
  await expect(detail.modelWritten(page)).toContainText('No summary yet');
  await detail.goto(page, rest.id);
  await expect(detail.signals(page)).toHaveCount(d.n_signals - 1);
  await expect(detail.signal(page, moved.id)).toHaveCount(0);

  await cards.goto(page);
  await expect(cards.rows(page)).toHaveCount(before.length + 1);
  await expect(cards.row(page, top.id)).toHaveCount(0);
  await expect(cards.row(page, single.id)).toContainText('No synthesis yet');
  const overrides = await getJson<any[]>(request, '/api/overrides');
  expect(overrides.map((o) => [o.kind, o.payload.signal_ids])).toEqual([['split', [moved.id]]]);
});

test('split needs at least one signal left behind; single-signal cards cannot split', async ({ page, request }) => {
  const all = await listClusters(request);
  const top = all[0];
  const d = await clusterDetail(request, top.id);
  await detail.goto(page, top.id);
  for (const s of d.signals) await detail.signalSelect(detail.signal(page, s.id)).check();
  await expect(page.getByText('Leave at least one signal on this card.')).toBeVisible();
  await expect(detail.splitButton(page)).toBeDisabled();

  const single = all.find((c) => c.n_signals === 1)!;
  await detail.goto(page, single.id);
  await expect(page.getByText('A card needs two or more signals to split.')).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Select signal for split' })).toHaveCount(0);
  await expect(detail.splitButton(page)).toBeDisabled();
  expect(await getJson<any[]>(request, '/api/overrides')).toEqual([]);
});
