# Venture Engine: notes for Claude Code sessions and agent teams

The README describes the full seven-agent plan. What's implemented so far is the Pain Discovery Agent: package `paindisc` in `src/paindisc/`, documented in `docs/pain-discovery.md`.

Read SPEC.md first. It is the contract: if code and spec disagree, fix one of them in the same change.

## Commands

- `uv sync`: install
- `uv run pytest -q`: offline test suite (no keys, no network). It must pass before any task is marked complete.
- `uv run paindisc demo`: the whole pipeline on synthetic fixtures with the mock LLM
- `uv run paindisc --help`: the CLI

## Non-negotiables

- Network access goes only through `PoliteClient` (`http.py`) and `config/registry.yaml`. Collectors never call httpx directly. No logins, CAPTCHA solving, proxy rotation, user-agent spoofing, or third-party resellers that skip a platform's approval.
- New sources go into the registry with `allowed: false` and a reason. Only the user flips them, after reading the terms.
- Evidence contract (SPEC section 3): code verifies every quote in `verify.py`. Never weaken verification to make a test pass. Financial figures are observed or derived, never inferred.
- No solution, product or idea fields anywhere. This tool finds problems.
- Usernames are stored only as salted hashes (`hash_author`). Never store or build profiles of people.
- Every collector has a fixture test using `httpx.MockTransport`; no test may need network or API keys.
- Every LLM step must also work in mock mode (`mock.py`). Fake-API tests for the Claude path use `httpx2` (the Anthropic SDK's own httpx fork); see `tests/test_llm.py`.

## File ownership (agent teams)

One owner per file. These shared files belong to the team lead: `src/paindisc/{pipeline,cli,models,store,report}.py`, `src/paindisc/collectors/__init__.py`, `config/*.yaml`, `prompts/*`, `SPEC.md`, `README.md`.

Teammates create new files and message the lead with the exact change they need in a shared file. If a test fails in another teammate's files, message that teammate or the lead; don't edit their files.

## Adding a collector

1. New module in `src/paindisc/collectors/` that returns `Document`s and fetches with `self.client.get(url, source="<name>")`.
2. Lead adds the registry entry (domains, route, terms_url, rate limit, `allowed: false`) and registers the class in `collectors/__init__.py`.
3. Fixture test with `httpx.MockTransport` that checks parsing, author hashing and dates.
