# Deployment: local, Docker, compose, scheduler, Slack

How to run paindisc as a service: the review UI and JSON API, Postgres + pgvector, and the weekly scheduler that collects, analyses, reports and posts a digest to Slack. Commands run from the repo root. The contract is still [SPEC.md](../SPEC.md); the API is in [api.md](api.md).

Everything here works offline with `LLM_MOCK=true`. No step needs a key unless you want real model output or real collection.

## 1. Local (no Docker)

```bash
uv sync
uv run pytest -q                    # offline suite
uv run paindisc demo                # synthetic fixtures -> data/demo.sqlite + reports/demo-pain-report.*
uv run paindisc serve --db data/demo.sqlite      # API + built UI on http://127.0.0.1:8000
```

Development, with auto-reload on both sides:

```bash
scripts/dev.sh --demo               # seeds data/demo.sqlite, API on 127.0.0.1:8101 (--reload), Vite on :5173
scripts/dev.sh                      # same, over data/paindisc.sqlite (or DATABASE_URL if set)
```

Vite proxies `/api` to the dev API (`VITE_API_TARGET`, default `http://127.0.0.1:8101`). Ctrl-C (or killing the script) stops both, and if either server dies the script stops the other. `DEV_DB=data/me/dev.sqlite scripts/dev.sh --demo` seeds and serves a different file.

Tests:

```bash
scripts/test-all.sh                 # pytest, frontend lint + unit tests + typecheck + build, Playwright e2e
SKIP_E2E=1 scripts/test-all.sh      # without the browser tests
scripts/e2e.sh                      # build the UI, run e2e/ (E2E_INSTALL_BROWSERS=true installs Chromium first)
```

## 2. Docker image

One image serves the API + UI, runs the scheduler and any CLI command.

```bash
docker build -t paindisc:local .
docker run --rm -p 127.0.0.1:8000:8000 -e LLM_MOCK=true paindisc:local       # SQLite inside the container
docker run --rm paindisc:local paindisc --help
```

What's in it: Python 3.12 slim, dependencies from `uv.lock` (`uv sync --frozen --no-dev`, no `embeddings` extra), `src/ config/ prompts/ scripts/`, the built UI at `/app/frontend/dist`, the synthetic demo fixture, and [supercronic](https://github.com/aptible/supercronic) (version and SHA-256 pinned in the Dockerfile). It runs as the non-root user `paindisc` (uid 10001); only `/app/data` and `/app/reports` are writable. `PAINDISC_HOME=/app`. A `HEALTHCHECK` polls `/api/health`.

No secrets are baked in: `.dockerignore` excludes `.env*`, `data/`, `reports/`, `.git`, `.venv` and `node_modules`. Keys are passed at run time.

## 3. Compose: Postgres, API, scheduler

```bash
docker compose up -d --build        # db -> migrate (one-shot) -> api + scheduler
open http://127.0.0.1:8000
docker compose logs -f api scheduler
docker compose down                 # stop; data stays in the volumes
```

| Service | What | Port (host) |
|---|---|---|
| `db` | `pgvector/pgvector:pg17`, `pg_isready` healthcheck, volume `pgdata` | `127.0.0.1:55432` |
| `migrate` | one-shot `paindisc db migrate`; api and scheduler wait for it to succeed | none |
| `api` | `paindisc serve` (API + UI) | `127.0.0.1:8000` |
| `scheduler` | `scripts/scheduler.sh`: supercronic runs `scripts/weekly.sh` on `WEEKLY_CRON` | none |

All app containers read `.env` if it exists (`env_file`, `required: false`), get `DATABASE_URL` pointing at the `db` service (this wins over any `DATABASE_URL` in `.env`), mount the `data` and `reports` volumes, and mount `./config` read-only, so edits to the registry or segments apply on the next run or restart without a rebuild.

Useful one-offs:

```bash
docker compose run --rm api paindisc sources                    # what's allowed / blocked
docker compose run --rm api paindisc demo --database-url postgresql://paindisc:paindisc@db:5432/paindisc   # WIPES the db, seeds the demo
docker compose run --rm scheduler scripts/weekly.sh             # a weekly run now
docker compose run --rm api paindisc trends
docker compose cp api:/app/reports ./reports-from-docker        # copy reports to the host (also listed in the UI)
```

Don't use `docker compose config` to debug while a real `.env` exists: it prints the merged environment, keys included.

### Smoke test

```bash
scripts/docker-smoke.sh
```

