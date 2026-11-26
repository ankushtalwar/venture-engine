import { expect, type Locator, type Page } from '@playwright/test';

/**
 * Locators for the review UI (frontend/src). Role/name first; data-testid where the frontend exposes one
 * for a repeated item (card rows, signal rows, evidence badges). Keep every selector here so a UI change
 * is a one-file fix.
 */

export const nav = (page: Page) => page.getByRole('navigation', { name: 'Main' });

// --------------------------------------------------------------------------- cards list

export const cards = {
  async goto(page: Page, query = '') {
    await page.goto(`/cards${query}`);
    await expect(page.getByRole('heading', { level: 1, name: 'Pain cards' })).toBeVisible();
    // Wait for the list (or its empty state) rather than the loading placeholder.
    await expect(page.getByTestId('card-row').first().or(page.locator('.empty'))).toBeVisible();
  },
  rows: (page: Page) => page.getByTestId('card-row'),
  row: (page: Page, clusterId: string) => page.locator(`[data-testid="card-row"][data-cluster-id="${clusterId}"]`),
  titleLink: (row: Locator) => row.getByRole('link'),
  score: (row: Locator) => row.getByTestId('card-score'),
  confidence: (row: Locator) => row.getByTestId('card-confidence'),
  label: (row: Locator) => row.getByTestId('card-label'),
  select: (row: Locator) => row.getByRole('checkbox'),
  confidenceFilter: (page: Page) => page.getByLabel('Confidence', { exact: true }),
  labelFilter: (page: Page) => page.getByLabel('Label', { exact: true }),
  mergeButton: (page: Page) => page.getByRole('button', { name: 'Merge selected' }),
  mergeRegion: (page: Page) => page.getByRole('region', { name: 'Merge cards' }),
  count: (page: Page) => page.getByRole('form', { name: 'Filter cards' }).locator('[aria-live]'),
  /** After a filter change the old rows stay up (aria-busy) until the new page arrives. */
  async settled(page: Page) {
    await expect(page.locator('ol.card-list[aria-busy="true"]')).toHaveCount(0);
  },
  async filter(page: Page, which: 'Confidence' | 'Label', option: { label: string }) {
    await page.getByLabel(which, { exact: true }).selectOption(option);
    await cards.settled(page);
  },
};

export const sources = {
  row: (page: Page, name: string) => page.locator(`[data-testid="source-row"][data-source="${name}"]`),
};

// --------------------------------------------------------------------------- card detail

export const detail = {
  async goto(page: Page, clusterId: string) {
    await page.goto(`/cards/${clusterId}`);
    await expect(page.locator(`[data-testid="card-detail"][data-cluster-id="${clusterId}"]`)).toBeVisible();
  },
  heading: (page: Page) => page.getByRole('heading', { level: 1 }),
  side: (page: Page) => page.getByRole('complementary', { name: 'Decision and score' }),
  currentLabel: (page: Page) => page.getByRole('complementary', { name: 'Decision and score' }).getByTestId('card-label'),
  accept: (page: Page) => page.getByRole('button', { name: 'Accept', exact: true }),
  reject: (page: Page) => page.getByRole('button', { name: 'Reject', exact: true }),
  clearLabel: (page: Page) => page.getByRole('button', { name: 'Clear label' }),
  resynthesize: (page: Page) => page.getByRole('button', { name: 'Re-synthesize' }),
  modelWritten: (page: Page) => page.getByTestId('model-written'),
  hypotheses: (page: Page) => page.getByTestId('model-written').getByTestId('hypothesis'),
  skeptic: (page: Page) => page.getByRole('region', { name: 'Skeptic' }),
  signals: (page: Page) => page.getByTestId('signal-row'),
  signal: (page: Page, signalId: string) => page.locator(`[data-testid="signal-row"][data-signal-id="${signalId}"]`),
  signalLabel: (signal: Locator) => signal.getByRole('radiogroup', { name: 'Signal label' }),
  signalLabelOption: (signal: Locator, name: 'Valid' | 'Invalid' | 'Duplicate' | 'None') =>
    signal.getByRole('radiogroup', { name: 'Signal label' }).getByRole('radio', { name, exact: true }),
  signalSelect: (signal: Locator) => signal.getByRole('checkbox', { name: 'Select signal for split' }),
  async expandFields(signal: Locator) {
    const details = signal.locator('details.signal-fields');
    if (!(await details.evaluate((d) => (d as HTMLDetailsElement).open))) {
      await signal.getByText('All fields for this signal').click();
    }
    await expect(details).toHaveAttribute('open', '');
  },
  splitButton: (page: Page) => page.getByRole('button', { name: 'Split selected signals' }),
  evidenceBadges: (scope: Page | Locator) => scope.getByTestId('evidence-status'),
  quotes: (scope: Page | Locator) => scope.getByTestId('quote'),
};

// --------------------------------------------------------------------------- other pages

export async function gotoPage(page: Page, path: string, heading: RegExp | string) {
  await page.goto(path);
  await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
}

/** A table row whose row header is exactly `name` (sources, coverage). */
export function rowByHeader(scope: Page | Locator, testId: string, name: string): Locator {
  // The `has` locator is resolved inside each row, so it must start from the page, not from `scope`.
  const page = 'goto' in scope ? scope : scope.page();
  return scope.getByTestId(testId).filter({ has: page.getByRole('rowheader', { name, exact: true }) });
}

export const tokenDialog = {
  dialog: (page: Page) => page.getByRole('dialog', { name: 'API token' }),
  input: (page: Page) => page.getByRole('dialog', { name: 'API token' }).getByLabel('API token'),
  save: (page: Page) => page.getByRole('dialog', { name: 'API token' }).getByRole('button', { name: 'Save token' }),
  cancel: (page: Page) => page.getByRole('dialog', { name: 'API token' }).getByRole('button', { name: 'Cancel' }),
};
