import { expect, getJson, test } from '../../lib/fixtures';
import { gotoPage, rowByHeader, sources } from '../../lib/ui';

// Journeys 8-11: trends, sources, coverage, reports. All read-only.

test.use({ reseed: 'file' });

test.describe('trends', () => {
  test('with a single clustering run there is nothing to compare', async ({ page, request }) => {
    const api = await getJson<any>(request, '/api/trends');
    expect(api.previous_run_at).toBeNull();
    await gotoPage(page, '/trends', 'Trends');
    await expect(page.getByText('There is only one clustering run so far')).toBeVisible();
    await expect(page.getByTestId('trend-row')).toHaveCount(0);
  });
});

test.describe('sources', () => {
  test('blocked sources are listed with their reasons', async ({ page, request }) => {
    const api = await getJson<any[]>(request, '/api/sources');
    await gotoPage(page, '/sources', 'Sources');
    await expect(page.getByTestId('source-row')).toHaveCount(api.length);
    const blocked = api.filter((r) => !r.allowed);
    await expect(page.getByText(`${api.length - blocked.length} sources allowed, ${blocked.length} blocked.`)).toBeVisible();

    const reddit = sources.row(page, 'reddit');
    await expect(reddit).toHaveAttribute('data-allowed', 'false');
    await expect(reddit).toContainText('Blocked');
    await expect(reddit).toContainText(api.find((r) => r.source === 'reddit').reason);

    const hn = sources.row(page, 'hn');
    await expect(hn).toHaveAttribute('data-allowed', 'true');
    await expect(hn).toContainText('Allowed');

    await expect(page.locator('[data-testid="source-row"][data-allowed="false"]')).toHaveCount(blocked.length);
    for (const r of blocked) {
      const row = sources.row(page, r.source);
      await expect(row).toContainText('Blocked');
      await expect(row).toContainText(r.reason);
    }
  });

  test('the page offers no way to enable a source', async ({ page }) => {
    await gotoPage(page, '/sources', 'Sources');
    await expect(page.getByTestId('source-row').first()).toBeVisible();
    await expect(page.getByRole('main').getByRole('button')).toHaveCount(0);
    await expect(page.getByRole('main').getByRole('checkbox')).toHaveCount(0);
    await expect(page.getByRole('main').getByRole('switch')).toHaveCount(0);
  });
});

test.describe('coverage', () => {
  test('per-source coverage matches /api/stats', async ({ page, request }) => {
    const api = await getJson<any>(request, '/api/stats');
    await gotoPage(page, '/coverage', 'Coverage');
    const table = page.getByRole('table', { name: 'Per source' });
    await expect(table.getByTestId('coverage-source-row')).toHaveCount(api.sources.length);
    for (const row of api.sources) {
      const tr = rowByHeader(table, 'coverage-source-row', row.source);
      await expect(tr.getByRole('cell')).toHaveText(
        ['docs', 'duplicates', 'triaged', 'pain', 'signals', 'dropped'].map((k) => String(row[k])),
      );
    }
    const arms = page.getByRole('table', { name: 'Top query arms' });
    if (api.arms.length === 0) await expect(arms).toContainText('No query arms have run yet.');
    else await expect(arms.getByTestId('arm-row')).toHaveCount(api.arms.length);
  });
});

test.describe('reports', () => {
  test('reports are listed newest first', async ({ page, request }) => {
    const api = await getJson<any[]>(request, '/api/reports');
    await gotoPage(page, '/reports', 'Reports');
    const rows = page.getByTestId('report-row');
    await expect(rows).toHaveCount(api.length);
    const expected = [...api].sort((a, b) => b.modified.localeCompare(a.modified)).map((r) => r.name);
    await expect(rows.getByRole('rowheader')).toHaveText(expected);
    for (const r of api) {
      const row = rowByHeader(page, 'report-row', r.name);
      await expect(row).toContainText(r.kind === 'html' ? 'HTML' : 'Markdown');
      await expect(row.getByRole('link', { name: r.name })).toHaveAttribute('href', `/api/reports/${r.name}`);
    }
  });

  test('the HTML report opens in a new tab', async ({ page, context }) => {
    await gotoPage(page, '/reports', 'Reports');
    const link = page.getByRole('link', { name: 'demo-pain-report.html' });
    await expect(link).toHaveAttribute('target', '_blank');
    await expect(link).toHaveAttribute('rel', /noopener/);
    const [tab] = await Promise.all([context.waitForEvent('page'), link.click()]);
    await tab.waitForLoadState();
    await expect(tab.getByRole('heading', { level: 1, name: 'Pain Report' })).toBeVisible();
    await expect(tab.getByText('DEMO', { exact: true })).toBeVisible();
    await tab.close();
  });

  test('the Markdown report opens in a new tab as text', async ({ page, context }) => {
    await gotoPage(page, '/reports', 'Reports');
    const [tab] = await Promise.all([context.waitForEvent('page'), page.getByRole('link', { name: 'demo-pain-report.md' }).click()]);
    await tab.waitForLoadState();
    await expect(tab.locator('body')).toContainText('# Pain Report');
    await tab.close();
  });
});