Builds the image, starts `db` + `migrate` + `api` as compose project `paindisc-devops` with `LLM_MOCK=true` and a throwaway env file (your `.env` is never loaded), seeds the demo into Postgres, checks `/api/health` (store `postgres`, mock on), `/api/clusters`, the UI at `/` (a built JS asset and the SPA fallback for deep links), that the port is bound to 127.0.0.1, that `trends` runs and `deliver` skips cleanly, and that the scheduler starts. Then `down -v`. It refuses to run as project `paindisc` (the real stack) because it deletes its volumes. Only one smoke run per project at a time: a second one exits 3 without touching the first, and a project that still has containers (a `SMOKE_KEEP=1` stack) must be removed with `docker compose -p <project> down -v` first. `SMOKE_KEEP=1` leaves it running; `SMOKE_API_PORT` / `SMOKE_DB_PORT` move the ports; `SMOKE_SKIP_BUILD=1` tests an image you already built (`SMOKE_IMAGE`, default `paindisc:smoke`); `SMOKE_E2E=1` also runs the Playwright `docker` project against the stack (needs node, uv and the e2e dependencies on the host). CI builds the image and runs the smoke test with `SMOKE_E2E=1` on every push.

## 4. The weekly run

`scripts/weekly.sh` runs: `run` (collect, triage, extract, jobs, prices, cluster, synthesise, report) -> `deepdive` -> `trends` -> `report` -> `deliver`. `run` and `report` must succeed; `deepdive`, `trends` and `deliver` are soft (a failure is logged, the report is still written, and the script exits 1 at the end).

In compose, the `scheduler` container runs it on `WEEKLY_CRON` (default `0 6 * * 1`, Monday 06:00) in `WEEKLY_TZ` (default `UTC`; for Monday 06:00 India time use `WEEKLY_TZ=Asia/Kolkata`). supercronic logs each run to `docker compose logs scheduler` and never overlaps runs. An invalid schedule stops the container with an error.

Outside Docker, use cron: `0 6 * * 1  cd /path/to/venture-engine && scripts/weekly.sh >> data/weekly.log 2>&1`.

With `WEEKLY_BATCH=true` (the default) each LLM step goes through the Message Batches API at half price and waits for its batch, so a run can take an hour or more.

## 5. Environment variables

Put secrets in `.env` (gitignored, never copied into the image). Everything is optional.

### Application (`src/paindisc/config.py`)

| Variable | Default | What |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Real model calls. Unset means mock mode. |
| `LLM_MOCK` | `false` | `true` forces the offline mock LLM even with a key. CI and the smoke test set it. |
| `PAINDISC_MODEL_TRIAGE` | `claude-haiku-5-5` | Triage and price extraction. |
| `PAINDISC_MODEL_EXTRACT` | `claude-sonnet-5-5` | Extraction, jobs, merge judge, agentic deep dive. |
| `PAINDISC_MODEL_SYNTH` | `claude-opus-5-5` | Synthesis and skeptic pass. |
| `GITHUB_TOKEN` | unset | Higher GitHub search rate limit (read-only, no scopes needed). |
| `BRAVE_API_KEY` | unset | Brave Search collector; without it that collector is skipped. |
| `PAINDISC_USER_AGENT` | generic | Set it with a contact address for polite crawling. |
| `PAINDISC_SALT` | `paindisc` | Salt for author hashes. Set a private value once and never change it: changing it breaks distinct-author counts across runs. |
| `PAINDISC_HOME` | cwd (`/app` in the image) | Where `config/`, `prompts/`, `data/`, `reports/` live. |
| `DATABASE_URL` | unset | `postgresql://...` selects Postgres; unset uses SQLite at `data/paindisc.sqlite`. Compose sets it. |
| `PAINDISC_API_HOST` | `127.0.0.1` | Bind address for `paindisc serve` (the image passes `0.0.0.0` inside the container). |
| `PAINDISC_API_PORT` | `8000` | API port (also used by the healthcheck). |
| `PAINDISC_API_TOKEN` | unset | If set, write endpoints need `Authorization: Bearer <token>`. Set it before exposing the API beyond localhost. |
| `PAINDISC_FRONTEND_DIST` | `<home>/frontend/dist` | Built UI served at `/`. |
| `SLACK_WEBHOOK_URL` | unset | Incoming-webhook URL for the weekly digest. A credential: keep it in `.env`. Also needs the registry entry (section 6). |
| `PAINDISC_SBERT_MODEL` | `all-MiniLM-L6-v2` | Only with `embedding: sbert` and `uv sync --extra embeddings` (not installed in the image). |
| `PAINDISC_TEST_DATABASE_URL` | unset | Tests only: runs the Postgres tests against this database (CI sets it). |

### Compose

