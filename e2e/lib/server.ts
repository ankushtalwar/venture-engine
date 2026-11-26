import { execFile, spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { promisify } from 'node:util';
import { REPO, homeFor, seedDb, serversDir, spaDir, type SeedName } from './paths';

export interface ServerOptions {
  seed: SeedName;
  /** Preferred port; if something else holds it, the server takes a free one instead. */
  port: number;
  /** When set, the server requires `Authorization: Bearer <token>` on write endpoints. */
  token?: string;
  /** Distinguishes DB copies between projects/workers. */
  tag: string;
}

/** True when `paindisc serve` is wired in the CLI; otherwise fall back to `python -m paindisc.api`. */
function hasServeCommand(): boolean {
  if (process.env.E2E_SERVER_CMD === 'module') return false;
  if (process.env.E2E_SERVER_CMD === 'cli') return true;
  try {
    return /def serve\(|command\(["']serve["']\)/.test(fs.readFileSync(path.join(REPO, 'src/paindisc/cli.py'), 'utf8'));
  } catch {
    return false;
  }
}

/** Environment for every paindisc process the suite starts: mock LLM, isolated home, no keys. */
export function paindiscEnv(seed: SeedName, extra: Record<string, string | undefined> = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  for (const key of ['ANTHROPIC_API_KEY', 'DATABASE_URL', 'PAINDISC_API_TOKEN', 'SLACK_WEBHOOK_URL', 'GITHUB_TOKEN',
    'BRAVE_API_KEY', 'PAINDISC_DB', 'PAINDISC_API_PORT', 'PAINDISC_API_HOST']) {
    delete env[key];
  }
  env.LLM_MOCK = 'true';
  env.PAINDISC_HOME = homeFor(seed);
  env.PAINDISC_FRONTEND_DIST = spaDir();
  env.PYTHONUNBUFFERED = '1';
  for (const [k, v] of Object.entries(extra)) {
    if (v === undefined) delete env[k];
    else env[k] = v;
  }
  return env;
}

export async function portInUse(port: number, host = '127.0.0.1'): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect({ port, host });
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('error', () => resolve(false));
  });
}

const execFileP = promisify(execFile);

/** `uv run paindisc cluster` against DATABASE_URL (paindisc cluster has no --db flag). Returns its output. */
export async function runCluster(seed: SeedName, databaseUrl: string): Promise<string> {
  try {
    const { stdout, stderr } = await execFileP('uv', ['run', '--no-sync', 'paindisc', 'cluster'],
      { cwd: REPO, env: paindiscEnv(seed, { DATABASE_URL: databaseUrl }), timeout: 120_000 });
    return stdout + stderr;
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; message: string };
    throw new Error(`paindisc cluster failed: ${e.message}\n${e.stdout ?? ''}\n${e.stderr ?? ''}`);
  }
}

/** A port the OS says is free right now. */
export async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address() as net.AddressInfo;
      srv.close(() => resolve(port));
    });
  });
}

export async function waitForHealth(baseURL: string, timeoutMs: number, proc?: ChildProcess, log?: () => string) {
  const deadline = Date.now() + timeoutMs;
  let last = '';
  while (Date.now() < deadline) {
    if (proc && proc.exitCode !== null) {
      throw new Error(`paindisc API exited with code ${proc.exitCode} before it was healthy.\n${log?.() ?? ''}`);
    }
    try {
      const res = await fetch(`${baseURL}/api/health`);
      if (res.ok) return;
      last = `HTTP ${res.status}`;
    } catch (err) {
      last = String(err);
    }
    await new Promise((r) => setTimeout(r, 150)); // polling interval for a process we just spawned, not a test wait
  }
  throw new Error(`paindisc API at ${baseURL} not healthy after ${timeoutMs} ms (${last}).\n${log?.() ?? ''}`);
}

/**
 * One API + SPA server on a private copy of a pristine seed DB. `restart()` throws the copy away and
 * starts again from the seed, which is how tests get a clean store without sharing state.
 */
export class LocalServer {
  private proc?: ChildProcess;
  private output: string[] = [];
  private port?: number;
  readonly dbPath: string;

