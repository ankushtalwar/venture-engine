import { expect, listClusters, test } from '../../lib/fixtures';
import { cards, nav } from '../../lib/ui';

// Journey 1: the cards list loads, ranked, with confidence and score.

test.use({ reseed: 'file' }); // read-only spec

test('/ redirects to the ranked cards list', async ({ page, request }) => {
  const api = await listClusters(request);
  await page.goto('/');
  await expect(page).toHaveURL(/\/cards$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Pain cards' })).toBeVisible();

  const rows = cards.rows(page);
  await expect(rows).toHaveCount(api.length);
  const shownIds = await rows.evaluateAll((els) => els.map((e) => e.getAttribute('data-cluster-id')));
  expect(shownIds, 'same order as the API (score, highest first)').toEqual(api.map((c) => c.id));
  await expect(cards.count(page)).toHaveText(`${api.length} cards`);
});

test('each row shows its title, score and confidence', async ({ page, request }) => {
  const api = await listClusters(request);
  await cards.goto(page);
  for (const c of api) {
    const row = cards.row(page, c.id);
    await expect(cards.titleLink(row)).toHaveText(c.title);
    await expect(cards.score(row)).toHaveText(c.score.toFixed(2));
    await expect(cards.confidence(row)).toHaveText(c.confidence);
    await expect(cards.label(row)).toHaveText('Unlabelled');
    await expect(row).toContainText(`${c.n_signals} signal`);
  }
  const shownScores = (await cards.rows(page).getByTestId('card-score').allInnerTexts()).map(Number);
  expect(shownScores).toEqual([...shownScores].sort((a, b) => b - a));
});

test('confidence filter narrows the list and is kept in the URL', async ({ page, request }) => {
  const api = await listClusters(request);
  await cards.goto(page);
  await cards.filter(page, 'Confidence', { label: 'Medium' });
  await expect(page).toHaveURL(/confidence=Medium/);
  const medium = api.filter((c) => c.confidence === 'Medium');
  await expect(cards.rows(page)).toHaveCount(medium.length);
  await expect(cards.row(page, medium[0].id)).toBeVisible();
  await expect(cards.count(page)).toHaveText(`${medium.length} card${medium.length === 1 ? '' : 's'} match`);

  await cards.filter(page, 'Confidence', { label: 'Low' });
  await expect(cards.rows(page)).toHaveCount(api.filter((c) => c.confidence === 'Low').length);
  for (const conf of await cards.rows(page).getByTestId('card-confidence').allInnerTexts()) {
    expect(conf).toBe('Low');
  }

  await page.reload();
  await expect(cards.confidenceFilter(page)).toHaveValue('Low');
  await expect(cards.rows(page)).toHaveCount(api.filter((c) => c.confidence === 'Low').length);
});

test('an empty filter result offers to clear the filters', async ({ page, request }) => {
  const api = await listClusters(request);
  await cards.goto(page, '?confidence=High');
  await expect(page.getByText('No cards match these filters.')).toBeVisible();
  await expect(cards.rows(page)).toHaveCount(0);
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect(cards.rows(page)).toHaveCount(api.length);
  await expect(page).not.toHaveURL(/confidence=/);
});

test('clicking a title opens the card', async ({ page, request }) => {
  const [top] = await listClusters(request);
  await cards.goto(page);
  await cards.titleLink(cards.row(page, top.id)).click();
  await expect(page).toHaveURL(new RegExp(`/cards/${top.id}$`));
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(top.title);
});

test('header shows the store and the mock model; nav reaches every page', async ({ page, target }) => {
  await cards.goto(page);
  await expect(page.getByTestId('health')).toContainText(target === 'external' ? 'Postgres' : 'SQLite');
  await expect(page.getByTestId('health')).toContainText('Mock model');
  for (const [name, path] of [['Trends', '/trends'], ['Overrides', '/overrides'], ['Sources', '/sources'],
    ['Coverage', '/coverage'], ['Reports', '/reports'], ['Pain cards', '/cards']] as const) {
    await nav(page).getByRole('link', { name }).click();
    await expect(page).toHaveURL(new RegExp(`${path}$`));
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  }
});

test('unknown client route shows the not-found page, not a blank screen', async ({ page }) => {
  await page.goto('/no/such/page');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(nav(page)).toBeVisible();
});
