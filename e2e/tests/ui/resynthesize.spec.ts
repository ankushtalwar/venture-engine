import { clusterDetail, expect, listClusters, test, waitForWrite } from '../../lib/fixtures';
import { cards, detail } from '../../lib/ui';

// Journey 7: re-synthesize works (mock LLM).

test('a merged card has no synthesis until it is re-synthesized', async ({ page, request, authHeaders }) => {
  const singles = (await listClusters(request)).filter((c) => c.n_signals === 1);
  const res = await request.post('/api/clusters/merge', { data: { ids: [singles[0].id, singles[1].id] }, headers: authHeaders });
  expect(res.status()).toBe(200);
  const merged = await res.json();
  expect(merged.synthesis).toBeNull();

  await detail.goto(page, merged.id);
  await expect(detail.modelWritten(page)).toContainText('No summary yet');
  await expect(detail.hypotheses(page)).toHaveCount(0);

  const call = waitForWrite(page, 'POST', new RegExp(`/api/clusters/${merged.id}/synthesize$`));
  await detail.resynthesize(page).click();
  const done = await call;
  expect(done.status()).toBe(200);
  const body = await done.json();
  expect(body.synthesis).not.toBeNull();

  await expect(detail.heading(page)).toHaveText(body.title);
  await expect(detail.modelWritten(page)).toContainText(body.synthesis.summary);
  await expect(detail.hypotheses(page)).toHaveText(body.synthesis.hypotheses);
  await expect(detail.modelWritten(page)).not.toContainText('No summary yet');

  await page.reload();
  await expect(detail.heading(page)).toHaveText(body.title);
  expect((await clusterDetail(request, merged.id)).has_synthesis).toBe(true);
  await cards.goto(page);
  await expect(cards.row(page, merged.id)).not.toContainText('No synthesis yet');
});

test('re-synthesizing an existing card keeps it consistent', async ({ page, request }) => {
  const [top] = await listClusters(request);
  await detail.goto(page, top.id);
  const call = waitForWrite(page, 'POST', new RegExp(`/api/clusters/${top.id}/synthesize$`));
  await detail.resynthesize(page).click();
  const res = await call;
  expect(res.status()).toBe(200);
  const body = await res.json();
  await expect(detail.resynthesize(page)).toBeEnabled();
  await expect(detail.heading(page)).toHaveText(body.title);
  await expect(detail.signals(page)).toHaveCount(top.n_signals);
  // Labels are untouched by synthesis.
  expect(body.label).toBe(top.label);
  expect(body.id).toBe(top.id);
});
