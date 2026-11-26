import { clusterDetail, expect, getJson, listClusters, test, type ClusterSummary } from '../../lib/fixtures';
import { LocalServer } from '../../lib/server';
import {
  FORBIDDEN_KEY, allKeys, expectClusterDetail, expectClusterSummary, expectKeys, isIsoDate,
} from '../../lib/shape';

/**
 * API contract v1 (TEAM_PLAN.md, docs/api.md), checked with Playwright's request fixture against a
 * server seeded by `paindisc demo` (5 clusters from 7 verified signals; one 3-signal Medium cluster).
 */

const post = (request: any, url: string, data: unknown, headers: Record<string, string>) =>
  request.post(url, { data, headers });

test.describe('read endpoints', () => {
  test.use({ reseed: 'file' }); // read-only: one store for the whole describe block

  test('GET /api/health', async ({ request, target }) => {
    const body = await getJson<any>(request, '/api/health');
    expectKeys(body, ['status', 'store', 'llm_mock', 'version'], 'health');
    expect(body.status).toBe('ok');
    expect(body.store).toBe(target === 'external' ? (process.env.E2E_EXPECT_STORE ?? 'postgres') : 'sqlite');
    expect(body.llm_mock, 'the e2e servers must run the mock LLM').toBe(true);
    expect(typeof body.version).toBe('string');
  });

  test('GET /api/clusters: ranked summaries with total', async ({ request }) => {
    const body = await getJson<{ items: ClusterSummary[]; total: number }>(request, '/api/clusters');
    expectKeys(body, ['items', 'total'], 'clusters');
    expect(body.total).toBe(5);
    expect(body.items).toHaveLength(5);
    body.items.forEach((c, i) => expectClusterSummary(c, `items[${i}]`));
    const scores = body.items.map((c) => c.score);
    expect(scores, 'ranked by score, highest first').toEqual([...scores].sort((a, b) => b - a));
    expect(new Set(body.items.map((c) => c.id)).size).toBe(5);
    const top = body.items[0];
    expect(top.n_signals).toBe(3);
    expect(top.confidence).toBe('Medium');
    expect(top.has_synthesis).toBe(true);
    expect(body.items.every((c) => c.label === null)).toBe(true);
  });

  test('GET /api/clusters: limit and offset page without overlap', async ({ request }) => {
    const all = await listClusters(request);
    const p1 = await getJson<any>(request, '/api/clusters?limit=2&offset=0');
    const p2 = await getJson<any>(request, '/api/clusters?limit=2&offset=2');
    const p3 = await getJson<any>(request, '/api/clusters?limit=2&offset=4');
    expect(p1.total).toBe(5);
    expect(p2.total).toBe(5);
    expect([...p1.items, ...p2.items, ...p3.items].map((c: ClusterSummary) => c.id)).toEqual(all.map((c) => c.id));
    expect(p3.items).toHaveLength(1);
  });

  test('GET /api/clusters: confidence filter', async ({ request }) => {
    const medium = await getJson<any>(request, '/api/clusters?confidence=Medium');
    expect(medium.items.length).toBe(1);
    expect(medium.total).toBe(1);
    const low = await getJson<any>(request, '/api/clusters?confidence=Low');
    expect(low.items.length).toBe(4);
    expect(low.items.every((c: ClusterSummary) => c.confidence === 'Low')).toBe(true);
    const high = await getJson<any>(request, '/api/clusters?confidence=High');
    expect(high.items).toEqual([]);
    expect(high.total).toBe(0);
  });

  test('GET /api/clusters: bad query values are 422 with {detail}', async ({ request }) => {
    for (const q of ['confidence=Huge', 'label=maybe', 'limit=-1', 'offset=abc']) {
      const res = await request.get(`/api/clusters?${q}`);
      expect(res.status(), q).toBe(422);
      expect(await res.json(), q).toHaveProperty('detail');
    }
  });

  test('GET /api/clusters/{id}: detail with signals and the evidence contract', async ({ request }) => {
    for (const c of await listClusters(request)) {
      const d = await clusterDetail(request, c.id);
      expectClusterDetail(d, `cluster ${c.id}`);
      expect(d.id).toBe(c.id);
      expect(d.score).toBe(c.score);
      for (const s of d.signals) {
        expect(s.document.url, 'every signal links to its source').toMatch(/^https?:\/\//);
        expect(s.document.published_at, 'every demo signal is dated').not.toBeNull();
      }
    }
    const top = (await listClusters(request))[0];
    const d = await clusterDetail(request, top.id);
    expect(d.synthesis?.hypotheses.length).toBeGreaterThan(0);
    const statuses = new Set(d.signals.flatMap((s) => Object.values(s.fields).map((e) => e.status)));
    expect([...statuses]).toEqual(expect.arrayContaining(['observed', 'inferred', 'unknown']));
  });

  test('GET /api/clusters/{id}: card is report.card()', async ({ request }) => {
    const top = (await listClusters(request))[0];
    const d = await clusterDetail(request, top.id);
    expectKeys(d.card, ['evidence', 'workarounds', 'spend', 'financial', 'frequency', 'pricing'], 'card');
  });

  test('GET /api/clusters/{id}: unknown id is 404 with {detail}', async ({ request }) => {
    const res = await request.get('/api/clusters/000000000000');
    expect(res.status()).toBe(404);
    expect(await res.json()).toHaveProperty('detail');
  });

  test('GET /api/sources: registry summary, blocked sources carry a reason', async ({ request }) => {
    const rows = await getJson<any[]>(request, '/api/sources');
    expect(rows.length).toBeGreaterThan(5);
    rows.forEach((r, i) => expectKeys(r, ['source', 'allowed', 'route', 'domains', 'reason'], `sources[${i}]`));
    const by = Object.fromEntries(rows.map((r) => [r.source, r]));
    expect(by.reddit?.allowed).toBe(false);
    expect(by.reddit?.reason).toMatch(/approval/i);
    expect(by.hn?.allowed).toBe(true);
    for (const r of rows.filter((x) => !x.allowed)) {
      expect(String(r.reason).length, `blocked source ${r.source} needs a reason`).toBeGreaterThan(0);
    }
  });

  test('GET /api/stats: source stats and top arms', async ({ request }) => {
    const body = await getJson<any>(request, '/api/stats');
    expectKeys(body, ['sources', 'arms'], 'stats');
    expect(body.sources.length).toBeGreaterThan(0);
    body.sources.forEach((r: any, i: number) =>
      expectKeys(r, ['source', 'docs', 'duplicates', 'triaged', 'pain', 'signals', 'dropped'], `stats.sources[${i}]`));
    const forum = body.sources.find((r: any) => r.source === 'demo-forum');
    expect(forum?.duplicates, 'the cross-post is marked as a duplicate').toBe(1);
    expect(Array.isArray(body.arms)).toBe(true);
    expect(body.arms.length).toBeLessThanOrEqual(20);
  });

  test('GET /api/trends: shape', async ({ request }) => {
    const body = await getJson<any>(request, '/api/trends');
    expectKeys(body, ['run_at', 'previous_run_at', 'items'], 'trends');
    if (body.run_at !== null) expect(isIsoDate(body.run_at), `run_at ${body.run_at}`).toBe(true);
    if (body.previous_run_at !== null) expect(isIsoDate(body.previous_run_at)).toBe(true);
    for (const [i, t] of body.items.entries()) {
      expectKeys(t, ['cluster_id', 'title', 'status', 'score', 'previous_score', 'delta'], `trends.items[${i}]`);
      expect(['new', 'rising', 'falling', 'gone']).toContain(t.status);
    }
  });

  test('GET /api/reports and /api/reports/{name}', async ({ request }) => {
    const rows = await getJson<any[]>(request, '/api/reports');
    rows.forEach((r, i) => expectKeys(r, ['name', 'kind', 'size', 'modified'], `reports[${i}]`));
    const names = rows.map((r) => r.name);
    expect(names).toEqual(expect.arrayContaining(['demo-pain-report.md', 'demo-pain-report.html']));
    for (const r of rows) {
      expect(['md', 'html']).toContain(r.kind);
      expect(isIsoDate(r.modified), `modified ${r.modified}`).toBe(true);
    }
    const md = await request.get('/api/reports/demo-pain-report.md');
    expect(md.status()).toBe(200);
    expect(await md.text()).toContain('Pain Report');
    const html = await request.get('/api/reports/demo-pain-report.html');
    expect(html.status()).toBe(200);
    expect(html.headers()['content-type']).toMatch(/text\/html/);
    expect(await request.get('/api/reports/nope.md').then((r) => r.status())).toBe(404);
  });

  test('GET /api/reports/{name}: path traversal is refused', async ({ request }) => {
    const attempts = [
      '..%2Fpyproject.toml', '..%2F..%2Fpyproject.toml', '%2E%2E%2F%2E%2E%2Fpyproject.toml',
      '..%2Fconfig%2Fregistry.yaml', '%2Fetc%2Fpasswd', '..%5C..%5Cpyproject.toml', '....%2F%2F....%2F%2Fpyproject.toml',
      '.env', '..%2F..%2F.env',
    ];
    for (const a of attempts) {
      const res = await request.get(`/api/reports/${a}`);
      const text = await res.text();
      expect([400, 404, 422], `${a} -> ${res.status()}`).toContain(res.status());
      expect(text, a).not.toMatch(/\[project\]|root:|allowed:|ANTHROPIC/);
    }
  });

  test('unknown /api path is a JSON 404, not the SPA', async ({ request }) => {
    const res = await request.get('/api/does-not-exist');
    expect(res.status()).toBe(404);
    expect(res.headers()['content-type']).toMatch(/json/);
  });

  test('OpenAPI is published and has no solution, product or author fields', async ({ request }) => {
    const spec = await getJson<any>(request, '/api/openapi.json');
    expect(spec.openapi).toMatch(/^3\./);
    const schemas = spec.components?.schemas ?? {};
    const names = new Set<string>(Object.keys(schemas));
    for (const s of Object.values<any>(schemas)) {
      for (const k of Object.keys(s.properties ?? {})) names.add(k);
    }
    const bad = [...names].filter((n) => FORBIDDEN_KEY.test(n));
    expect(bad, 'schema names/properties that suggest solutions or expose people').toEqual([]);
    const paths = Object.keys(spec.paths ?? {});
    for (const p of ['/api/clusters', '/api/clusters/{id}', '/api/clusters/merge', '/api/sources', '/api/trends']) {
      expect(paths.some((x) => x.replace(/\{[^}]+\}/g, '{id}') === p), `path ${p}`).toBe(true);
    }
  });

  test('no response body carries author or solution fields', async ({ request }) => {
    const bodies: unknown[] = [];
    for (const url of ['/api/clusters', '/api/sources', '/api/stats', '/api/trends', '/api/overrides', '/api/reports']) {
      bodies.push(await getJson(request, url));
    }
    for (const c of await listClusters(request)) bodies.push(await clusterDetail(request, c.id));
    const keys = [...allKeys(bodies)];
    expect(keys.filter((k) => FORBIDDEN_KEY.test(k))).toEqual([]);
  });

  test('SPA fallback serves index.html for client routes', async ({ request }) => {
    const res = await request.get('/cards/some-client-route');
    expect(res.status()).toBe(200);
    expect(res.headers()['content-type']).toMatch(/text\/html/);
    expect(await res.text()).toMatch(/<div id="root"|<script type="module"/);
  });
});

