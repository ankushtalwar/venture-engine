import { clusterDetail, expect, getJson, listClusters, test, type ClusterSummary } from '../../lib/fixtures';
import { cards, detail } from '../../lib/ui';

/**
 * Manual merges and splits are stored as overrides and re-applied by every automatic clustering run
 * (Pipeline.cluster -> cluster.apply_overrides). `paindisc cluster` runs at the scoring.yaml threshold
 * (0.7, the demo used 0.75), so other memberships may change; we assert only what the overrides promise.
 */

async function clusterOf(request: any, signalId: string): Promise<ClusterSummary & { signalIds: string[] }> {
  for (const c of await listClusters(request)) {
    const ids = (await clusterDetail(request, c.id)).signals.map((s) => s.id);
    if (ids.includes(signalId)) return { ...c, signalIds: ids };
  }
  throw new Error(`signal ${signalId} is in no cluster`);
}

test('a merge survives the next automatic clustering run, with its label and synthesis', async ({ request, authHeaders, app }) => {
  const [x, y] = (await listClusters(request)).filter((c) => c.n_signals === 1);
  const merged = await (await request.post('/api/clusters/merge', { data: { ids: [x.id, y.id] }, headers: authHeaders })).json();
  const signalIds: string[] = merged.signals.map((s: any) => s.id).sort();
  await request.post(`/api/clusters/${merged.id}/label`, { data: { label: 'accept' }, headers: authHeaders });
  const synth = await (await request.post(`/api/clusters/${merged.id}/synthesize`, { headers: authHeaders })).json();
  expect(synth.synthesis).not.toBeNull();

  const log = await app.server.recluster();
  expect(log).toMatch(/re-applied 1 manual override/);

  const after = await clusterOf(request, signalIds[0]);
  expect(after.signalIds.sort(), 'the merged signals are still one cluster').toEqual(signalIds);
  expect(after.label, 'the accept label followed the cluster').toBe('accept');
  expect((await clusterDetail(request, after.id)).synthesis?.title).toBe(synth.synthesis.title);
  expect((await getJson<any[]>(request, '/api/overrides')).map((o) => o.kind)).toEqual(['merge']);
});

test('a split survives the next automatic clustering run', async ({ request, authHeaders, app }) => {
  const top = (await listClusters(request))[0];
  const d = await clusterDetail(request, top.id);
  const moved = d.signals[0].id;
  const res = await request.post(`/api/clusters/${top.id}/split`, { data: { signal_ids: [moved] }, headers: authHeaders });
  expect(res.status()).toBe(200);

  await app.server.recluster();

  const own = await clusterOf(request, moved);
  expect(own.signalIds, 'the split-off signal forms exactly its own cluster').toEqual([moved]);
  expect((await getJson<any[]>(request, '/api/overrides')).map((o) => o.kind)).toEqual(['split']);
});

test('deleting an override lets automatic clustering decide again', async ({ request, authHeaders, app }) => {
  const [x, y] = (await listClusters(request)).filter((c) => c.n_signals === 1);
  const merged = await (await request.post('/api/clusters/merge', { data: { ids: [x.id, y.id] }, headers: authHeaders })).json();
  const [o] = await getJson<any[]>(request, '/api/overrides');
  await request.delete(`/api/overrides/${o.id}`, { headers: authHeaders });
  // Deleting takes effect on the next run only.
  expect((await listClusters(request)).map((c) => c.id)).toContain(merged.id);

  const log = await app.server.recluster();
  expect(log).not.toMatch(/re-applied/);
  const signalIds: string[] = merged.signals.map((s: any) => s.id);
  const a = await clusterOf(request, signalIds[0]);
  const b = await clusterOf(request, signalIds[1]);
  expect(a.id, 'these two unrelated signals are clustered apart again').not.toBe(b.id);
});

test('the UI shows the merged card after a re-cluster', async ({ page, request, authHeaders, app }) => {
  const [x, y] = (await listClusters(request)).filter((c) => c.n_signals === 1);
  const merged = await (await request.post('/api/clusters/merge', { data: { ids: [x.id, y.id] }, headers: authHeaders })).json();
  await app.server.recluster();
  const after = await clusterOf(request, merged.signals[0].id);
  await cards.goto(page);
  await expect(cards.row(page, after.id)).toContainText('2 signals');
  await detail.goto(page, after.id);
  await expect(detail.signals(page)).toHaveCount(2);
});
