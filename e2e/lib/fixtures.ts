import { test as base, expect, type APIRequestContext, type Page } from '@playwright/test';
import { ExternalServer } from './external';
import type { SeedName } from './paths';
import { LocalServer } from './server';

export type Target = 'local' | 'external';
/** When the store is reset: before every test that follows another (default), once per spec file, or never. */
export type Reseed = 'test' | 'file' | 'none';

type Server = LocalServer | ExternalServer;

interface WorkerState {
  server: Server;
  dirty: boolean;
  lastFile?: string;
}

type WorkerFixtures = {
  seed: SeedName;
  apiToken: string | undefined;
  target: Target;
  basePort: number;
  app: WorkerState;
};

type TestFixtures = {
  reseed: Reseed;
  isolation: void;
  /** Headers that authorise write calls (empty when the server has no token). */
  authHeaders: Record<string, string>;
  /** Restart the store from the pristine seed in the middle of a test. */
  freshStore: () => Promise<void>;
};

export const test = base.extend<TestFixtures, WorkerFixtures>({
  seed: ['demo', { scope: 'worker', option: true }],
  apiToken: [undefined, { scope: 'worker', option: true }],
  target: ['local', { scope: 'worker', option: true }],
  basePort: [8765, { scope: 'worker', option: true }],

  app: [async ({ seed, apiToken, target, basePort }, use, workerInfo) => {
    const tag = `${workerInfo.project.name}-w${workerInfo.parallelIndex}`;
    const server: Server = target === 'external'
      ? new ExternalServer(seed, apiToken)
      : new LocalServer({ seed, token: apiToken, port: basePort + workerInfo.parallelIndex, tag });
    await server.start();
    try {
      await use({ server, dirty: false });
    } finally {
      await server.stop();
    }
  }, { scope: 'worker', timeout: 120_000 }],

  baseURL: async ({ app }, use) => {
    await use(app.server.baseURL);
  },

  reseed: ['test', { option: true }],

  isolation: [async ({ app, reseed }, use, testInfo) => {
    const newFile = app.lastFile !== testInfo.file;
    const needed = app.dirty && (reseed === 'test' || (reseed === 'file' && newFile));
    if (needed) {
      await app.server.restart();
      app.dirty = false;
    }
    app.lastFile = testInfo.file;
    await use();
    app.dirty = true;
    if (testInfo.status !== testInfo.expectedStatus) {
      await testInfo.attach('server-log', { body: app.server.logTail(), contentType: 'text/plain' });
    }
  }, { auto: true, timeout: 90_000 }],

  freshStore: async ({ app }, use) => {
    await use(async () => {
      await app.server.restart();
    });
  },

  authHeaders: async ({ app }, use) => {
    await use(app.server.token ? { Authorization: `Bearer ${app.server.token}` } : {});
  },

  page: async ({ page }, use, testInfo) => {
    const uncaught: string[] = [];
    page.on('pageerror', (err) => uncaught.push(`${err.name}: ${err.message}\n${err.stack ?? ''}`));
    await use(page);
    if (uncaught.length) {
      await testInfo.attach('uncaught-page-errors', { body: uncaught.join('\n\n'), contentType: 'text/plain' });
    }
    expect(uncaught, 'the SPA threw uncaught errors (see attachment)').toEqual([]);
  },
});

export { expect };

// --------------------------------------------------------------------------- API helpers

export interface Evidence { value: unknown; status: 'observed' | 'inferred' | 'derived' | 'unknown'; quote: string; basis: string }

export interface ClusterSummary {
  id: string; title: string; canonical: string; score: number; confidence: 'High' | 'Medium' | 'Low';
  n_signals: number; n_authors: number; source_types: string[]; first_seen: string | null; last_seen: string | null;
  components: Record<string, number>; label: 'accept' | 'reject' | null;
  trend: 'new' | 'rising' | 'falling' | 'steady' | null; has_synthesis: boolean;
}

export interface SignalView {
  id: string; label: 'valid' | 'invalid' | 'duplicate' | null; specificity: string; intensity: number;
  author_type: string; canonical: string; fields: Record<string, Evidence>;
  spend_evidence: { type: string; amount: string; quote: string }[];
  document: { id: string; source: string; source_type: string; url: string; title: string; published_at: string | null };
}

export interface ClusterDetail extends ClusterSummary {
  synthesis: { title: string; summary: string; hypotheses: string[] } | null;
  skeptic: { points: { claim: string; kind: 'evidence' | 'hypothesis'; signal_ids: string[] }[] } | null;
  card: Record<string, unknown>;
  signals: SignalView[];
}

export async function getJson<T>(request: APIRequestContext, url: string): Promise<T> {
  const res = await request.get(url);
  expect(res.status(), `GET ${url}: ${await res.text().catch(() => '')}`).toBe(200);
  return (await res.json()) as T;
}

export async function listClusters(request: APIRequestContext, query = ''): Promise<ClusterSummary[]> {
  const body = await getJson<{ items: ClusterSummary[]; total: number }>(request, `/api/clusters${query}`);
  return body.items;
}

export async function clusterDetail(request: APIRequestContext, id: string): Promise<ClusterDetail> {
  return getJson<ClusterDetail>(request, `/api/clusters/${id}`);
}

/** Title the UI shows for a card: the synthesis title when there is one, else the canonical. */
export function displayTitle(c: Pick<ClusterSummary, 'title' | 'canonical'>): string {
  return c.title || c.canonical;
}

/** Waits for a write call the UI makes, and returns its response (optionally only one with `status`). */
export function waitForWrite(page: Page, method: string, pathPattern: RegExp, status?: number) {
  return page.waitForResponse((r) => r.request().method() === method && pathPattern.test(new URL(r.url()).pathname)
    && (status === undefined || r.status() === status));
}
