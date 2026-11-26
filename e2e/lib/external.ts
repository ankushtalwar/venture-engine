import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { FIXTURES, RECLUSTER, REPO, type SeedName } from './paths';
import { paindiscEnv, runCluster, waitForHealth } from './server';

const run = promisify(execFile);

/**
 * A server we don't start (the Docker compose app, Postgres-backed). Reseeding runs
 * `paindisc demo --database-url ...` against the same database, which resets it via `store.reset()`.
 *
 *   E2E_EXTERNAL_URL   app URL (default http://127.0.0.1:8000)
 *   E2E_DATABASE_URL   Postgres URL the app uses, reachable from this machine (required to reseed)
 *   E2E_RESEED_CMD     optional override, run with `sh -c`; `{fixture}` is replaced by the fixture path
 */
export class ExternalServer {
  readonly baseURL = (process.env.E2E_EXTERNAL_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '');

  constructor(readonly seed: SeedName, readonly token?: string) {}

  logTail(): string {
    return `(external server at ${this.baseURL}; see its container logs)`;
  }

  async start() {
    await this.reseed();
    await waitForHealth(this.baseURL, 30_000);
  }

  async stop() {}

  async restart() {
    await this.reseed();
  }

  /** Automatic `paindisc cluster` against the stack's database, as the scheduler would run it. */
  async recluster(): Promise<string> {
    const url = process.env.E2E_DATABASE_URL;
    if (!url) throw new Error('E2E_DATABASE_URL is required to re-cluster the external database.');
    return runCluster(this.seed, url);
  }

  private async reseed() {
    const fixture = FIXTURES[this.seed];
    const custom = process.env.E2E_RESEED_CMD;
    if (custom) {
      await run('sh', ['-c', custom.replaceAll('{fixture}', fixture)], { cwd: REPO, env: process.env, timeout: 180_000 });
      return;
    }
    const url = process.env.E2E_DATABASE_URL;
    if (!url) throw new Error('E2E_DATABASE_URL (or E2E_RESEED_CMD) is required to reseed the external server.');
    await run('uv', ['run', 'paindisc', 'demo', '--database-url', url, '--fixture', fixture],
      { cwd: REPO, env: paindiscEnv(this.seed), timeout: 180_000 });
    if (RECLUSTER[this.seed]) {
      await run('uv', ['run', 'paindisc', 'cluster'],
        { cwd: REPO, env: paindiscEnv(this.seed, { DATABASE_URL: url }), timeout: 180_000 });
    }
  }
}
