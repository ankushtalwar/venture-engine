import { execFile } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { promisify } from 'node:util';
import type { FullConfig } from '@playwright/test';
import {
  DATA, FIXTURES, FRONTEND, FRONTEND_DIST, RECLUSTER, REPO, RUNS, homeFor, runDir, seedDb, seedsDir, serversDir, spaDir,
  type SeedName,
} from './lib/paths';
import { paindiscEnv } from './lib/server';

const run = promisify(execFile);

function prepareHome(seed: SeedName) {
  const home = homeFor(seed);
  fs.rmSync(home, { recursive: true, force: true });
  fs.mkdirSync(home, { recursive: true });
  for (const dir of ['config', 'prompts']) {
    fs.symlinkSync(path.join(REPO, dir), path.join(home, dir));
  }
}

async function paindisc(seed: SeedName, args: string[]) {
  try {
    await run('uv', ['run', 'paindisc', ...args],
      { cwd: REPO, env: paindiscEnv(seed), timeout: 180_000, maxBuffer: 16 * 1024 * 1024 });
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; message: string };
    throw new Error(`Seeding ${seed} failed: paindisc ${args.join(' ')}: ${e.message}\n${e.stdout ?? ''}\n${e.stderr ?? ''}`);
  }
}

/** `paindisc demo` into a pristine seed DB; each worker copies it before starting its server. */
async function seed(seed: SeedName) {
  prepareHome(seed);
  // `paindisc cluster` has no --db flag, so seed at the home's default path, then copy it aside.
  const work = path.join(homeFor(seed), 'data', 'paindisc.sqlite');
  await paindisc(seed, ['demo', '--fixture', FIXTURES[seed], '--db', work]);
  if (RECLUSTER[seed]) await paindisc(seed, ['cluster']);
  if (!fs.existsSync(work)) throw new Error(`Seeding ${seed} did not create ${work}`);
  fs.copyFileSync(work, seedDb(seed));
}

function newestMtime(target: string): number {
  if (!fs.existsSync(target)) return 0;
  const stat = fs.statSync(target);
  if (!stat.isDirectory()) return stat.mtimeMs;
  let newest = 0;
  for (const entry of fs.readdirSync(target)) {
    if (entry === 'node_modules' || entry === 'dist') continue;
    newest = Math.max(newest, newestMtime(path.join(target, entry)));
  }
  return newest;
}

function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === 'EPERM';
  }
}

/** Removes run directories left by runs that are no longer alive (crashed or killed). */
function pruneDeadRuns() {
  if (!fs.existsSync(RUNS)) return;
  for (const name of fs.readdirSync(RUNS)) {
    const pid = Number(name.split('-').pop());
    if (!Number.isInteger(pid) || !alive(pid)) fs.rmSync(path.join(RUNS, name), { recursive: true, force: true });
  }
}

