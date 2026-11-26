import { expect, getJson, listClusters, test, waitForWrite } from '../../lib/fixtures';
import { cards, tokenDialog } from '../../lib/ui';

// Journey 12: with PAINDISC_API_TOKEN set (project `sqlite-auth`), reads stay open, writes need the
// Bearer token, and the UI asks for it once per tab.

test.beforeEach(({ apiToken }) => {
  expect(apiToken, 'this spec runs only in the sqlite-auth project').toBeTruthy();
});

test('reads work without a token', async ({ request }) => {
  for (const url of ['/api/health', '/api/clusters', '/api/sources', '/api/stats', '/api/trends', '/api/overrides', '/api/reports']) {
    expect((await request.get(url)).status(), url).toBe(200);
  }
});

test('every write endpoint rejects a missing or wrong token with 401', async ({ request }) => {
  const [a, b] = await listClusters(request);
  const sig = (await getJson<any>(request, `/api/clusters/${a.id}`)).signals[0].id;
  const writes: [string, string, unknown][] = [
    ['POST', `/api/clusters/${a.id}/label`, { label: 'accept' }],
    ['POST', `/api/signals/${sig}/label`, { label: 'valid' }],
    ['POST', '/api/clusters/merge', { ids: [a.id, b.id] }],
    ['POST', `/api/clusters/${a.id}/split`, { signal_ids: [sig] }],
    ['POST', `/api/clusters/${a.id}/synthesize`, {}],
    ['DELETE', '/api/overrides/1', undefined],
  ];
  for (const [method, url, data] of writes) {
    for (const headers of [{}, { Authorization: 'Bearer wrong-token' }, { Authorization: 'wrong-scheme' }] as Record<string, string>[]) {
      const res = await request.fetch(url, { method, data, headers });
      expect(res.status(), `${method} ${url} with ${JSON.stringify(headers)}`).toBe(401);
      expect(await res.json()).toHaveProperty('detail');
    }
  }
  // Nothing changed.
  expect((await listClusters(request)).every((c) => c.label === null)).toBe(true);
  expect(await getJson<any[]>(request, '/api/overrides')).toEqual([]);
});

test('writes work with the token', async ({ request, authHeaders }) => {
  const [a] = await listClusters(request);
  const res = await request.post(`/api/clusters/${a.id}/label`, { data: { label: 'accept' }, headers: authHeaders });
  expect(res.status()).toBe(200);
  expect((await res.json()).label).toBe('accept');
});

test('the UI prompts for the token on the first write and then keeps it for the tab', async ({ page, request, apiToken }) => {
  const [x, y, z] = (await listClusters(request)).filter((c) => c.n_signals === 1);
  await cards.goto(page);
  await expect(tokenDialog.dialog(page)).toHaveCount(0); // reads don't prompt

  // First write: 401, the dialog asks for the token. Cancel -> nothing is written, an error is shown.
  await cards.select(cards.row(page, x.id)).check();
  await cards.select(cards.row(page, y.id)).check();
  await cards.mergeButton(page).click();
  await expect(tokenDialog.dialog(page)).toBeVisible();
  await expect(tokenDialog.dialog(page)).toContainText('PAINDISC_API_TOKEN');
  await expect(tokenDialog.save(page)).toBeDisabled();
  await tokenDialog.cancel(page).click();
  await expect(tokenDialog.dialog(page)).toHaveCount(0);
  await expect(page.getByRole('alert')).toContainText(/token/i);
  expect(await getJson<any[]>(request, '/api/overrides')).toEqual([]);

  // A wrong token is rejected; the next attempt says so and asks again.
  await cards.mergeButton(page).click();
  await tokenDialog.input(page).fill('wrong-token');
  await tokenDialog.save(page).click();
  await expect(page.getByRole('alert')).toContainText(/token/i);
  await cards.mergeButton(page).click();
  await expect(tokenDialog.dialog(page)).toContainText(/rejected/i);

  // The right token: the retried request succeeds and opens the merged card.
  const merged = waitForWrite(page, 'POST', /\/api\/clusters\/merge$/);
  await tokenDialog.input(page).fill(apiToken!);
  await tokenDialog.save(page).click();
  expect((await merged).status()).toBe(200);
  await expect(page).toHaveURL(/\/cards\/[0-9a-f]+$/);
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  expect(await getJson<any[]>(request, '/api/overrides')).toHaveLength(1);

  // Same tab, after a reload: no prompt for the next write.
  await cards.goto(page);
  await page.reload();
  const others = (await listClusters(request)).filter((c) => c.n_signals === 1);
  expect(others.map((c) => c.id)).toContain(z.id);
  await cards.select(cards.row(page, others[0].id)).check();
  await cards.select(cards.row(page, others[1].id)).check();
  const second = waitForWrite(page, 'POST', /\/api\/clusters\/merge$/);
  await cards.mergeButton(page).click();
  expect((await second).status()).toBe(200);
  await expect(tokenDialog.dialog(page)).toHaveCount(0);
  expect(await getJson<any[]>(request, '/api/overrides')).toHaveLength(2);
});

test('the token is not kept in localStorage or cookies', async ({ page, context, request, apiToken }) => {
  const [x, y] = (await listClusters(request)).filter((c) => c.n_signals === 1);
  await cards.goto(page);
  await cards.select(cards.row(page, x.id)).check();
  await cards.select(cards.row(page, y.id)).check();
  const merged = waitForWrite(page, 'POST', /\/api\/clusters\/merge$/, 200); // after the 401 and the prompt
  await cards.mergeButton(page).click();
  await tokenDialog.input(page).fill(apiToken!);
  await tokenDialog.save(page).click();
  expect((await merged).status()).toBe(200);

  const local = await page.evaluate(() => JSON.stringify(Object.entries(localStorage)));
  expect(local).not.toContain(apiToken!);
  expect(JSON.stringify(await context.cookies())).not.toContain(apiToken!);
  // A new tab has its own sessionStorage, so it would ask again.
  const other = await context.newPage();
  await other.goto('/cards');
  expect(await other.evaluate(() => JSON.stringify(Object.entries(sessionStorage)))).not.toContain(apiToken!);
  await other.close();
});
