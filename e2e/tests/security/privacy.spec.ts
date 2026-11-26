import fs from 'node:fs';
import { clusterDetail, expect, getJson, listClusters, test } from '../../lib/fixtures';
import { FIXTURES } from '../../lib/paths';
import { FORBIDDEN_KEY, allKeys } from '../../lib/shape';
import { cards, detail, gotoPage } from '../../lib/ui';

// Journey 14: no author hashes and no solution/product fields anywhere in the API or the UI.
// The xss seed gives each document a distinctive author_hash (e2eauthorhash...), so a leak is easy to spot.

test.use({ seed: 'xss', reseed: 'file' });

const AUTHOR_HASHES = fs.readFileSync(FIXTURES.xss, 'utf8').split('\n').filter(Boolean)
  .map((l) => JSON.parse(l).author_hash as string).filter(Boolean);

const PAGES: [string, RegExp][] = [
  ['/cards', /pain cards/i], ['/trends', /trends/i], ['/overrides', /overrides/i],
  ['/sources', /sources/i], ['/coverage', /coverage/i], ['/reports', /reports/i],
];

/** UI copy that would mean the tool proposes solutions (it only finds problems). */
const SOLUTION_COPY = /\b(solutions?|product ideas?|business ideas?|startup ideas?|pitch|proposed (fix|product)|what to build|opportunit(y|ies))\b/i;

test('fixture sanity: author hashes are present in the seed', () => {
  expect(AUTHOR_HASHES.length).toBeGreaterThanOrEqual(3);
});

test('no API response contains an author hash or an author/solution field', async ({ request }) => {
  const bodies: unknown[] = [];
  for (const url of ['/api/clusters', '/api/sources', '/api/stats', '/api/trends', '/api/overrides', '/api/reports', '/api/health']) {
    bodies.push(await getJson(request, url));
  }
  for (const c of await listClusters(request)) bodies.push(await clusterDetail(request, c.id));
  const text = JSON.stringify(bodies);
  for (const h of AUTHOR_HASHES) expect(text, `author hash ${h} leaked`).not.toContain(h);
  expect([...allKeys(bodies)].filter((k) => FORBIDDEN_KEY.test(k))).toEqual([]);
  for (const name of ['demo-pain-report.md', 'demo-pain-report.html']) {
    const body = await (await request.get(`/api/reports/${name}`)).text();
    for (const h of AUTHOR_HASHES) expect(body, `${name} leaks ${h}`).not.toContain(h);
  }
});

test('no page shows an author hash', async ({ page, request }) => {
  const check = async (where: string) => {
    const html = await page.content();
    for (const h of AUTHOR_HASHES) expect(html, `${where} leaks ${h}`).not.toContain(h);
  };
  for (const [path, heading] of PAGES) {
    await gotoPage(page, path, heading);
    await check(path);
  }
  for (const c of await listClusters(request)) {
    await detail.goto(page, c.id);
    await check(`/cards/${c.id}`);
  }
});

test('there is no per-person view', async ({ page, request }) => {
  for (const path of ['/authors', '/people', '/users', '/author/e2eauthorhash7f3a9c01']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    expect(await page.content()).not.toContain('e2eauthorhash');
  }
  const paths = Object.keys((await getJson<any>(request, '/api/openapi.json')).paths ?? {});
  expect(paths.filter((p) => /author|user|people|person/i.test(p))).toEqual([]);
});

test('no UI label, heading, column or button talks about solutions or products', async ({ page, request }) => {
  const labelsOf = async () => page.evaluate(() => {
    const sel = 'h1,h2,h3,h4,h5,h6,th,dt,label,legend,button,[role=columnheader],[role=tab],nav a,summary,caption';
    return [...document.querySelectorAll(sel)].map((e) => (e as HTMLElement).innerText.trim()).filter(Boolean);
  });
  const seen: string[] = [];
  for (const [path, heading] of PAGES) {
    await gotoPage(page, path, heading);
    seen.push(...(await labelsOf()));
  }
  for (const c of await listClusters(request)) {
    await detail.goto(page, c.id);
    seen.push(...(await labelsOf()));
  }
  // Card titles quote sources, so they are data, not UI copy: drop them before checking.
  const titles = new Set((await listClusters(request)).map((c) => c.title));
  const copy = [...new Set(seen)].filter((t) => !titles.has(t));
  expect(copy.filter((t) => SOLUTION_COPY.test(t)), 'UI copy that proposes solutions').toEqual([]);
});

test('the cards list does not show author identifiers, only counts', async ({ page, request }) => {
  const all = await listClusters(request);
  await cards.goto(page);
  for (const c of all) {
    await expect(cards.row(page, c.id)).toContainText(`${c.n_authors} author`);
  }
});
