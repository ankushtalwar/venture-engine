import type { Page } from '@playwright/test';
import { clusterDetail, expect, getJson, listClusters, test } from '../../lib/fixtures';
import { cards, detail, gotoPage } from '../../lib/ui';

// Journey 13: fetched content is untrusted. e2e/fixtures/xss_docs.jsonl puts HTML, script, an
// <iframe srcdoc>, a markdown javascript: link and a javascript: document URL into titles, text and
// metrics; all of it must render as inert text. Every payload sets window.__pwned (or parent.__pwned)
// if it ever runs.

test.use({ seed: 'xss', reseed: 'file' });

// Report files come from seeding; an external stack keeps its own reports volume, which we don't reseed.
const EXTERNAL_REPORTS = 'report files are checked on the local projects; the suite does not reseed an external reports volume';

/** Fails the test if anything executed, opened a dialog, or became live markup. */
async function expectInert(page: Page, dialogs: string[]) {
  expect(await page.evaluate(() => (window as any).__pwned), 'an injected payload executed').toBeUndefined();
  expect(dialogs, 'an injected payload opened a dialog').toEqual([]);
  const live = await page.evaluate(() => ({
    img: document.querySelectorAll('img[src="x"], img[onerror]').length,
    svg: document.querySelectorAll('svg[onload]').length,
    iframe: document.querySelectorAll('iframe[srcdoc]').length,
    script: [...document.querySelectorAll('script')].filter((s) => /__pwned/.test(s.textContent ?? '')).length,
    jsLinks: [...document.querySelectorAll('a[href]')].filter((a) => /^\s*javascript:/i.test(a.getAttribute('href') ?? '')).length,
    handlers: [...document.querySelectorAll('*')].filter((el) => [...el.attributes].some((at) => /^on/i.test(at.name))).length,
  }));
  expect(live, 'injected markup must not become DOM elements').toEqual({ img: 0, svg: 0, iframe: 0, script: 0, jsLinks: 0, handlers: 0 });
}

function watchDialogs(page: Page): string[] {
  const dialogs: string[] = [];
  page.on('dialog', (d) => { dialogs.push(`${d.type()}: ${d.message()}`); void d.dismiss(); });
  return dialogs;
}

test('the seed really contains the payloads (guards against a silently cleaned fixture)', async ({ request }) => {
  const all = await listClusters(request);
  expect(all.length).toBeGreaterThanOrEqual(3);
  const details = await Promise.all(all.map((c) => clusterDetail(request, c.id)));
  const text = JSON.stringify(details);
  for (const needle of ['<img src=x onerror=', '<svg onload=', '<iframe srcdoc=', '](javascript:', 'javascript:window.__pwned=2', '<script>window.__pwned=1</script>']) {
    expect(text, needle).toContain(needle);
  }
});

test('cards list renders payloads in titles as text', async ({ page, request }) => {
  const dialogs = watchDialogs(page);
  const all = await listClusters(request);
  await cards.goto(page);
  await expect(cards.rows(page)).toHaveCount(all.length);
  const html = all.find((c) => c.title.includes('<img'))!;
  await expect(cards.titleLink(cards.row(page, html.id))).toHaveText(html.title);
  await expectInert(page, dialogs);
});

