# Review UI API (contract v1)

JSON over HTTP for the review UI (SPEC backlog #5). The code lives in `src/paindisc/api/`: `schemas.py` holds the models and `app.py` the routes. FastAPI publishes the machine-readable contract at `/api/openapi.json`, with Swagger UI at `/api/docs`. If this page and the code disagree, fix one of them in the same change and tell `frontend` and `integration-tester`.

## Running it

```bash
uv run paindisc demo --db data/me/demo.sqlite                 # seed a database (mock LLM, synthetic fixtures)
uv run paindisc serve --db data/me/demo.sqlite --port 8101    # or: uv run python -m paindisc.api --db ... --port 8101
```

`python -m paindisc.api` takes `--db PATH` (a SQLite file, which overrides `DATABASE_URL`), `--home DIR`, `--host`, `--port`, `--reload` and `--mock`. Without `--db`, the store is Postgres when `DATABASE_URL` is set and `data/paindisc.sqlite` otherwise. From Python, the entry points are `paindisc.api.create_app(settings, store=None)` and `paindisc.api.run(settings, host, port, reload=False)`.

| Setting (env) | Effect |
|---|---|
| `PAINDISC_API_HOST`, `PAINDISC_API_PORT` | Bind address. The defaults are 127.0.0.1 and 8000. |
| `PAINDISC_API_TOKEN` | When set, every POST and DELETE needs `Authorization: Bearer <token>` and gets 401 without it. GETs stay open. |
| `PAINDISC_FRONTEND_DIST` | The built SPA, served at `/`. The default is `frontend/dist`. |
| `DATABASE_URL` | Selects the Postgres store. |
| `LLM_MOCK` / no `ANTHROPIC_API_KEY` | Synthesis runs on the offline mock. `/api/health` reports `llm_mock`. |

CORS allows only the Vite dev origins `http://localhost:5173` and `http://127.0.0.1:5173`. Vite also proxies `/api`, so the UI never needs CORS in production.

## Conventions

- Every response is JSON except the report files. Dates are ISO 8601.
- Errors come back as `{"detail": ...}`:
  - **400**: a semantic error, such as split signals that aren't in the cluster, splitting off every signal, or merging fewer than two distinct ids.
  - **401**: the bearer token is missing or wrong (only when a token is configured).
  - **404**: an unknown cluster, signal, override or report, or an unknown `/api/*` path.
  - **422**: schema validation failed. FastAPI then sends a list of problems in `detail`.
  - **502**: the model call behind `/synthesize` failed. Nothing is saved.
- Source text (quotes, titles, canonical statements) goes out as plain JSON strings. The UI must render it as text, never as HTML.
- No response contains an author hash or any solution, product or idea field.

## Shared shapes

```text
Evidence       = {value, status: "observed"|"inferred"|"derived"|"unknown", quote, basis}
ClusterSummary = {id, title, canonical, score, confidence: "High"|"Medium"|"Low", n_signals, n_authors,
                  source_types[], first_seen, last_seen,
                  components: {breadth, spend, frequency, underserved, intensity, recency, automatability, trend?},
                  label: "accept"|"reject"|null, trend: "new"|"rising"|"falling"|"steady"|null, has_synthesis}
ClusterDetail  = ClusterSummary + {
                  synthesis: {title, summary, hypotheses[]} | null,        # model-written: label it as such
                  skeptic:   {points: [{claim, kind: "evidence"|"hypothesis", signal_ids[]}]} | null,
                  card:      Card,
                  signals:   SignalView[] }                                 # specificity A->C, then intensity desc
SignalView     = {id, label: "valid"|"invalid"|"duplicate"|null, specificity: "A"|"B"|"C", intensity: 1-3,
                  author_type, canonical,
                  fields: {problem, segment, affected_role, likely_buyer, workaround, occurrence_freq,
                           financial_impact, existing_tool_complaint, automatability: Evidence},
                  spend_evidence: [{type, amount, quote}],
                  document: {id, source, source_type, url, title, published_at}}
Card           = report.card(): {id, title, summary, hypotheses[], skeptic[], canonical, confidence, score,
                  n_signals, n_authors, source_types[], span, segment[], affected_role[], likely_buyer[],
                  frequency, frequency_quotes: QuoteRef[], financial: [{value, status: "observed"|"derived",
                  quote, basis, url, label}], workarounds: QuoteRef[], tool_complaints: QuoteRef[],
                  spend: [{type, amount, quote, url, label}], evidence: QuoteRef[],
                  pricing: [{vendor, tiers, url, label}], components}
QuoteRef       = {quote, url, label}                                        # label = "source · date"
```

- `title` is the synthesis title when the cluster has one and the shortened canonical statement otherwise.
- `components.trend` is the informational 90-day evidence trend, which isn't scored.
- The top-level `trend` is the cluster's status in the latest clustering run compared with the baseline run, using `trends.compute_trends(...)["status_by_cluster"]`. The baseline is the newest run at least `trends.compare_days` older, or else the previous run. `trend` is null when there's only one run, and for clusters created by a manual merge or split since the latest run.
- `card.pricing` holds the vendor prices on record that match the cluster's quotes (vendor_prices).
- `card.segment`, `card.affected_role` and `card.likely_buyer` are strings in the form `"value (status, Nx)"`.

## Endpoints

### Read

| Method and path | Response |
|---|---|
| `GET /api/health` | `{status: "ok", store: "sqlite"\|"postgres", llm_mock, version}` |
| `GET /api/clusters?limit=50&offset=0&confidence=&label=` | `{items: ClusterSummary[], total}`. Sorted by score, highest first. `limit` runs from 1 to 500. `confidence` is one of `High`, `Medium` or `Low`. `label` is one of `accept`, `reject` or `unlabelled`. `total` counts the matches after filtering and before pagination. |
| `GET /api/clusters/{id}` | `ClusterDetail`, or 404. |
| `GET /api/overrides` | `[{id, kind: "merge"\|"split", payload: {signal_ids[]}, created_at}]`, oldest first. |
| `GET /api/trends` | `{run_at, previous_run_at, items: [{cluster_id, title, status, score, previous_score, delta, previous_cluster_id}]}`. `status` is one of `new`, `rising`, `falling` or `gone`; steady clusters aren't listed. For `new`, `previous_score` and `delta` are null. For `gone`, `cluster_id` is the old id and `score` and `delta` are null. With fewer than two runs, `previous_run_at` is null and `items` is empty. |
| `GET /api/sources` | `[{source, allowed, route, domains, reviewed, reason}]`, from `Registry.summary()`. |
| `GET /api/stats` | `{sources: [{source, docs, duplicates, triaged, pain, signals, dropped}], arms: [...]}`. `arms` holds the top 20 query arms, `{arm_id, source, query, runs, empty_runs, docs, verified}`, sorted by verified, then docs. |
| `GET /api/reports` | `[{name, kind: "md"\|"html", size, modified}]`, newest first. |
| `GET /api/reports/{name}` | The report file, Markdown as `text/markdown` and HTML as `text/html`. |

`GET /api/reports/{name}` only serves names matching `[A-Za-z0-9][A-Za-z0-9._-]*\.(md|html)` that sit directly inside `reports/`. Anything else is a 404, including traversal, hidden files and symlinks that point outside. HTML reports carry a sandboxing `Content-Security-Policy` and `nosniff`, because they quote fetched web text.

### Write

These need the bearer token when one is configured.

| Method and path | Body | Response |
|---|---|---|
| `POST /api/clusters/{id}/label` | `{label: "accept"\|"reject"\|null, note?}` | `ClusterSummary`. `null` clears the label. Writes the `labels` table (`target_type="cluster"`). |
| `POST /api/signals/{id}/label` | `{label: "valid"\|"invalid"\|"duplicate"\|null, note?}` | `{id, label}`. `null` clears the label. Returns 404 for an unknown signal. |
| `POST /api/clusters/merge` | `{ids: [>= 2 cluster ids]}` | `ClusterDetail` of the new cluster. |
| `POST /api/clusters/{id}/split` | `{signal_ids: [>= 1]}` | `{clusters: [split_off, remainder]}`, two `ClusterSummary`s. |
| `POST /api/clusters/{id}/synthesize` | none | `ClusterDetail` with a fresh synthesis and skeptic pass. In mock mode it uses the offline mock. Returns 502 if the model call fails. |
| `DELETE /api/overrides/{id}` | none | `{id, deleted: true}`, or 404. |

**Merge.** The new cluster holds every signal of the listed clusters. It is rescored with `make_cluster`, has a new id, no synthesis and no label. The old clusters are removed. One `merge` override is stored with `payload.signal_ids` set to the new cluster's signals.

**Split.** The listed signals must all belong to the cluster, and at least one signal must stay behind. The result is two rescored clusters, each with a new id, no synthesis and no label, and the original cluster is removed. One `split` override is stored with `payload.signal_ids` set to the signals that were split off.

**Overrides.** Every `paindisc cluster` run rebuilds clusters from scratch, and cluster ids are hashes of their signal ids. `Pipeline.cluster()` therefore re-applies the stored overrides, oldest first, after automatic clustering and the LLM merge judge. It uses `cluster.apply_overrides`:

- A merge override says that the clusters now holding any of those signals become one cluster. A new signal that automatic clustering placed next to them comes along.
- A split override says that those signals form their own cluster, wherever they landed.
- Signal ids that no longer exist are ignored.
- Clusters that no override touched keep their id and synthesis.

Deleting an override only changes what the next run does. The current clusters stay as they are.

Manual merges and splits don't write a trend snapshot (`replace_clusters(..., snapshot=False)`). Only clustering runs count for trends.

## SPA

Every non-`/api` GET serves a file from `frontend_dist` when one exists at that path, and `index.html` otherwise, which supports client-side routes. Paths that resolve outside `frontend_dist` get `index.html`. Without a build, these requests return 404 with `{"detail": "frontend build not found (cd frontend && npm run build)"}`.

## Concurrency

The API holds one process-wide write lock around label, merge, split, synthesize and override writes. Synthesis keeps the lock through the model call, so a concurrent merge can't be overwritten by a stale save. The store itself is thread-safe.
