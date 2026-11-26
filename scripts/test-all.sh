#!/usr/bin/env bash
# Everything CI runs, locally and offline: pytest, then frontend lint + unit tests + typecheck + build, then e2e.
#   scripts/test-all.sh
# Env: SKIP_FRONTEND=1, SKIP_E2E=1 to skip those stages.
set -euo pipefail
cd "$(dirname "$0")/.."

export LLM_MOCK=true
step() { printf '\n==> %s\n' "$*"; }
skip() { case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in 1|true|yes) return 0 ;; *) return 1 ;; esac; }

step "pytest"
uv run pytest -q

if skip "${SKIP_FRONTEND:-}"; then
  step "frontend: skipped (SKIP_FRONTEND)"
elif [ ! -f frontend/package.json ]; then
  step "frontend: skipped (no frontend/package.json)"
else
  if [ ! -d frontend/node_modules ]; then npm --prefix frontend ci --no-audit --no-fund; fi
  step "frontend: safety lint"
  npm --prefix frontend run lint
  step "frontend: unit tests"
  npm --prefix frontend test -- --run
  step "frontend: typecheck"
  npm --prefix frontend run typecheck
  step "frontend: build"
  npm --prefix frontend run build
fi

if skip "${SKIP_E2E:-}"; then
  step "e2e: skipped (SKIP_E2E)"
elif [ ! -f e2e/package.json ]; then
  step "e2e: skipped (no e2e/package.json)"
else
  step "e2e"
  scripts/e2e.sh
fi

step "all green"
