import { defineConfig, devices } from '@playwright/test';
import type { Reseed, Target } from './lib/fixtures';
import type { SeedName } from './lib/paths';

type Options = { seed: SeedName; apiToken: string | undefined; target: Target; basePort: number; reseed: Reseed };

/** Token used by the `sqlite-auth` project (a throwaway value for a local test server). */
const E2E_TOKEN = 'e2e-test-token-not-a-secret';

const docker = process.env.E2E_DOCKER === '1';

export default defineConfig<Options>({
  testDir: './tests',
  outputDir: './test-results',
  globalSetup: './global-setup.ts',
  // Spec files run in parallel, tests inside a file run in order on one worker. Each worker owns one
  // API server (port 8765 + worker index) on a private copy of the seed DB, reset between tests.
  fullyParallel: false,
  workers: process.env.CI ? 2 : 4,
  retries: 0,
  forbidOnly: !!process.env.CI,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { open: 'never', outputFolder: './playwright-report' }]],
  use: {
    ...devices['Desktop Chrome'],
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'sqlite',
      testIgnore: ['**/auth.spec.ts'],
      use: { seed: 'demo', target: 'local' },
    },
    {
      name: 'sqlite-auth',
      testMatch: ['**/auth.spec.ts'],
      use: { seed: 'demo', target: 'local', apiToken: E2E_TOKEN },
    },
    ...(docker
      ? [{
          name: 'docker',
          // The compose app is one shared server, so its specs run one at a time.
          workers: 1,
          testIgnore: ['**/auth.spec.ts'],
          use: { seed: 'demo' as SeedName, target: 'external' as Target, apiToken: process.env.E2E_DOCKER_TOKEN },
        }]
      : []),
  ],
});
