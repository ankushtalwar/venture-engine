# Pain Discovery Agent — Specification

This file is the contract for anyone (human or coding agent) working on the repo. If code and spec disagree, fix one of them in the same change.

## 1. Purpose

Find recurring, expensive, underserved business problems that software and AI agents could address, for small and mid-sized businesses, professional services firms, B2B SaaS companies and vertical businesses.

The output is a ranked list of **pain cards**, each backed by verified quotes with links and dates. It is a shortlist for customer interviews, not a verdict.

Non-goals: proposing products, solutions or pricing; scraping sources that haven't been permitted; profiling individuals.

## 2. Pipeline

| Stage | Module | What it does |
|---|---|---|
| Plan | `planner.py` | Builds query arms (phrase family x segment vocabulary x source) and picks them by Thompson sampling. Reward: a collected document yields at least one verified signal. Empty runs count as failures. |
| Collect | `collectors/`, `http.py` | Fetches only through `config/registry.yaml`. Every request and redirect hop is checked against the registry; robots.txt is enforced for crawled routes; per-source rate limits. |
| De-duplicate | `dedupe.py` | Exact (normalised content hash) and near-duplicate (MinHash LSH, Jaccard 0.8 on word 5-shingles). Duplicates are stored but marked and never analysed. |
| Triage | `pipeline.triage` | Cheap model (default Haiku 5.5): is this a recurring business pain? Forums, issues, search snippets and reviews only. |
| Extract | `pipeline.extract` | Strong model (default Sonnet 5.5) fills the evidence contract (section 3); `verify.py` checks every quote in code. |
| Jobs | `pipeline.jobs`, `jobtasks.py` | Job posts become verified atomic tasks (frequency, tools, inputs, posted pay), grouped across postings and optionally mapped to O*NET tasks. |
| Prices | `pipeline.prices` | Pricing-page snapshots (live and Wayback) become verified price tiers; changes over time are flagged. |
| Cluster | `cluster.py` | Groups signals on the job to be done (TF-IDF or optional sentence embeddings; average-linkage agglomerative clustering), then an LLM merge judge combines clusters describing the same job. |
| Score | `score.py` | Visible components and computed confidence (section 5). |
| Synthesise | `synth.py` | Strongest model (default Opus 5.5) writes a title, summary and interview hypotheses from verified evidence only; a skeptic pass argues against each card. |
| Deep dive | `pipeline.deepdive` | Targeted searches for the top clusters, then re-analysis. |
| Report | `report.py` | Markdown + HTML Pain Report, plus job-task, pricing-history and coverage sections. |

All LLM steps run in parallel sync mode or through the Message Batches API (`--batch`). Without an API key, `mock.py` stands in so everything runs offline.

## 3. Evidence contract

Every extracted field is `{value, status, quote, basis}` with status:

- `observed`: the document states it; `quote` is a verbatim span.
- `inferred`: model hypothesis; `basis` says why.
- `derived`: financial impact only; stated quantity x stated or cited rate, formula and source in `basis`.
- `unknown`.

Fields per signal: problem, segment, affected_role, likely_buyer, workaround, occurrence_freq (per business, not mention volume), financial_impact, spend_evidence[], existing_tool_complaint, automatability (always inferred), specificity (A/B/C), author_type, intensity (1-3), canonical (`[segment] must [job] because [cause]; today they [workaround]`). Source URL and dates live on the document.

Verification rules (`verify.py`), applied in code after every extraction:

1. `problem` must be observed and its quote found in the document, or the signal is dropped.
2. Quotes match after Unicode/whitespace/case normalisation; quotes of 20+ characters may fuzzy-match (partial ratio >= 95); shorter quotes must match exactly.
3. Any other observed field whose quote isn't found becomes unknown. `derived` is only allowed for financial impact.
4. `financial_impact` is never inferred. Observed figures must appear literally in the quote. Derived figures need a basis and a verified quote for the stated quantity.
5. Spend evidence needs a found quote; an amount whose numbers aren't in the quote is cleared.
6. The schema has no solution field.

## 4. Sources and access

`config/registry.yaml` is the only access control. Allowed now: Hacker News (Algolia API), GitHub Issues (API), Greenhouse and Lever public job-board APIs, Brave Search API (snippets only; result pages are never fetched), Wayback Machine, and vendor pricing pages listed in `config/pricing_targets.yaml` with `terms_checked: true`.

Blocked until approval or written permission: Reddit (Data API approval, Responsible Builder Policy), Upwork (approved key; personal/internal use), Product Hunt (non-commercial by default), G2 (API/syndication access via G2), Capterra (terms bar automated extraction), Shopify App Store and AppExchange (no review APIs). Enabling one means: get approval, flip `allowed`, record who reviewed the terms and when, add a collector with fixture tests.

Never: logins on someone else's behalf, CAPTCHA solving, proxy rotation, user-agent spoofing, or third-party resellers that skip a platform's approval. Usernames are stored only as salted hashes.

## 5. Scoring and confidence

Components, each in [0, 1]: breadth (distinct authors x source types), spend (signals with observed spend or financial impact), frequency (best observed per-business frequency), underserved (complaints about existing tools), intensity (inferred), recency (share of evidence from the last 12 months), automatability (inferred; weight 0 by default). Trend (last 90 vs previous 90 days) is shown, not scored. Weights live in `config/scoring.yaml`.

Confidence is computed, never asked of a model:

- High: >= 5 distinct authors, >= 2 source types, >= 1 spend signal, newest evidence <= 365 days old.
- Medium: >= 3 distinct authors, or >= 2 authors with a spend signal.
- Low: everything else (all single-author clusters).

## 6. Storage

SQLite (`data/paindisc.sqlite`): documents (JSON + indexed columns + MinHash), triage, extraction_runs, signals, job_runs, price_runs, clusters, labels, arms. Swap for Postgres + pgvector when volumes require it; keep the Store interface.

## 7. Quality bars

- `uv run pytest` passes offline (no keys, no network).
- No stored quote is missing from its document (`tests/test_pipeline.py`).
- Gold-set evaluation (`paindisc eval`): triage precision >= 0.9; zero financial figures on documents marked `no_figures`; duplicate rate in the top 50 clusters < 5%.
- Re-run the gold set on every prompt or model change.

## 8. Backlog

Each item is sized for one agent session. Acceptance criteria in brackets.

1. **Reddit collector** once approved [OAuth client, subreddit search, honour deletions on re-check, fixture tests, registry flipped].
2. **Upwork collector** once approved [marketplace job search, budgets as observed spend, fixture tests].
3. **Licensed job-posting feed** for small-business roles [one provider behind the BOARD_COLLECTORS interface; terms recorded in registry].
4. **Procurement collectors**: USAspending (award values), SAM.gov, TED, UK Find a Tender [public APIs; awards as observed spend].
5. **Review UI** [local web page over the store: accept/reject cards, merge/split clusters, label signals; writes to `labels`].
6. **Learned ranker** [after >= 200 labelled clusters: fit on components, report AUC against hand ranking, keep weights visible].
7. **Agentic deep dive** with the Claude Agent SDK [tool-using loop restricted to registered collectors; must log every query].
8. **Trend alerts** [weekly diff of cluster scores; flag new or rising clusters].
9. **Scheduler + Docker** [one container running `scripts/weekly.sh`; report delivered by email or Slack].