| Variable | Default | What |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `paindisc` | Database credentials. Change the password before the first `up` (it's set when the volume is created). |
| `POSTGRES_PORT` | `55432` | Host port for Postgres (127.0.0.1 only). |
| `PAINDISC_PORT` | `8000` | Host port for the API + UI (127.0.0.1 only). |
| `PAINDISC_IMAGE` | `paindisc:local` | Image tag to build and run. |
| `PAINDISC_ENV_FILE` | `.env` | Env file loaded into the app containers. |
| `WEEKLY_CRON` | `0 6 * * 1` | Scheduler cron expression. |
| `WEEKLY_TZ` | `UTC` | Time zone the schedule is read in. |

### Weekly run (`scripts/weekly.sh`)

| Variable | Default |
|---|---|
| `WEEKLY_SOURCES` | `hn github brave greenhouse lever pricing` |
| `WEEKLY_BUDGET` | `25` queries per query-driven source |
| `WEEKLY_BATCH` | `true` (Message Batches API) |
| `WEEKLY_DEEPDIVE_TOP` | `10` |
| `WEEKLY_DEEPDIVE_SOURCES` | `hn github` |
| `WEEKLY_AGENTIC` | `false`; `true` runs the tool-using deep dive (`deepdive --agentic`), which logs every query |
| `WEEKLY_REPORT_TOP` | `20` |
| `WEEKLY_DELIVER_TOP` | `10` |

### Scripts

`DEV_API_PORT` (8101), `DEV_UI_PORT` (5173), `DEV_DB` for `dev.sh` (it sets `VITE_API_TARGET` for Vite); `E2E_INSTALL_BROWSERS` for `e2e.sh`; `SKIP_FRONTEND`, `SKIP_E2E` for `test-all.sh`; `SMOKE_PROJECT`, `SMOKE_API_PORT`, `SMOKE_DB_PORT`, `SMOKE_IMAGE`, `SMOKE_KEEP` for `docker-smoke.sh`.

## 6. Slack delivery

`paindisc deliver` (the last weekly step) posts a compact digest: the top cards with computed confidence and score (cards you rejected in the review UI are left out), new and rising trend alerts with a count of falling and gone ones, and the report's file path. In mock mode the header says so. Titles from fetched content are escaped, so a title can't ping a channel or smuggle a link.

It goes out through `PoliteClient` as source `slack`, so the registry gates it like any collector. It's a no-op that exits 0 with a message (`skipped: SLACK_WEBHOOK_URL is not set`, or `skipped: registry refused Slack delivery: ...`) until you do all of this:

1. Create a Slack app with an incoming webhook for the channel you want ([docs](https://api.slack.com/messaging/webhooks)).
2. Read Slack's API terms yourself.
3. Put `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...` in `.env`.
4. In `config/registry.yaml`, set the `slack` entry to `allowed: true` and fill `reviewed:` (date) and `reviewed_by:` (your name). Only you flip it; no code or agent does.
5. `docker compose up -d` (picks up the new env; `./config` is mounted, so no rebuild) and test with `docker compose run --rm api paindisc deliver`.

`deliver` exits 1 only when Slack rejects the post. Errors never include the webhook URL.

**Email is not implemented on purpose.** SMTP would open a network path outside `PoliteClient` and the registry, which the project rules forbid. If you need mail, use Slack's email forwarding for the channel, or add an HTTP email API as a registry entry (`allowed: false` until reviewed) and a sender that goes through `PoliteClient`.

## 7. Backups (Postgres volume)

Compose keeps state in named volumes: `paindisc_pgdata` (the database: documents, signals, clusters, labels, overrides, snapshots), `paindisc_data` and `paindisc_reports`. `docker compose down -v` deletes them; plain `down` doesn't.

```bash
mkdir -p backups
docker compose exec -T db pg_dump -U paindisc -Fc paindisc > backups/paindisc-$(date +%F).dump    # backup
docker compose exec -T db pg_restore -U paindisc -d paindisc --clean --if-exists < backups/paindisc-2026-11-30.dump  # restore
```

Back up before upgrading the Postgres major version (`pg17` -> later needs a dump and restore, not a volume reuse). Labels are the expensive part to recreate: they're your review decisions and the ranker's training data.

## 8. Security notes

- **Localhost only.** Compose publishes the API on `127.0.0.1:8000` and Postgres on `127.0.0.1:55432`. Inside the container the API binds `0.0.0.0`, so `docker run -p 8000:8000` (without `127.0.0.1:`) would expose it on every interface. Don't.
- **Exposing the UI** (a team server, a tunnel): set `PAINDISC_API_TOKEN` to a long random value (`openssl rand -hex 32`) so writes need `Authorization: Bearer <token>`, put TLS and authentication in front (reverse proxy, VPN or an identity-aware tunnel), and change `POSTGRES_PASSWORD`. Reads stay open without a token: everything shown is public-source evidence, but treat it as internal.
- **Secrets** live in `.env` (gitignored, excluded from the build context) and reach containers at run time only. The Slack webhook URL is a credential.
- **Outbound network** from the app goes only through `PoliteClient` and `config/registry.yaml`, Slack included. The container healthcheck only calls the API on localhost.
- **Non-root**: the app runs as uid 10001 with a read-only install; only `data/` and `reports/` are writable, and `config/` is mounted read-only.
- **Pinned inputs**: base images by tag, uv by version, supercronic by version and SHA-256, GitHub Actions by commit SHA. CI needs no secrets.
