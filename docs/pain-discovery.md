# paindisc — Pain Discovery Agent

Finds recurring, expensive, underserved business problems from permitted sources, and backs every claim with a quote that code has checked against the source. Output: a weekly Pain Report of ranked problem cards with links, dates, spend evidence and computed confidence. It finds problems, not products.

The full contract is in [SPEC.md](../SPEC.md). Commands below run from the repo root.

## Try it offline (no keys, no network)

```bash
uv sync
uv run paindisc demo          # synthetic fixtures, mock LLM
open reports/demo-pain-report.html
uv run pytest                 # 32 tests, offline
```

A copy of the demo output is in [sample-demo-report.md](sample-demo-report.md).

## Real runs

```bash
cp .env.example .env          # set ANTHROPIC_API_KEY; GITHUB_TOKEN and BRAVE_API_KEY are optional
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

Reports land in `reports/pain-report-YYYY-MM-DD.{md,html}`. `scripts/weekly.sh` is a cron-ready weekly run.

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
src/paindisc collectors/, planner, dedupe, verify, llm, mock, cluster, score, synth, report, pipeline, cli
tests/       offline tests + synthetic fixtures + gold set
```
