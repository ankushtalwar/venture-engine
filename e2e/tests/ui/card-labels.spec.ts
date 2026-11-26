import { clusterDetail, expect, listClusters, test, waitForWrite } from '../../lib/fixtures';
import { cards, detail } from '../../lib/ui';

// Journey 3: accept/reject persists across reload and filters correctly.

test('accept and reject on the card page persist across reload', async ({ page, request }) => {
  const [a, b] = await listClusters(request);

  await detail.goto(page, a.id);
  const saved = waitForWrite(page, 'POST', new RegExp(`/api/clusters/${a.id}/label$`));
  await detail.accept(page).click();
  expect((await saved).status()).toBe(200);
  await expect(detail.accept(page)).toHaveAttribute('aria-pressed', 'true');
  await expect(detail.reject(page)).toHaveAttribute('aria-pressed', 'false');

  await detail.goto(page, b.id);
  const rejected = waitForWrite(page, 'POST', new RegExp(`/api/clusters/${b.id}/label$`));
  await detail.reject(page).click();
  expect((await rejected).status()).toBe(200);

  await page.reload();
  await expect(detail.reject(page)).toHaveAttribute('aria-pressed', 'true');
  await expect(detail.currentLabel(page)).toHaveText('Rejected');
  await detail.goto(page, a.id);
  await expect(detail.accept(page)).toHaveAttribute('aria-pressed', 'true');
  await expect(detail.currentLabel(page)).toHaveText('Accepted');

  expect((await clusterDetail(request, a.id)).label).toBe('accept');
  expect((await clusterDetail(request, b.id)).label).toBe('reject');

  await cards.goto(page);
  await expect(cards.label(cards.row(page, a.id))).toHaveText('Accepted');
  await expect(cards.label(cards.row(page, b.id))).toHaveText('Rejected');
});

test('label filter shows accepted, rejected and unlabelled cards', async ({ page, request, authHeaders }) => {
  const all = await listClusters(request);
  const [a, b] = all;
  await request.post(`/api/clusters/${a.id}/label`, { data: { label: 'accept' }, headers: authHeaders });
  await request.post(`/api/clusters/${b.id}/label`, { data: { label: 'reject' }, headers: authHeaders });

  await cards.goto(page);
  await cards.filter(page, 'Label', { label: 'Accepted' });
  await expect(page).toHaveURL(/label=accept/);
  await expect(cards.rows(page)).toHaveCount(1);
  await expect(cards.row(page, a.id)).toBeVisible();

  await cards.filter(page, 'Label', { label: 'Rejected' });
  await expect(cards.rows(page)).toHaveCount(1);
  await expect(cards.row(page, b.id)).toBeVisible();

  await cards.filter(page, 'Label', { label: 'Unlabelled' });
  await expect(cards.rows(page)).toHaveCount(all.length - 2);
  await expect(cards.row(page, a.id)).toHaveCount(0);
  await expect(cards.row(page, b.id)).toHaveCount(0);

  await page.reload();
  await expect(cards.labelFilter(page)).toHaveValue('unlabelled');
  await expect(cards.rows(page)).toHaveCount(all.length - 2);
});

test('changing a decision moves the card between filters', async ({ page, request }) => {
  const [a] = await listClusters(request);
  await detail.goto(page, a.id);
  await detail.accept(page).click();
  await expect(detail.accept(page)).toHaveAttribute('aria-pressed', 'true');
  await detail.reject(page).click();
  await expect(detail.reject(page)).toHaveAttribute('aria-pressed', 'true');
  await expect(detail.accept(page)).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(async () => (await clusterDetail(request, a.id)).label).toBe('reject');

  await cards.goto(page, '?label=accept');
  await expect(cards.rows(page)).toHaveCount(0);
  await cards.goto(page, '?label=reject');
  await expect(cards.row(page, a.id)).toBeVisible();
});

test('clear label removes the decision', async ({ page, request }) => {
  const [a] = await listClusters(request);
  await detail.goto(page, a.id);
  await expect(detail.clearLabel(page)).toBeDisabled();
  await detail.accept(page).click();
  await expect(detail.accept(page)).toHaveAttribute('aria-pressed', 'true');
  await expect(detail.currentLabel(page)).toHaveText('Accepted');
  const cleared = waitForWrite(page, 'POST', new RegExp(`/api/clusters/${a.id}/label$`));
  await detail.clearLabel(page).click();
  expect((await cleared).request().postDataJSON()).toMatchObject({ label: null });
  await expect(detail.accept(page)).toHaveAttribute('aria-pressed', 'false');
  await expect(detail.currentLabel(page)).toHaveText('Unlabelled');
  await expect(detail.clearLabel(page)).toBeDisabled();
  await page.reload();
  await expect(detail.currentLabel(page)).toHaveText('Unlabelled');
  expect((await clusterDetail(request, a.id)).label).toBeNull();
});

test('a decision note is sent with the label', async ({ page, request }) => {
  const [a] = await listClusters(request);
  await detail.goto(page, a.id);
  await page.getByLabel('Note (optional)').fill('Heard this in three interviews');
  const saved = waitForWrite(page, 'POST', new RegExp(`/api/clusters/${a.id}/label$`));
  await detail.reject(page).click();
  expect((await saved).request().postDataJSON()).toEqual({ label: 'reject', note: 'Heard this in three interviews' });
  await expect(page.getByLabel('Note (optional)')).toHaveValue('');
});
