# End-to-end tests (Playwright)

Browser and API tests for the review UI (`frontend/`) and the JSON API (`src/paindisc/api/`). They run offline: mock LLM, synthetic fixtures, no keys, no network beyond `127.0.0.1`.

## Run

```bash
cd e2e
npm ci
npx playwright install chromium   # once
npx playwright test               # sqlite + sqlite-auth projects
```

From the repo root, `scripts/e2e.sh` does the same (it also builds the frontend). Other commands:

| Command | What it runs |
|---|---|
| `npx playwright test tests/ui/merge.spec.ts` | one spec file |
| `npx playwright test --project=sqlite-auth` | only the token tests |
| `npm run test:docker` | the suite against the Docker/Postgres stack (see below) |
| `npx playwright test --repeat-each=3` | flakiness check |
| `npx playwright show-report` | HTML report of the last run (traces and screenshots for failures) |
| `npm run typecheck` | `tsc --noEmit` over the suite |

Requirements: Node 20+, `uv`, and `frontend/node_modules` (`npm --prefix frontend ci`) so the SPA can be built.

## How it works

**Global setup** (`global-setup.ts`) runs once per invocation:

1. Creates a private run directory, `data/integration-tester/runs/<run id>/`, so two runs on one machine never share files. Teardown deletes it (`E2E_KEEP_DATA=1` keeps it), and the next run prunes directories left by dead runs.
2. Seeds three pristine SQLite DBs with `uv run paindisc demo --fixture ... --db ...`:
   - `demo`: `tests/fixtures/demo_docs.jsonl`. 5 cards from 7 verified signals; the top card is Medium confidence with 3 signals, the rest are single-signal Low cards.
   - `xss`: `e2e/fixtures/xss_docs.jsonl`. Titles, text and metrics carry `<img onerror>`, `<svg onload>`, `<iframe srcdoc>`, a markdown `javascript:` link and a `javascript:` document URL. Each payload sets `window.__pwned` if it ever runs. Author hashes are distinctive (`e2eauthorhash...`) so a leak is easy to find.
   - `trends`: the demo, plus a second `paindisc cluster` run at the default threshold. That gives two snapshot runs: 3 new cards, 1 gone, the rest steady.

   Each seed gets its own `PAINDISC_HOME` (config/ and prompts/ symlinked, a private reports/), so the demo never overwrites the repo's `reports/`, and the user's `.env` (and any `ANTHROPIC_API_KEY` in it) is never loaded.
3. Builds `frontend/dist` if it is missing or older than `frontend/src` (under a lock, `data/integration-tester/spa-build.lock`), then copies it into the run directory. Servers serve the copy, so a rebuild of `frontend/dist` during the run can't break the tests. `E2E_SKIP_BUILD=1` skips the build and uses the existing `dist/`.

**Servers.** There is no shared `webServer`. Each Playwright worker owns one `paindisc serve` process (worker fixture `app` in `lib/fixtures.ts`) on its own copy of a seed DB. It uses port `8765 + worker index`, or a free port if that one is taken. Environment: `LLM_MOCK=true`, no API keys, `PAINDISC_FRONTEND_DIST` pointing at the run's SPA copy. Set `E2E_SERVER_CMD=module` to use `python -m paindisc.api` instead of `paindisc serve`.

**Isolation.** Before every test that follows another test on the same worker, the worker restarts its server from a fresh copy of the seed (`reseed: 'test'`, the default). Read-only spec files opt into `reseed: 'file'`. No test sees another test's labels, merges or splits. Inside a spec, `freshStore()` reseeds mid-test, and `LocalServer.bounce()` restarts the process on the same DB to prove state lives in the store.

**Selectors** live in `lib/ui.ts` (role/name first, `data-testid` for repeated rows: `card-row`, `signal-row`, `evidence-status`, `trend-row`, `source-row`, `coverage-source-row`, `report-row`, `override-row`). A UI change should need edits in that file only.

## Projects

| Project | Server | Specs |
|---|---|---|
| `sqlite` | local `paindisc serve`, SQLite | everything except `tests/auth/` |
| `sqlite-auth` | local, with `PAINDISC_API_TOKEN` set | `tests/auth/auth.spec.ts` |
| `docker` (only with `E2E_DOCKER=1`) | the compose app, Postgres | everything except `tests/auth/`, one worker |

### Docker / Postgres

`scripts/docker-smoke.sh` runs it for you with `SMOKE_E2E=1`. By hand, against a running compose stack:

```bash
E2E_DOCKER=1 E2E_SKIP_BUILD=1 \
E2E_EXTERNAL_URL=http://127.0.0.1:8000 \
E2E_DATABASE_URL=postgresql://paindisc:paindisc@127.0.0.1:55432/paindisc \
npx playwright test --project=docker
```

The suite reseeds Postgres between tests with `uv run paindisc demo --database-url $E2E_DATABASE_URL --fixture ...` (plus `paindisc cluster` for the trends seed). Set `E2E_RESEED_CMD` to override it (`{fixture}` is replaced by the fixture path). The stack must run with `LLM_MOCK=true`; the health test fails otherwise, which stops the re-synthesize test from calling a real model. Report-file checks are skipped against Docker, because the suite doesn't reseed the container's reports volume.

## What is covered

| # | Journey | Spec |
|---|---|---|
| 1 | Cards list loads, ranked, with confidence and score; filters; navigation | `tests/ui/cards-list.spec.ts` |
| 2 | Card detail: verbatim quotes with source links and dates, evidence status badges, model-written synthesis, hypotheses, skeptic | `tests/ui/card-detail.spec.ts` |
| 3 | Accept / reject / clear persist across reload and drive the label filter | `tests/ui/card-labels.spec.ts` |
| 4 | Signal labels persist | `tests/ui/signals.spec.ts` |
| 5 | Merge creates a new card and an override; deleting the override | `tests/ui/merge.spec.ts` |
| 6 | Split moves signals into a new card | `tests/ui/signals.spec.ts` |
| 7 | Re-synthesize (mock LLM) | `tests/ui/resynthesize.spec.ts` |
| 8 | Trends: groups, links, badges; single-run empty state | `tests/ui/trends.spec.ts`, `tests/ui/pages.spec.ts` |
| 9 | Sources: blocked sources and their reasons; no way to enable one | `tests/ui/pages.spec.ts` |
| 10 | Coverage table matches `/api/stats` | `tests/ui/pages.spec.ts` |
| 11 | Reports list; HTML and Markdown open | `tests/ui/pages.spec.ts` |
| 12 | API token: 401 without it, works with it, UI prompt, kept per tab only | `tests/auth/auth.spec.ts` |
| 13 | Untrusted content renders as inert text; `javascript:` URLs are not links; reports are sandboxed and contain no `javascript:` links | `tests/security/xss.spec.ts` |
| 14 | No author hashes or solution/product fields in the API, OpenAPI or UI; no per-person routes | `tests/security/privacy.spec.ts` |
| - | Contract v1 shape for every endpoint, validation errors, path traversal, SPA fallback | `tests/api/contract.spec.ts` |

## Debugging a failure

- Every failed test attaches the tail of its API server log (`server-log`), a screenshot and a trace: `npx playwright show-trace test-results/<test>/trace.zip`.
- `E2E_KEEP_DATA=1` keeps the run directory (seed DBs, per-worker DB copies, reports) for inspection with `sqlite3`.
- If a port is busy, the server moves to a free one; `lsof -nP -iTCP:8765 -sTCP:LISTEN` shows who holds 8765.