test.describe('write endpoints', () => {
  test('cluster label: set, filter, clear', async ({ request, authHeaders }) => {
    const [a, b] = await listClusters(request);
    let res = await post(request, `/api/clusters/${a.id}/label`, { label: 'accept', note: 'e2e' }, authHeaders);
    expect(res.status(), await res.text()).toBe(200);
    const summary = await res.json();
    expectClusterSummary(summary);
    expect(summary.id).toBe(a.id);
    expect(summary.label).toBe('accept');
    res = await post(request, `/api/clusters/${b.id}/label`, { label: 'reject' }, authHeaders);
    expect(res.status()).toBe(200);

    expect((await clusterDetail(request, a.id)).label).toBe('accept');
    expect((await listClusters(request, '?label=accept')).map((c) => c.id)).toEqual([a.id]);
    expect((await listClusters(request, '?label=reject')).map((c) => c.id)).toEqual([b.id]);
    const unlabelled = await listClusters(request, '?label=unlabelled');
    expect(unlabelled).toHaveLength(3);
    expect(unlabelled.map((c) => c.id)).not.toContain(a.id);

    res = await post(request, `/api/clusters/${a.id}/label`, { label: null }, authHeaders);
    expect(res.status()).toBe(200);
    expect((await res.json()).label).toBeNull();
    expect(await listClusters(request, '?label=accept')).toEqual([]);
  });

  test('cluster label: validation', async ({ request, authHeaders }) => {
    const [a] = await listClusters(request);
    let res = await post(request, `/api/clusters/${a.id}/label`, { label: 'maybe' }, authHeaders);
    expect(res.status()).toBe(422);
    expect(await res.json()).toHaveProperty('detail');
    res = await post(request, '/api/clusters/000000000000/label', { label: 'accept' }, authHeaders);
    expect(res.status()).toBe(404);
  });

  test('signal label: set and clear', async ({ request, authHeaders }) => {
    const top = (await listClusters(request))[0];
    const sig = (await clusterDetail(request, top.id)).signals[0];
    for (const label of ['valid', 'invalid', 'duplicate'] as const) {
      const res = await post(request, `/api/signals/${sig.id}/label`, { label, note: 'e2e' }, authHeaders);
      expect(res.status(), await res.text()).toBe(200);
      expect(await res.json()).toEqual({ id: sig.id, label });
      const again = (await clusterDetail(request, top.id)).signals.find((s) => s.id === sig.id);
      expect(again?.label).toBe(label);
    }
    const res = await post(request, `/api/signals/${sig.id}/label`, { label: null }, authHeaders);
    expect(await res.json()).toEqual({ id: sig.id, label: null });
    expect(await post(request, `/api/signals/${sig.id}/label`, { label: 'great' }, authHeaders).then((r: any) => r.status())).toBe(422);
    expect(await post(request, '/api/signals/nosuchsignal/label', { label: 'valid' }, authHeaders).then((r: any) => r.status())).toBe(404);
  });

  test('merge: new card, override stored, sources gone', async ({ request, authHeaders }) => {
    const before = await listClusters(request);
    const [x, y] = before.filter((c) => c.n_signals === 1).slice(0, 2);
    const xs = (await clusterDetail(request, x.id)).signals.map((s) => s.id);
    const ys = (await clusterDetail(request, y.id)).signals.map((s) => s.id);
    const res = await post(request, '/api/clusters/merge', { ids: [x.id, y.id] }, authHeaders);
    expect(res.status(), await res.text()).toBe(200);
    const merged = await res.json();
    expectClusterDetail(merged, 'merged');
    expect([x.id, y.id]).not.toContain(merged.id);
    expect(merged.synthesis).toBeNull();
    expect(merged.has_synthesis).toBe(false);
    expect(merged.n_signals).toBe(2);
    expect(merged.signals.map((s: any) => s.id).sort()).toEqual([...xs, ...ys].sort());

    const after = await listClusters(request);
    expect(after).toHaveLength(before.length - 1);
    expect(after.map((c) => c.id)).not.toContain(x.id);
    expect(after.map((c) => c.id)).not.toContain(y.id);
    expect(after.map((c) => c.id)).toContain(merged.id);
    expect((await request.get(`/api/clusters/${x.id}`)).status()).toBe(404);

    const overrides = await getJson<any[]>(request, '/api/overrides');
    expect(overrides).toHaveLength(1);
    expectKeys(overrides[0], ['id', 'kind', 'payload', 'created_at'], 'override');
    expect(overrides[0].kind).toBe('merge');
    expect([...overrides[0].payload.signal_ids].sort()).toEqual([...xs, ...ys].sort());
    expect(isIsoDate(overrides[0].created_at)).toBe(true);
  });

  test('merge: validation', async ({ request, authHeaders }) => {
    const [a] = await listClusters(request);
    for (const body of [{ ids: [a.id] }, { ids: [] }, {}]) {
      const res = await post(request, '/api/clusters/merge', body, authHeaders);
      expect([400, 422], JSON.stringify(body)).toContain(res.status());
    }
    const res = await post(request, '/api/clusters/merge', { ids: [a.id, '000000000000'] }, authHeaders);
    expect(res.status()).toBe(404);
    expect(await getJson<any[]>(request, '/api/overrides')).toEqual([]);
  });

  test('split: moved signals become their own card', async ({ request, authHeaders }) => {
    const top = (await listClusters(request))[0];
    const d = await clusterDetail(request, top.id);
    const moved = [d.signals[0].id];
    const res = await post(request, `/api/clusters/${top.id}/split`, { signal_ids: moved }, authHeaders);
    expect(res.status(), await res.text()).toBe(200);
    const body = await res.json();
    expectKeys(body, ['clusters'], 'split');
    expect(body.clusters).toHaveLength(2);
    body.clusters.forEach((c: any, i: number) => expectClusterSummary(c, `split.clusters[${i}]`));
    expect(body.clusters.every((c: any) => c.has_synthesis === false)).toBe(true);
    expect(body.clusters.map((c: any) => c.n_signals).sort()).toEqual([1, 2]);

    const single = body.clusters.find((c: any) => c.n_signals === 1);
    expect((await clusterDetail(request, single.id)).signals.map((s) => s.id)).toEqual(moved);
    const rest = body.clusters.find((c: any) => c.n_signals === 2);
    expect((await clusterDetail(request, rest.id)).signals.map((s) => s.id)).not.toContain(moved[0]);

    const after = await listClusters(request);
    expect(after).toHaveLength(6);
    expect(after.map((c) => c.id)).not.toContain(top.id);
    const overrides = await getJson<any[]>(request, '/api/overrides');
    expect(overrides.map((o) => o.kind)).toEqual(['split']);
    expect(overrides[0].payload.signal_ids).toEqual(moved);
  });

  test('split: validation', async ({ request, authHeaders }) => {
    const top = (await listClusters(request))[0];
    const d = await clusterDetail(request, top.id);
    const all = d.signals.map((s) => s.id);
    const other = (await listClusters(request))[1];
    const foreign = (await clusterDetail(request, other.id)).signals[0].id;
    for (const signal_ids of [[], all, [foreign]]) {
      const res = await post(request, `/api/clusters/${top.id}/split`, { signal_ids }, authHeaders);
      expect([400, 409, 422], JSON.stringify(signal_ids)).toContain(res.status());
    }
    expect((await post(request, '/api/clusters/000000000000/split', { signal_ids: [all[0]] }, authHeaders)).status()).toBe(404);
    expect(await getJson<any[]>(request, '/api/overrides')).toEqual([]);
  });

  test('synthesize: a merged card gets a (mock) synthesis', async ({ request, authHeaders }) => {
    const singles = (await listClusters(request)).filter((c) => c.n_signals === 1);
    const merged = await (await post(request, '/api/clusters/merge', { ids: [singles[0].id, singles[1].id] }, authHeaders)).json();
    expect(merged.synthesis).toBeNull();
    const res = await post(request, `/api/clusters/${merged.id}/synthesize`, {}, authHeaders);
    expect(res.status(), await res.text()).toBe(200);
    const d = await res.json();
    expectClusterDetail(d, 'synthesized');
    expect(d.id).toBe(merged.id);
    expect(d.synthesis).not.toBeNull();
    expect(d.synthesis.title.length).toBeGreaterThan(0);
    expect(d.has_synthesis).toBe(true);
    expect((await clusterDetail(request, merged.id)).synthesis?.title).toBe(d.synthesis.title);
    expect((await post(request, '/api/clusters/000000000000/synthesize', {}, authHeaders)).status()).toBe(404);
  });

  test('overrides: delete one', async ({ request, authHeaders }) => {
    const singles = (await listClusters(request)).filter((c) => c.n_signals === 1);
    await post(request, '/api/clusters/merge', { ids: [singles[0].id, singles[1].id] }, authHeaders);
    const [o] = await getJson<any[]>(request, '/api/overrides');
    const res = await request.delete(`/api/overrides/${o.id}`, { headers: authHeaders });
    expect([200, 204]).toContain(res.status());
    expect(await getJson<any[]>(request, '/api/overrides')).toEqual([]);
    expect((await request.delete(`/api/overrides/${o.id}`, { headers: authHeaders })).status()).toBe(404);
  });

  test('labels and overrides survive a server restart (they live in the store)', async ({ request, authHeaders, app }) => {
    test.skip(!(app.server instanceof LocalServer), 'external servers are not restarted by the suite');
    const [a, b, c] = await listClusters(request);
    await post(request, `/api/clusters/${a.id}/label`, { label: 'reject' }, authHeaders);
    const merged = await (await post(request, '/api/clusters/merge', { ids: [b.id, c.id] }, authHeaders)).json();
    await (app.server as LocalServer).bounce();
    expect((await clusterDetail(request, a.id)).label).toBe('reject');
    expect((await listClusters(request)).map((x) => x.id)).toContain(merged.id);
    expect(await getJson<any[]>(request, '/api/overrides')).toHaveLength(1);
  });
});