/** One SPA build at a time across concurrent runs; a lock held by a dead process is taken over. */
async function withBuildLock<T>(fn: () => Promise<T>): Promise<T> {
  const lock = path.join(DATA, 'spa-build.lock');
  const deadline = Date.now() + 300_000;
  for (;;) {
    try {
      fs.writeFileSync(lock, String(process.pid), { flag: 'wx' });
      break;
    } catch {
      const holder = Number(fs.readFileSync(lock, 'utf8').trim());
      if (!alive(holder)) {
        fs.rmSync(lock, { force: true });
        continue;
      }
      if (Date.now() > deadline) throw new Error(`SPA build lock ${lock} held by pid ${holder} for 5 minutes`);
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  try {
    return await fn();
  } finally {
    fs.rmSync(lock, { force: true });
  }
}

/** Builds frontend/dist when it is missing or older than the frontend sources, then copies it aside. */
async function buildSpa() {
  await withBuildLock(async () => {
    if (process.env.E2E_SKIP_BUILD !== '1') await buildSpaUnlocked();
    await snapshotSpa();
  });
}

/** True when index.html exists and every /assets/ file it references is present. */
function completeBuild(dir: string): boolean {
  const index = path.join(dir, 'index.html');
  if (!fs.existsSync(index)) return false;
  const refs = [...fs.readFileSync(index, 'utf8').matchAll(/(?:src|href)="\/(assets\/[^"]+)"/g)].map((m) => m[1]);
  return refs.every((ref) => fs.existsSync(path.join(dir, ref)));
}

/** Copies frontend/dist into this run's directory; retries if someone else is rebuilding it right now. */
async function snapshotSpa() {
  if (!fs.existsSync(FRONTEND_DIST)) {
    // Normal for the docker project in CI: it tests the UI inside the container, not a host build.
    console.warn(`[e2e] ${FRONTEND_DIST} does not exist: local UI specs would fail; API and docker specs are unaffected.`);
    return;
  }
  for (let attempt = 0; attempt < 20; attempt++) {
    if (completeBuild(FRONTEND_DIST)) {
      fs.rmSync(spaDir(), { recursive: true, force: true });
      fs.cpSync(FRONTEND_DIST, spaDir(), { recursive: true });
      if (completeBuild(spaDir())) return;
    }
    await new Promise((r) => setTimeout(r, 1_000));
  }
  console.warn(`[e2e] no complete frontend build in ${FRONTEND_DIST}: UI specs will fail, API specs still run.`);
}

async function buildSpaUnlocked() {
  if (!fs.existsSync(path.join(FRONTEND, 'package.json'))) {
    console.warn('[e2e] frontend/ does not exist yet: UI specs will fail, API specs still run.');
    return;
  }
  const builtAt = newestMtime(path.join(FRONTEND_DIST, 'index.html'));
  const sources = Math.max(
    ...['src', 'index.html', 'package.json', 'vite.config.ts', 'vite.config.js', 'tsconfig.json']
      .map((p) => newestMtime(path.join(FRONTEND, p))),
  );
  if (builtAt && builtAt >= sources) return;
  if (!fs.existsSync(path.join(FRONTEND, 'node_modules'))) {
    console.warn('[e2e] frontend/node_modules is missing, so the SPA cannot be built. ' +
      'Run `npm --prefix frontend ci` (or set E2E_SKIP_BUILD=1 to test an existing dist/).');
    return;
  }
  console.log('[e2e] building the SPA (npm --prefix frontend run build)...');
  try {
    await run('npm', ['--prefix', FRONTEND, 'run', 'build'], { cwd: REPO, timeout: 300_000, maxBuffer: 32 * 1024 * 1024 });
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; message: string };
    throw new Error(`SPA build failed: ${e.message}\n${e.stdout ?? ''}\n${e.stderr ?? ''}`);
  }
}

export default async function globalSetup(config: FullConfig) {
  fs.mkdirSync(RUNS, { recursive: true });
  pruneDeadRuns();
  // Workers inherit this, so every path below resolves to this run's own directory.
  process.env.E2E_RUN_ID = `${Date.now().toString(36)}-${process.pid}`;
  fs.mkdirSync(seedsDir(), { recursive: true });
  fs.mkdirSync(serversDir(), { recursive: true });
  const projects = config.projects.map((p) => p.name);
  const seeds = Object.keys(FIXTURES) as SeedName[];
  console.log(`[e2e] run ${process.env.E2E_RUN_ID}: seeding ${seeds.join(', ')} (projects: ${projects.join(', ')})`);
  // Seed sequentially first so a pending `uv sync` happens once, not in parallel processes.
  await seed(seeds[0]);
  await Promise.all([...seeds.slice(1).map(seed), buildSpa()]);

  return async () => {
    if (process.env.E2E_KEEP_DATA === '1') {
      console.log(`[e2e] kept ${runDir()}`);
      return;
    }
    fs.rmSync(runDir(), { recursive: true, force: true });
  };
}
