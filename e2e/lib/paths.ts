import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

/** Repository root (the directory with pyproject.toml). */
export const REPO = path.resolve(here, '..', '..');
export const E2E = path.join(REPO, 'e2e');

/** Scratch data for this suite only (gitignored via data/). Never data/paindisc.sqlite. */
export const DATA = path.join(REPO, 'data', 'integration-tester');
export const RUNS = path.join(DATA, 'runs');

/**
 * Every invocation of the suite works in its own directory, data/integration-tester/runs/<run id>, so two
 * runs on one machine (say scripts/test-all.sh and a developer) never share seeds, DB copies or reports.
 * globalSetup sets E2E_RUN_ID; workers inherit it.
 */
export function runDir(): string {
  const id = process.env.E2E_RUN_ID;
  if (!id) throw new Error('E2E_RUN_ID is not set: run the suite through `npx playwright test` (globalSetup sets it).');
  return path.join(RUNS, id);
}

/**
 * Isolated PAINDISC_HOME per seed: config/ and prompts/ are symlinks into the repo, reports/ is private.
 * Two reasons: `paindisc demo` writes reports into <home>/reports (we must not overwrite the user's
 * reports/), and the user's .env (which may hold ANTHROPIC_API_KEY) is not loaded from here, so the
 * servers stay in mock LLM mode.
 */
export function homeFor(seed: SeedName): string {
  return path.join(runDir(), `home-${seed}`);
}

/** Pristine seed DBs, one per seed. */
export function seedsDir(): string {
  return path.join(runDir(), 'seeds');
}

/** Per-worker DB copies the API servers write to. */
export function serversDir(): string {
  return path.join(runDir(), 'servers');
}

export const FRONTEND = path.join(REPO, 'frontend');
export const FRONTEND_DIST = path.join(FRONTEND, 'dist');

/**
 * This run's private copy of frontend/dist. Servers serve the copy, so a rebuild of frontend/dist by
 * someone else during the run (Vite empties the folder first) cannot break the tests.
 */
export function spaDir(): string {
  return path.join(runDir(), 'spa');
}

/**
 * Seeds: `paindisc demo` on a fixture. `trends` is the demo plus a second `paindisc cluster` run at the
 * default (tighter) threshold, so there are two snapshot runs to compare (new + gone clusters).
 */
export const FIXTURES = {
  demo: path.join(REPO, 'tests', 'fixtures', 'demo_docs.jsonl'),
  xss: path.join(E2E, 'fixtures', 'xss_docs.jsonl'),
  trends: path.join(REPO, 'tests', 'fixtures', 'demo_docs.jsonl'),
} as const;

export const RECLUSTER: Partial<Record<keyof typeof FIXTURES, boolean>> = { trends: true };

export type SeedName = keyof typeof FIXTURES;

export function seedDb(seed: SeedName): string {
  return path.join(seedsDir(), `${seed}.sqlite`);
}