  constructor(private readonly opts: ServerOptions) {
    this.dbPath = path.join(serversDir(), `${opts.tag}.sqlite`);
  }

  /** Fixed for the life of this object (restarts reuse the port), so the request fixture stays valid. */
  get baseURL(): string {
    if (this.port === undefined) throw new Error('LocalServer.baseURL read before start()');
    return `http://127.0.0.1:${this.port}`;
  }

  get seed(): SeedName {
    return this.opts.seed;
  }

  get token(): string | undefined {
    return this.opts.token;
  }

  logTail(lines = 60): string {
    return this.output.join('').split('\n').slice(-lines).join('\n');
  }

  private freshCopy() {
    fs.mkdirSync(serversDir(), { recursive: true });
    for (const suffix of ['', '-wal', '-shm', '-journal']) {
      fs.rmSync(this.dbPath + suffix, { force: true });
    }
    const src = seedDb(this.opts.seed);
    if (!fs.existsSync(src)) {
      throw new Error(`Seed DB ${src} is missing: globalSetup should have created it.`);
    }
    fs.copyFileSync(src, this.dbPath);
    for (const suffix of ['-wal', '-shm']) {
      if (fs.existsSync(src + suffix)) fs.copyFileSync(src + suffix, this.dbPath + suffix);
    }
  }

  async start(opts: { keepDb?: boolean } = {}) {
    if (this.port === undefined) {
      this.port = (await portInUse(this.opts.port)) ? await freePort() : this.opts.port;
    } else if (await portInUse(this.port)) {
      throw new Error(`Port ${this.port} was taken by another process between restarts ` +
        `(lsof -nP -iTCP:${this.port} -sTCP:LISTEN).`);
    }
    if (!opts.keepDb) this.freshCopy();
    // --no-sync: globalSetup's `uv run` already synced the venv; servers must not re-sync it mid-run.
    const port = String(this.port);
    const args = hasServeCommand()
      ? ['run', '--no-sync', 'paindisc', 'serve', '--db', this.dbPath, '--host', '127.0.0.1', '--port', port]
      : ['run', '--no-sync', 'python', '-m', 'paindisc.api', '--db', this.dbPath, '--host', '127.0.0.1', '--port', port];
    this.output = [];
    this.proc = spawn('uv', args, {
      cwd: REPO,
      env: paindiscEnv(this.opts.seed, { PAINDISC_API_TOKEN: this.opts.token }),
      detached: true, // own process group, so stop() also reaches the python child of `uv run`
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const keep = (chunk: Buffer) => {
      this.output.push(chunk.toString());
      if (this.output.length > 400) this.output.splice(0, 200);
    };
    this.proc.stdout?.on('data', keep);
    this.proc.stderr?.on('data', keep);
    await waitForHealth(this.baseURL, 60_000, this.proc, () => this.logTail());
  }

  async stop() {
    const proc = this.proc;
    this.proc = undefined;
    if (!proc || proc.exitCode !== null || proc.pid === undefined) return;
    const exited = new Promise<void>((resolve) => proc.once('exit', () => resolve()));
    try {
      process.kill(-proc.pid, 'SIGTERM');
    } catch {
      return;
    }
    const timer = setTimeout(() => {
      try { process.kill(-proc.pid!, 'SIGKILL'); } catch { /* already gone */ }
    }, 5_000);
    await exited;
    clearTimeout(timer);
    // The listening socket can linger for a moment after exit; wait until the port is free.
    const deadline = Date.now() + 5_000;
    while (Date.now() < deadline && this.port !== undefined && await portInUse(this.port)) {
      await new Promise((r) => setTimeout(r, 50));
    }
  }

  /** Throw the DB copy away and start again from the pristine seed. */
  async restart() {
    await this.stop();
    await this.start();
  }

  /** Restart the process on the same DB copy (to prove state lives in the store, not in memory). */
  /** Runs an automatic `paindisc cluster` on this server's DB (the weekly job), while the server keeps running. */
  async recluster(): Promise<string> {
    return runCluster(this.opts.seed, `sqlite:///${this.dbPath}`);
  }

  async bounce() {
    await this.stop();
    await this.start({ keepDb: true });
  }
}