test('card detail renders quotes, titles and values as text', async ({ page, request }) => {
  const dialogs = watchDialogs(page);
  for (const c of await listClusters(request)) {
    const d = await clusterDetail(request, c.id);
    await detail.goto(page, c.id);
    await expect(detail.signals(page)).toHaveCount(d.n_signals);
    for (const s of d.signals) {
      await expect(detail.signal(page, s.id)).toContainText(s.fields.problem.quote);
    }
    for (const href of await page.locator('a[href]').evaluateAll((els) => els.map((a) => a.getAttribute('href') ?? ''))) {
      expect(href, 'only http(s) or in-app links').toMatch(/^(https?:\/\/|\/|#)/);
    }
    await expectInert(page, dialogs);
  }
  // Spot checks that the payload text is visible, i.e. escaped rather than stripped or executed.
  const first = (await listClusters(request)).find((c) => c.title.includes('<img'))!;
  await detail.goto(page, first.id);
  await expect(page.getByText('<img src=x onerror="window.__pwned=1">', { exact: false }).first()).toBeVisible();
  await expect(page.getByText('<img src=x onerror=window.__pwned=8>accounting firms', { exact: false }).first()).toBeVisible();
});

test('a javascript: document URL is not a clickable link', async ({ page, request }) => {
  const dialogs = watchDialogs(page);
  const all = await listClusters(request);
  for (const c of all) {
    const d = await clusterDetail(request, c.id);
    const bad = d.signals.find((s) => s.document.url.startsWith('javascript:'));
    if (!bad) continue;
    await detail.goto(page, c.id);
    const sig = detail.signal(page, bad.id);
    await expect(sig).toBeVisible();
    await expect(sig.locator('a[href^="javascript:" i]')).toHaveCount(0);
    // The source name is still shown, as plain text.
    await expect(sig).toContainText(bad.document.source);
    await expectInert(page, dialogs);
    return;
  }
  throw new Error('no signal with a javascript: URL in the xss seed');
});

test('reports open in new tabs; nothing runs, even when a report link is clicked', async ({ page, context, target }) => {
  test.skip(target === 'external', EXTERNAL_REPORTS);
  const dialogs = watchDialogs(page);
  await gotoPage(page, '/reports', 'Reports');
  await expectInert(page, dialogs);

  const [md] = await Promise.all([context.waitForEvent('page'), page.getByRole('link', { name: 'demo-pain-report.md' }).click()]);
  const mdDialogs = watchDialogs(md);
  await md.waitForLoadState();
  // Markdown escaping may turn __ into \_\_; either way the payload is there as text.
  await expect(md.locator('body')).toContainText(/window\.(__|\\_\\_)pwned/);
  await expectInert(md, mdDialogs);
  await md.close();

  // The served HTML report is sandboxed by CSP, so even a javascript: href in it cannot run.
  const [html] = await Promise.all([context.waitForEvent('page'), page.getByRole('link', { name: 'demo-pain-report.html' }).click()]);
  const htmlDialogs = watchDialogs(html);
  await html.waitForLoadState();
  await expect(html.getByRole('heading', { level: 1, name: 'Pain Report' })).toBeVisible();
  await expect(html.locator('body')).toContainText('window.__pwned');
  for (const link of await html.locator('a[href^="javascript:" i]').all()) {
    await link.click();
  }
  expect(await html.evaluate(() => (window as any).__pwned)).toBeUndefined();
  expect(htmlDialogs).toEqual([]);
  const live = await html.evaluate(() => document.querySelectorAll('img[onerror], svg[onload], iframe[srcdoc], script').length);
  expect(live).toBe(0);
  await html.close();
});

test('served reports carry nosniff, and HTML reports a sandboxing CSP', async ({ request }) => {
  const html = await request.get('/api/reports/demo-pain-report.html');
  expect(html.status()).toBe(200);
  expect(html.headers()['x-content-type-options']).toBe('nosniff');
  const csp = html.headers()['content-security-policy'] ?? '';
  expect(csp).toContain("default-src 'none'");
  expect(csp).toMatch(/(^|;)\s*sandbox\b/);
  expect(csp).not.toMatch(/allow-scripts|allow-same-origin|script-src/);
  const md = await request.get('/api/reports/demo-pain-report.md');
  expect(md.headers()['x-content-type-options']).toBe('nosniff');
});

test('report files contain no javascript: links (they are also read outside the app)', async ({ request, target }) => {
  test.skip(target === 'external', EXTERNAL_REPORTS);
  // report.py output is opened from disk and delivered to Slack, where no CSP protects the reader.
  const html = await (await request.get('/api/reports/demo-pain-report.html')).text();
  expect(html.match(/href\s*=\s*["']?\s*javascript:[^"'>]*/gi) ?? [], 'HTML report links with a javascript: URL').toEqual([]);
  const md = await (await request.get('/api/reports/demo-pain-report.md')).text();
  // An escaped bracket (\]) cannot close a link, so only an unescaped `](javascript:` is live.
  expect(md.match(/(?<!\\)\]\(\s*javascript:[^)]*\)/gi) ?? [], 'Markdown report links with a javascript: target').toEqual([]);
  expect(md, 'the payload is still there, as escaped text').toContain('\\[click me\\]');
});

test('the markdown report is served as text, not HTML', async ({ request }) => {
  const res = await request.get('/api/reports/demo-pain-report.md');
  expect(res.status()).toBe(200);
  expect(res.headers()['content-type']).toMatch(/^text\/(markdown|plain)/);
});

test('API responses are JSON, so payloads cannot be sniffed as HTML', async ({ request }) => {
  for (const url of ['/api/clusters', '/api/sources', '/api/stats', '/api/trends']) {
    const res = await request.get(url);
    expect(res.headers()['content-type'], url).toMatch(/^application\/json/);
  }
  const all = await getJson<any>(request, '/api/clusters');
  const res = await request.get(`/api/clusters/${all.items[0].id}`);
  expect(res.headers()['content-type']).toMatch(/^application\/json/);
});
