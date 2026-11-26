# paindisc — Pain Discovery Agent

Finds recurring, expensive, underserved business problems from permitted sources, and backs every claim with a quote that code has checked against the source. Output: a weekly Pain Report of ranked problem cards with links, dates, spend evidence and computed confidence. It finds problems, not products.

The full contract is in [SPEC.md](../SPEC.md). Commands below run from the repo root.

## Try it offline (no keys, no network)

```bash
uv sync
uv run paindisc demo          # synthetic fixtures, mock LLM
open reports/demo-pain-report.html
uv run pytest                 # offline; Postgres tests run when PAINDISC_TEST_DATABASE_URL is set
```

A copy of the demo output is in [sample-demo-report.md](sample-demo-report.md).

## Review UI

```bash
uv run paindisc demo --db data/demo.sqlite
(cd frontend && npm ci && npm run build)
uv run paindisc serve --db data/demo.sqlite      # http://127.0.0.1:8000
```

Accept or reject pain cards, label signals valid / invalid / duplicate, merge or split clusters, re-synthesise a card, and see trends, sources, coverage and reports. Merges and splits are stored as overrides, so the next `paindisc cluster` keeps them; labels follow a cluster when its membership changes. Set `PAINDISC_API_TOKEN` to require a bearer token for writes. API reference: [api.md](api.md). For development, `scripts/dev.sh --demo` runs the API with reload plus the Vite dev server.

## Docker

`docker compose up -d --build` starts Postgres + pgvector, applies migrations, serves the UI on 127.0.0.1:8000 and schedules `scripts/weekly.sh` (Mondays 06:00 by default). Environment variables, Slack delivery, backups and security notes are in [deployment.md](deployment.md).

## Real runs

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env   # GITHUB_TOKEN and BRAVE_API_KEY are optional; all variables are in deployment.md
```

1. **Pick 3 pilot verticals** in `config/segments.yaml` (the shipped three are examples) and write their vocabulary the way operators talk. Optionally scope GitHub to repos of business tools with `github_scope`.
2. **Add job boards** to `config/job_boards.yaml`. `uv run paindisc boards-discover "office manager"` finds Greenhouse/Lever boards (needs `BRAVE_API_KEY`).
3. **Add pricing pages** to `config/pricing_targets.yaml`, setting `terms_checked: true` only after reading each site's terms.
4. Run:

```bash
uv run paindisc sources                                   # what's allowed, what's blocked and why
uv run paindisc run -s hn -s github -s greenhouse -s lever --budget 20
uv run paindisc run -s hn -s github -s brave --batch      # Message Batches API: half price, async
uv run paindisc deepdive --top 10                          # corroborate / contradict the top problems
uv run paindisc label                                      # accept / reject cards
uv run paindisc stats                                      # coverage and which queries pay off
```

```bash
uv run paindisc deepdive --agentic --top 5   # Claude picks searches on allowed sources; every query is logged
uv run paindisc trends                        # new / rising / falling / gone since the last comparable run
uv run paindisc deliver                       # Slack digest; a no-op until SLACK_WEBHOOK_URL is set and `slack` is allowed
```

Reports land in `reports/pain-report-YYYY-MM-DD.{md,html}`. `scripts/weekly.sh` is the weekly run (run, deep dive, trends, report, deliver). Set `DATABASE_URL=postgresql://...` to use Postgres instead of SQLite (`paindisc db migrate` applies the schema).

## How it works

```
planner ─► collectors (registry-gated) ─► dedupe ─► triage (Haiku) ─► extract (Sonnet) ─► verify (code)
                                                                                    │
report ◄── synthesise + skeptic (Opus) ◄── score ◄── cluster + merge judge ◄────────┘
   │
labels ─► tuning, ranker, planner rewards
```

- **Evidence contract.** Every field is observed (verbatim quote), inferred (labelled hypothesis), derived (financial only, formula shown) or unknown. Code drops a signal whose problem quote isn't in the source, downgrades unsupported fields, and never lets a financial figure through unless the source states it.
- **Two frequencies.** How often the pain hits one business is extracted; how often it's mentioned is computed from the cluster.
- **Buyer vs complainer.** `affected_role` is usually observed; `likely_buyer` is almost always inferred, and says so.
- **Confidence is computed**, not asked of a model ([SPEC](../SPEC.md) section 5).
- **Job posts** become verified tasks grouped across companies, with posted pay where stated. Put the [O*NET database](https://www.onetcenter.org/database.html) text files (CC BY 4.0) in `data/onet/` to map tasks to occupations.

## Sources

| Status | Sources |
|---|---|
| On | Hacker News (Algolia API), GitHub Issues (API), Greenhouse and Lever job-board APIs, Brave Search (snippets only), Wayback Machine, vendor pricing pages you've cleared |
| Waiting on approval | Reddit, Upwork, Product Hunt, G2 |
| Manual only | Capterra, Shopify App Store, AppExchange |

The registry (`config/registry.yaml`) is the only access control: collectors can't call a host it doesn't allow, and every redirect is re-checked. No logins, CAPTCHA solving or proxy rotation. Usernames are stored as salted hashes.

## Models and cost

Defaults: `claude-haiku-5-5` (triage, prices), `claude-sonnet-5-5` (extraction, jobs, merge judge), `claude-opus-5-5` (synthesis, skeptic). Override with `PAINDISC_MODEL_*`. At batch prices, 100k documents a month with ~15% passing triage comes to roughly $100/month in model spend.

## Layout

```
config/      registry, phrases, segments, job boards, pricing targets, scoring
prompts/     triage, extract, jobs, prices, merge, synthesize, skeptic
src/paindisc collectors/, planner, dedupe, verify, llm, mock, cluster, score, synth, report, pipeline, cli,
             store (SQLite), pgstore + migrations/ (Postgres), storage, trends, deliver, agent/ (deep dive), api/
frontend/    review UI (React + Vite + TypeScript)
e2e/         Playwright end-to-end tests
scripts/     weekly run, scheduler, dev, e2e, test-all, docker smoke
tests/       offline tests + synthetic fixtures + gold set
```
